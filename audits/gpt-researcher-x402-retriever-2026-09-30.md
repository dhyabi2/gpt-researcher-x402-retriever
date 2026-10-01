# gpt-researcher-x402-retriever — audit 2026-09-30

Scope: can an agent pay for a search in XNO with this today, without being hurt.
Read end to end: `terms.py`, `payers.py`, `retriever.py`, `__init__.py`, the test suite, `README.md`.

## Checked

- `pytest`: **40 passed, 2 skipped** before and after this change (the 2 skips are the live tests,
  which need `X402_LIVE=1`).
- Amount handling, the whole way through. No float touches an amount anywhere: `xno_to_raw` parses
  through `Decimal(str(value))` and refuses anything finer than 1 raw (`terms.py:44-55`);
  `PaymentOffer.amount_raw` is an `int`; `amount_xno` is a `Decimal`. The 402's `amount` must be a
  positive integer raw *string* (`terms.py:82`).
- The refusal path before money moves (`retriever.py:137-143`): scheme/network/asset must be
  `exact`/`nano:mainnet`/`XNO`, `payTo` must match the Nano address grammar, the price must be at
  or under `X402_MAX_XNO`, `payTo` must equal `X402_PAY_TO` when pinned, and the endpoint must be
  HTTPS. All five are checked before a payer is ever asked.
- No payer means no payment: `resolve_payer` returns `None`, the price is logged and `search()`
  returns `[]` (`retriever.py:116-122`).
- The paid retry pays at most once: a non-200 on the retry raises and keeps the block hash on
  `last_payment` rather than paying again (`retriever.py:128-134`).
- The payer's return value is checked to be a 64-hex block hash before it is sent as `X-PAYMENT`
  (`retriever.py:125-126`).
- The `nano-wallet-xno` payer recipe in the README, against that repository's real API:
  `payments.send(...)` does take `amount_xno` as a string, `wallet.raw_to_xno` returns an exact
  decimal string (no float), and the result does carry `block_hash`. All accurate.

## Found and fixed (this PR)

**The README's payer recipe stopped paying after the first search.** The recipe built

```python
idempotency_key=f"x402:{offer.resource}:{offer.amount_raw}"[:64]
```

`offer.resource` is the endpoint URL and `amount_raw` is the price, so that string is identical on
every search of the same endpoint at the same price. `nano-wallet-xno` treats a repeated
`idempotency_key` as the same payment and returns the first block hash again with
`replayed=True`, sending nothing (`payments.py:250-260`). With a payer installed through
`set_default_payer` — one long-lived instance, which the same README documents — the second search
hands the seller an already-spent block hash, the seller refuses it, and `_fetch` raises; every
paid search after the first returns `[]`.

Reproduced against `nano-wallet-xno`'s own `FakeNode`, three searches through one payer:

```
--- README recipe: key = resource + amount ---
  search 1: block 72DDB6E342D2BC0F  replayed=False
  search 2: block 72DDB6E342D2BC0F  replayed=True
  search 3: block 72DDB6E342D2BC0F  replayed=True
  send blocks actually published: 1
--- one key per call ---
  search 1: block 1228E01AD02EF304  replayed=False
  search 2: block C501417FE0C16CF9  replayed=False
  search 3: block CC5EA5C0E36BCCD3  replayed=False
  send blocks actually published: 3
```

The `[:64]` truncation compounded it: the key was cut mid-amount at
`x402:https://search.paypercall.dev/api/v1/web-search:10000000000`, 11 of the price's 27 digits, so
two different prices on the same endpoint collide as well.

Fixed: the recipe now uses one key per call, with the reason and the retry caveat spelled out.
This is documentation only — no code changed, and the 40 tests are untouched.

## Observations — no change made

- `payer_from_env` runs on every `_fetch` (`retriever.py:116` -> `payers.py:55-60`), so the
  `X402_PAYER` route builds a new payer, and for `Feeless402Payer` a new `Wallet` and `RPC`, on
  every search. Wasteful rather than wrong, and it also means the `sent` record in the recipe above
  is empty each call on that route, so it provides no protection there at all. Left alone: the
  fix is for the user to install one payer via `set_default_payer`, which the README already
  documents and which the corrected recipe now works with.
- `parse_challenge` raises on the first Nano-shaped offer with a malformed amount instead of trying
  the next entry in `accepts` (`terms.py:81-86`). That refuses rather than overpays, so it is the
  safe direction; not changed.

## Could not verify

- No live call and no payment: no funded wallet in this run, and the live tests stay skipped.
- `https://search.paypercall.dev/...` (the default endpoint) and `https://docs.gptr.dev/...` could
  not be reached from this container — the proxy answers `CONNECT tunnel failed, response 403` for
  both, which is this environment's network policy and **not** evidence that either is down.
  `https://extract.paypercall.dev/.well-known/x402`, both GitHub links and the PyPI link all
  answered 200, so the README's links are otherwise good.
