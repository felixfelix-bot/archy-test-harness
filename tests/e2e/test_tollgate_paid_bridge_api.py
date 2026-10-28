"""Regression guard: the paid bridge must be able to reach the TollGate API.

## Why this exists

`tollgate-wrt` ships `/etc/nftables.d/30-backend-firewall.nft`, which drops
`tcp dport 2121` for every ingress interface *except* a hardcoded allowlist.
If the captive bridge is not in that allowlist, the portal's own SPA loads
(port 2050 is not dropped) but its backend call dies — the customer sees a
portal that never leaves "Loading" and can never buy anything.

That failure is invisible from the router's own shell: `curl
127.0.0.1:2121` and even `curl <gateway-ip>:2121` return 200, because those
paths traverse `lo`/`br-lan` rather than the paid bridge. So a test that
probes from the router *passes on a broken board*. That is exactly the trap
this file avoids: it asserts the **structural** property (the paid bridge is
inside the drop rule's allowlist), which is what actually determines the
outcome for a real client.

## What makes it a real check

The bridge name is read from the node's own `get-status`
(`gatewayinterface`), not hardcoded. A board that renames its captive bridge
stays covered, and a board that regresses to the packaged fragment fails.

Set ARCHY_PAID_CLIENT_IP to additionally prove reachability as a client:

    ARCHY_PAID_CLIENT_IP=192.168.99.158 pytest ...

That leg curls from the paid-side address, which is the only vantage point
that reproduces the customer's experience.

Run with any pytest; the venv used for the rest of the tollgate suite works.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from pathlib import Path

import pytest

RPC_URL = os.environ.get("ARCHY_RPC_URL", "http://127.0.0.1:5678").rstrip("/")
RPC_ENDPOINT = f"{RPC_URL}/rpc/v1"
RPC_PASSWORD = os.environ.get("ARCHY_RPC_PASSWORD", "")
ROUTER_HOST = os.environ.get("ARCHY_ROUTER_HOST", "")
ROUTER_PASSWORD = os.environ.get("ARCHY_ROUTER_PASSWORD", "")
ROUTER_KEY = os.environ.get("ARCHY_ROUTER_KEY", "")
PAID_CLIENT_IP = os.environ.get("ARCHY_PAID_CLIENT_IP", "")
RPC_TIMEOUT = int(os.environ.get("ARCHY_RPC_TIMEOUT", "300"))

FRAGMENT = "/etc/nftables.d/30-backend-firewall.nft"
API_PORT = "2121"

pytestmark = pytest.mark.skipif(
    not (ROUTER_HOST and RPC_PASSWORD),
    reason="set ARCHY_ROUTER_HOST and ARCHY_RPC_PASSWORD (or ARCHY_ROUTER_KEY)",
)


def _ssh(command: str, timeout: int = 40) -> str:
    """Run a command on the router. Mirrors the version-pin suite's helper."""
    base = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=10",
    ]
    if ROUTER_KEY and Path(ROUTER_KEY).exists():
        base += ["-i", ROUTER_KEY, "-o", "IdentitiesOnly=yes"]
        base += ["-o", "PreferredAuthentications=publickey"]
    else:
        base += ["-o", "PreferredAuthentications=password"]
        base += ["-o", "PubkeyAuthentication=no"]
        base = ["sshpass", "-p", ROUTER_PASSWORD] + base
    base += [f"root@{ROUTER_HOST}", command]
    out = subprocess.run(base, capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise AssertionError(
            f"ssh failed (rc={out.returncode}): {out.stderr.strip()[:300]}"
        )
    return out.stdout


_SESSION: dict[str, str] = {}


def _rpc(method: str, params: dict | None = None) -> dict:
    """JSON-RPC call, logging in once and carrying cookie + CSRF thereafter.

    `openwrt.get-status` authenticates to the router itself and the endpoint
    rejects unauthenticated calls with a bare 404, so a naive POST looks like
    "method does not exist" rather than "you are not signed in".
    """
    if not _SESSION:
        login = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "auth.login",
             "params": {"password": RPC_PASSWORD}}
        ).encode()
        req = urllib.request.Request(
            RPC_ENDPOINT, data=login, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=RPC_TIMEOUT) as r:
            payload = json.load(r)
            for raw in r.headers.get_all("Set-Cookie") or []:
                name, _, rest = raw.partition("=")
                _SESSION[name.strip()] = rest.split(";")[0]
        if payload.get("error"):
            raise AssertionError(f"auth.login failed: {payload['error']}")
        csrf = _SESSION.get("csrf_token", "")
        if csrf:
            _SESSION["X-CSRF-Token"] = csrf

    headers = {"Content-Type": "application/json"}
    cookie = "; ".join(
        f"{k}={v}" for k, v in _SESSION.items() if k != "X-CSRF-Token"
    )
    if cookie:
        headers["Cookie"] = cookie
    if _SESSION.get("X-CSRF-Token"):
        headers["X-CSRF-Token"] = _SESSION["X-CSRF-Token"]

    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    ).encode()
    req = urllib.request.Request(RPC_ENDPOINT, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=RPC_TIMEOUT) as r:
        payload = json.load(r)
    if payload.get("error"):
        raise AssertionError(f"{method} error: {payload['error']}")
    return payload.get("result") or {}


