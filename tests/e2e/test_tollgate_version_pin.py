"""End-to-end proof that Archipelago provisions the PINNED tollgate-wrt version.

This is the reusable gate for "the TollGate version bump actually landed on a
router". It drives the same JSON-RPC the product UI drives
(`openwrt.provision-tollgate`), then reads the version back from two independent
places and requires them to agree:

  1. the node's own view  — `openwrt.get-status`
  2. the router itself    — `opkg list-installed` / `apk list --installed`

A version string in one place proves nothing: the failure this guards against is
a router whose files are new while its recorded version is old, or the node
reporting success for a router it never touched. Two sources that must agree is
the point.

Venue
-----
Requires a running Archipelago node and a reachable OpenWrt router. Both are
configured, never assumed:

  ARCHY_RPC_URL        e.g. http://10.99.99.2:5678
  ARCHY_RPC_PASSWORD   node admin password
  ARCHY_ROUTER_HOST    e.g. 10.99.99.1
  ARCHY_ROUTER_PASSWORD

Optional:
  ARCHY_TOLLGATE_VERSION   expected version (default 0.6.0)
  ARCHY_EVIDENCE_DIR       where the JSON evidence lands

Run:
  ./scripts/run-profile.py --profile tollgate
or
  pytest tests/e2e/test_tollgate_version_pin.py -v

Skips (does not silently pass) when a venue variable is missing, so a green run
means "verified", not "did not run".
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

RPC_URL = os.environ.get("ARCHY_RPC_URL", "").rstrip("/")
RPC_PASSWORD = os.environ.get("ARCHY_RPC_PASSWORD", "")
ROUTER_HOST = os.environ.get("ARCHY_ROUTER_HOST", "")
ROUTER_PASSWORD = os.environ.get("ARCHY_ROUTER_PASSWORD", "")
EXPECTED = os.environ.get("ARCHY_TOLLGATE_VERSION", "0.6.0")
EVIDENCE_DIR = Path(os.environ.get("ARCHY_EVIDENCE_DIR", "results/tollgate"))
# Provision is download + sha256 + install + read-back on the ROUTER's CPU.
# A mipsel_24kc board exceeded 60s, and the client gave up while the router
# went on to finish successfully - a test-side timeout is not a product
# failure, so this is generous and overridable.
RPC_TIMEOUT = int(os.environ.get("ARCHY_RPC_TIMEOUT", "300"))

REQUIRED = {
    "ARCHY_RPC_URL": RPC_URL,
    "ARCHY_RPC_PASSWORD": RPC_PASSWORD,
    "ARCHY_ROUTER_HOST": ROUTER_HOST,
}
MISSING = sorted(k for k, v in REQUIRED.items() if not v)
# Router auth may be a password or a key — require one, not both.
if not (ROUTER_PASSWORD or os.environ.get("ARCHY_ROUTER_KEY")):
    MISSING.append("ARCHY_ROUTER_PASSWORD|ARCHY_ROUTER_KEY")
pytestmark = pytest.mark.skipif(
    bool(MISSING),
    reason=f"tollgate venue unavailable, missing: {MISSING}",
)


def _evidence(name: str, payload) -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    (EVIDENCE_DIR / name).write_text(json.dumps(payload, indent=2, sort_keys=True))


class Node:
    """Minimal JSON-RPC client for an Archipelago node."""

    def __init__(self, url: str, password: str) -> None:
        self.url = url
        self.password = password
        self.cookies: dict[str, str] = {}
        self.csrf = ""

    def _post(self, method: str, params: dict) -> dict:
        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        ).encode()
        req = urllib.request.Request(
            f"{self.url}/rpc/v1", data=body, headers={"Content-Type": "application/json"}
        )
        if self.cookies:
            req.add_header(
                "Cookie", "; ".join(f"{k}={v}" for k, v in self.cookies.items())
            )
        if self.csrf:
            req.add_header("X-CSRF-Token", self.csrf)
        try:
            with urllib.request.urlopen(req, timeout=RPC_TIMEOUT) as r:
                for header in r.headers.get_all("Set-Cookie") or []:
                    k, _, v = header.partition("=")
                    self.cookies[k.strip()] = v.split(";")[0]
                return json.loads(r.read())
        except urllib.error.HTTPError as e:  # surface the body, not just "503"
            raise AssertionError(f"{method} HTTP {e.code}: {e.read()[:400]!r}") from e

    def login(self) -> None:
        r = self._post("auth.login", {"password": self.password})
        assert r.get("error") is None, f"login failed: {r}"
        self.csrf = self.cookies.get("csrf_token", "")

    def call(self, method: str, params: dict | None = None) -> dict:
        r = self._post(method, params or {})
        assert r.get("error") is None, f"{method} returned an error: {r.get('error')}"
        return r["result"]


ROUTER_KEY = os.environ.get("ARCHY_ROUTER_KEY", "")


def status_params() -> dict:
    """get-status authenticates to the router itself, so it needs the creds."""
    return {
        "host": ROUTER_HOST,
        "ssh_user": "root",
        "ssh_password": ROUTER_PASSWORD,
    }


def router_ssh(command: str) -> str:
    """Run a command on the router over SSH.

    Key auth when ARCHY_ROUTER_KEY is set (the physical lab routers), otherwise
    password auth. Both are real venues; neither is assumed.
    """
    if ROUTER_KEY:
        argv = ["ssh", "-i", ROUTER_KEY, "-o", "IdentitiesOnly=yes"]
    else:
        argv = ["sshpass", "-p", ROUTER_PASSWORD, "ssh"]
    proc = subprocess.run(
        argv
        + [
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "BatchMode=yes" if ROUTER_KEY else "BatchMode=no",
            "-o", "ConnectTimeout=15",
            f"root@{ROUTER_HOST}", command,
        ],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"router command failed ({proc.returncode}): {proc.stderr[:300]}"
    return proc.stdout


@pytest.fixture(scope="module")
def node() -> Node:
    n = Node(RPC_URL, RPC_PASSWORD)
    n.login()
    return n


def test_provision_pins_the_release_version(node: Node) -> None:
    """Provision through the real RPC and require a clean result.

    `provision-tollgate` used to return `{"error": ...}` AFTER a successful
    install whenever the router had no radio to reload, which made a working
    provision indistinguishable from a broken one. A caller must get
    `ok: true` with `error: null` here.
    """
    result = node.call(
        "openwrt.provision-tollgate",
        {
            "host": ROUTER_HOST,
            "ssh_user": "root",
            "ssh_password": ROUTER_PASSWORD,
            "price_sats": 10,
            "step_size_ms": 60000,
            "min_steps": 1,
            "mint_url": os.environ.get("ARCHY_MINT_URL", ""),
        },
    )
    _evidence("provision-result.json", result)
    assert result.get("ok") is True, f"provision did not report ok: {result}"
    assert result.get("host") == ROUTER_HOST


def test_node_reports_the_pinned_version(node: Node) -> None:
    """The node's own view must say installed, version-known, and up to date."""
    status = node.call("openwrt.get-status", status_params())
    _evidence("get-status.json", status)
    # get-status returns {host, hostname, release, tollgate:{...}} — the
    # TollGate verdict lives under "tollgate", not at the top level.
    status = status.get("tollgate") or {}
    assert status, "get-status returned no 'tollgate' block"

    assert status.get("installed") is True, f"not installed: {status}"
    assert status.get("version_known") is True, (
        f"node cannot name the installed version, so it cannot have verified it: {status}"
    )
    assert status.get("version") == f"{EXPECTED}-r1" or status.get("version") == EXPECTED, (
        f"expected {EXPECTED}, node reports {status.get('version')!r}"
    )
    assert status.get("up_to_date") is True, f"node says not up to date: {status}"


