# gpt-researcher-x402-retriever — audit 2026-10-12

Lens: can an agent pay in XNO through this retriever today without being hurt? This package
spends real XNO on a stranger's 402, so the audit is spent on the decision to pay.

Starting point: `main` at `27163fa`, **74 passed, 3 skipped** before any change.

## What I checked

- Installed from the tree and ran `pytest -q` and `python -m compileall src tests`.
- The spend path end to end: `_fetch` → `parse_challenge` → `_check_offer` → `resolve_payer`
  → `payer.pay(offer)` → the paid retry, and `terms.py`'s address, amount and cap handling
  underneath it.
- Amount handling. No float reaches an amount: `RAW_AMOUNT` is `^[0-9]+\Z` (not
  `str.isdigit()`, which admits `٣`), `amount_raw` is a Python `int`, and `xno_to_raw`
  runs in its own 60-digit `localcontext` with `Inexact` trapped, so the cap is exact rather
  than rounded at the process-global 28 digits. Re-read the two measured cap cases in its
  docstring; both still convert exactly.
- `valid_nano_address` checks the blake2b checksum, not just the shape — the only thing that
  catches a single mistyped character in an address a send cannot be taken back from.
- Every pattern in `terms.py` ends in `\Z`, not `$`, so a trailing newline cannot pass as an
  address or a block hash. Still true of all three.
- The refusal order in `_fetch`: the offer is fully checked **before** `resolve_payer` is
  called, so no payer is even constructed for terms that will be refused.
- `payer.pay`'s return is re-validated as 64 hex before it goes anywhere near a header.

## Found and fixed — a 402 reached *through* a plain-HTTP hop was paid

`_check_offer` refuses a 402 that did not arrive over HTTPS (added 10-04, because "the Nano
address came from an origin anyone on the path could have rewritten"). It reads
`response.url`. That is the **final** hop only: the hops a redirect passed *through* live
solely in `response.history`.

Measured here, `requests` on a three-hop chain:

```
requested : http://127.0.0.1:35859/start
r.url     : http://127.0.0.1:38505/final
r.history : [(302, '…/start'), (302, '…/mid')]
```

So `https -> http -> https` satisfies both existing checks. Driven through `PayPerCallSearch`
with a real `requests.Session` against a local TLS → plain-HTTP → TLS chain:

```
endpoint seen by guard : https://localhost:40955/api/v1/web-search
challenge_url seen     : https://localhost:42013/api/v1/web-search
PAID?                  : True
amount_raw             : 100000000000000000000000000      (0.0001 XNO)
pay_to                 : nano_1yo6c1t64ahfjdw1dxizmbb...
```

0.0001 XNO sent irreversibly to an address chosen at the plain-HTTP hop, with both guard legs
reading `https`. `X402_PAY_TO` would catch it, but it is optional and unset by default — the
same reason the 10-04 guard could not rely on it.

**Fix:** one more refusal in `_check_offer` — every URL in `response.history` must also be
`https`, else `TermsError`. It narrows what is accepted and nothing else: no amount,
destination, rounding, address, cap or key path is touched, and no existing refusal is
removed or widened. A redirect chain that stays on HTTPS throughout is still paid, which the
control test pins.

**Failing-then-passing:** with the two new tests and the six new lines deleted (the guard
alone, nothing else), exactly the refusal test fails —
`AssertionError: nothing may be sent for a 402 reached through a plain-HTTP hop`, with the
offer the payer was handed printed beside it — and the all-HTTPS control passes. With the
guard: **76 passed, 3 skipped**; `compileall` clean. Against the real chain above, the fix
answers `the 402 was reached through a http redirect (http://127.0.0.1:36065/...) --
refusing to pay an address chosen on a hop anyone on the path could rewrite`, and
`PAID? False`.

The README's step 3 said the endpoint and the URL the 402 came from are both checked; it now
says every hop is, and why `response.url` alone is not enough.

## Checked and left alone

- **The paid retry is not hop-checked.** `_get({"X-PAYMENT": ...})` could itself be
  redirected through plain HTTP, leaking the block hash to the path. The money has already
  moved by then, so refusing there would throw away a paid call rather than prevent a loss,
  and the block hash is public ledger data. Noted, not changed — widening the fix past the
  pre-payment decision is not a correction.
- `X402_PAY_TO` is compared to `offer.pay_to` with `!=` and is not normalised, so pinning an
  `xrb_` form against a seller quoting the equivalent `nano_` form refuses a legitimate
  payment. It fails safe (refuses, never overpays), and treating an exact pin as exact is
  defensible, so it is recorded rather than changed.

## Could not verify

- No live 402 was fetched. `https://search.paypercall.dev` was not called from this
  container; `tests/test_live.py` remains the opt-in path for that.
- The GPT Researcher resolution tests still need a `gpt_researcher` checkout, which is not
  installed here; they are among the 3 skipped, not failing.