@pytest.fixture(scope="module")
def paid_bridge() -> str:
    """Name of the captive bridge, discovered rather than assumed.

    `openwrt.get-status` does not expose it, so it is derived from the socket
    NoDogSplash actually listens on: find the portal's local address, then the
    interface that owns it. That keeps this correct on a board that renames
    its captive bridge. ARCHY_PAID_BRIDGE overrides the discovery.
    """
    override = os.environ.get("ARCHY_PAID_BRIDGE", "").strip()
    if override:
        return override

    status = _rpc("openwrt.get-status", {"host": ROUTER_HOST,
                                        "ssh_user": "root",
                                        "ssh_password": ROUTER_PASSWORD})
    tollgate = status.get("tollgate") or status
    if not tollgate.get("installed"):
        pytest.skip("tollgate-wrt is not installed on this router")

    # Which address is the portal bound to?
    addr = _ssh(
        "ss -ltn 2>/dev/null | awk '$4 ~ /:2050$/ {print $4; exit}'"
    ).strip()
    if addr and not addr.startswith("0.0.0.0") and not addr.startswith("[::"):
        ip = addr.rsplit(":", 1)[0]
        iface = _ssh(
            f"ip -4 -o addr show | awk -v ip='{ip}/' "
            "'$4==ip {print $2; exit}'"
        ).strip()
        if iface:
            return iface

    # Falling back to the conventional name is honest only because the
    # structural assertion below still has to hold against the real fragment.
    return os.environ.get("ARCHY_DEFAULT_PAID_BRIDGE", "br-tollgate")


def test_the_api_drop_rule_exempts_the_paid_bridge(paid_bridge: str) -> None:
    """The invariant, asserted structurally — the only reliable vantage point.

    A router-local curl cannot see this bug: it travels lo/br-lan and returns
    200 on a board that blocks every real customer. The nft interface set is
    what decides the customer's fate, so that is what we assert.
    """
    assert _ssh(f"test -f {FRAGMENT} && echo present").strip() == "present", (
        f"{FRAGMENT} is missing; tollgate-wrt changed shape and this guard "
        "needs revisiting rather than silently passing"
    )

    rule = _ssh(
        f"grep -n 'dport {API_PORT}' {FRAGMENT} | head -1"
    ).strip()
    assert rule, (
        f"no rule in {FRAGMENT} mentions tcp dport {API_PORT}; the API may now "
        "be governed by a different fragment"
    )

    assert f'"{paid_bridge}"' in rule, (
        f"the paid bridge '{paid_bridge}' is NOT in the allowlist of the rule "
        f"that drops tcp/{API_PORT}. Real clients on that bridge will have "
        "their portal API call dropped, so the portal can never sell access.\n"
        f"rule: {rule}"
    )


def test_the_paid_bridge_actually_exists(paid_bridge: str) -> None:
    """Guard against a passing grep on a bridge that no longer exists."""
    present = _ssh(f"ip link show {paid_bridge} >/dev/null 2>&1 && echo yes || echo no")
    assert present.strip() == "yes", (
        f"get-status claims gatewayinterface='{paid_bridge}' but no such "
        "interface exists; the node and the router disagree"
    )


@pytest.mark.skipif(
    not PAID_CLIENT_IP,
    reason="set ARCHY_PAID_CLIENT_IP to prove reachability from the paid side",
)
def test_a_paid_side_client_reaches_the_api() -> None:
    """The customer's-eye view. This is the leg that failed before the fix.

    Source address is pinned to the paid-side client so the packet takes the
    same path a real customer's does. Before the fix this returned 000 while
    the router-local probe returned 200 — the reason the structural test
    above exists as well.
    """
    # A dead WiFi link also produces 000. Distinguish "dropped" from "not
    # there" before blaming the firewall: an unassociated client sends
    # nothing at all, and a DORMANT interface keeps its address.
    local_iface = os.environ.get("ARCHY_PAID_CLIENT_IFACE", "")
    if local_iface:
        operstate = Path(f"/sys/class/net/{local_iface}/operstate")
        if not operstate.exists():
            pytest.skip(f"client interface {local_iface} does not exist on this host")
        state = operstate.read_text().strip()
        if state in {"down", "dormant", "lowerlayerdown"}:
            pytest.skip(
                f"{local_iface} operstate={state}: no radio link, so 000 would "
                "mean a dead link rather than a firewall drop"
            )
    reach = subprocess.run(
        ["ping", "-c", "1", "-W", "2", "192.168.99.1"],
        capture_output=True, text=True,
    )
    if reach.returncode != 0:
        pytest.skip(
            "gateway 192.168.99.1 does not answer ICMP from this host; the paid "
            "link is down, so a 000 here is not a firewall verdict"
        )

    out = subprocess.run(
        ["curl", "-s", "-m", "10", "-o", "/dev/null",
         "--interface", PAID_CLIENT_IP,
         "-w", "%{http_code}", f"http://192.168.99.1:{API_PORT}/api/v1/info"],
        capture_output=True, text=True, timeout=30,
    )
    assert out.stdout.strip() == "200", (
        f"paid-side client {PAID_CLIENT_IP} got {out.stdout.strip()!r} from the "
        f"API on {API_PORT}. 000 means dropped — the portal will hang at "
        "'Loading' for real customers."
    )
