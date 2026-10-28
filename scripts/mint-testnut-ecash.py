"""Mint test ecash from testnut's FakeWallet - no real sats involved.

WHY THIS MATTERS FOR THE PAYMENT TEST
-------------------------------------
testnut.cashu.space reports in its own /v1/info description_long:

    "This mint uses a FakeWallet. All your Lightning invoices will always be
     marked paid ... no real funds are moved."

So a tollgate payment test needs NO funding. Read description_long - not just
`description`, which only says "Mint for testing Cashu wallets" - before
declaring that a payment test is blocked on sats. Getting this wrong once cost
a whole round of reporting a blocker that did not exist.

CAVEAT: a mint with a FakeWallet is a free-money hole for a PRODUCTION rail.
Fine here (a lab router selling test ecash), never for a service that pays out.
"""
"""Mint test ecash from testnut's FakeWallet — no real sats involved.

testnut's /v1/info states: "This mint uses a FakeWallet. All your Lightning
invoices will always be marked paid." So the mint quote settles without a
payment, which is exactly what a payment-path test needs.

Run: python scripts/mint-testnut-ecash.py <amount> <db-path>
Requires: pip install cashu "marshmallow<4"  (environs needs marshmallow<4)
Prints the token on stdout; diagnostics on stderr so the token stays clean.
"""

import asyncio
import inspect
import sys

from cashu.wallet.wallet import Wallet


async def main() -> None:
    amount = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    db = sys.argv[2] if len(sys.argv) > 2 else "/tmp/cashu-wallet.sqlite3"

    wallet = await Wallet.with_db(url="https://testnut.cashu.space", db=db)
    await wallet.load_mint()
    print(f"mint: {wallet.url}", file=sys.stderr)

    quote = await wallet.request_mint(amount)
    print(f"quote: {quote.quote} paid={getattr(quote, 'paid', None)}", file=sys.stderr)

    # FakeWallet marks it paid immediately; poll anyway so this works against a
    # real mint too.
    for attempt in range(30):
        state = await wallet.get_mint_quote(quote.quote)
        if getattr(state, "paid", False):
            print(f"settled after {attempt} poll(s)", file=sys.stderr)
            break
        await asyncio.sleep(1)
    else:
        raise SystemExit("quote never settled")

    # Signature moved between cashu-py releases; bind by name rather than guess.
    sig = inspect.signature(wallet.mint)
    kwargs = {}
    for name in sig.parameters:
        if name in {"amount", "sats"}:
            kwargs[name] = amount
        elif name in {"quote_id", "quote"}:
            kwargs[name] = quote.quote
    proofs = await wallet.mint(**kwargs)
    if proofs is None:
        proofs = wallet.proofs
    print(f"minted proofs: {len(proofs)}", file=sys.stderr)
    bal = wallet.balance
    if callable(bal):
        bal = bal()
    if inspect.isawaitable(bal):
        bal = await bal
    print(f"balance: {bal}", file=sys.stderr)

    token = wallet.serialize_proofs(proofs)
    if inspect.isawaitable(token):
        token = await token
    print(token)


asyncio.run(main())