def test_router_agrees_with_the_node(node: Node) -> None:
    """Read the version from the ROUTER, independently of the node's claim.

    This is the check a success flag cannot fake. If the node and the router
    disagree, the node is reporting a state the device is not in.
    """
    status = (node.call("openwrt.get-status", status_params()) or {}).get("tollgate") or {}
    remote_version = ""

    opkg = router_ssh("opkg list-installed 2>/dev/null | grep '^tollgate-wrt ' || true")
    apk = router_ssh("apk list --installed 2>/dev/null | grep -i tollgate-wrt || true")
    _evidence("router-listing.json", {"opkg": opkg.strip(), "apk": apk.strip()})
    listing = opkg.strip() or apk.strip()

    assert listing, (
        "router does not list tollgate-wrt as installed — the node claims it "
        f"is ({status.get('version')!r}), so the two disagree"
    )

    m = re.search(r"(\d+\.\d+\.\d+(?:_rc\d+)?(?:-r\d+)?)", listing)
    assert m, f"could not parse a version out of the router listing: {listing!r}"
    remote_version = m.group(1)

    node_version = str(status.get("version", ""))
    assert remote_version.startswith(EXPECTED), (
        f"router has {remote_version}, expected the {EXPECTED} pin"
    )
    assert node_version.startswith(EXPECTED), (
        f"node reports {node_version}, expected the {EXPECTED} pin"
    )


def test_installed_binary_is_present_and_not_a_stub(node: Node) -> None:
    """A version string with no binary behind it is the failure this guards.

    The removed extraction path produced routers whose listing said one thing
    and whose `/usr/bin/tollgate-wrt` was missing or stale.
    """
    size_out = router_ssh("wc -c < /usr/bin/tollgate-wrt 2>/dev/null || echo 0").strip()
    size = int(size_out or 0)
    _evidence("binary-size.json", {"bytes": size})
    assert size > 1_000_000, (
        f"/usr/bin/tollgate-wrt is {size} bytes — a missing or stub binary, "
        "not the shipped package"
    )
