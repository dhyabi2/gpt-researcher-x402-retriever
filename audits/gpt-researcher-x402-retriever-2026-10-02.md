# gpt-researcher-x402-retriever — audit 2026-10-02

Python 3.11.15. `pip install -e ".[test]"` then `pytest -q` → **40 passed, 2 skipped** before any
change (the 2 skips are the live paid calls, which need a funded wallet and spend the seller's
trial). CI runs the same on 3.10 and 3.12.

Read through one question: if an agent pays for a search with this today, can it lose the money?

## Checked

- **Every amount is an integer.** `xno_to_raw` goes through `Decimal(str(value))`, refuses a
  non-finite or negative value and refuses more precision than 1 raw (`terms.py:44-55`); the price
  cap is compared as `int` (`retriever.py:138`); `PaymentOffer.amount_raw` is `int` and
  `amount_xno` is a `Decimal` used only for messages. No `float` reaches an amount.
- **The guards that run before any payer is called** (`retriever.py:137-143`): the price cap, the
  optional `X402_PAY_TO` pin, and the refusal to pay over a non-HTTPS endpoint. All three are
  reached from `_fetch` before `resolve_payer`, and `parse_challenge` has already refused any offer
  that is not `exact` / `nano:mainnet` / `XNO`.
- **One payment per call.** `_fetch` calls `payer.pay` once, requires a 64-hex block hash back
  (`retriever.py:125`), retries exactly once with `X-PAYMENT`, and on a non-200 after paying raises
  with the block hash in the message rather than paying again (`retriever.py:128-134`).
- **The retry sends the same request.** `self._get` rebuilds `params` from `_query_text()`, which is
  deterministic for a given instance; `test_402_with_a_payer_pays_the_exact_terms_once_and_retries_with_the_hash`
  asserts `session.calls[1]["params"] == session.calls[0]["params"]`.
- **`payTo` and `amount` are read only from the challenge**, never from a caller, and the offer the
  payer receives is frozen (`@dataclass(frozen=True)`).
- No secret, key or seed in the tree or in any commit; no `exec`, `subprocess` or shell anywhere.

## Found and fixed

**1. `payTo` was checked for shape but not for its checksum** (`terms.py:19`, used at `terms.py:85`).
A Nano address ends in a 5-byte blake2b digest of its own public key precisely so that a mistyped
character is caught. `NANO_ADDRESS` matches the alphabet and the length only, so a `payTo` with one
character changed inside the key passed, and `Feeless402Payer.pay` sent the amount to an account
nobody holds the key to — irreversibly, and without the call being served either. `valid_nano_address`
now recomputes the digest with `hashlib` and `parse_challenge` uses it. Merged as #3, test
`test_pay_to_with_a_broken_checksum_is_refused_before_the_payer_is_asked`.

**2. An `isdigit()` amount could leave a bare `ValueError`** (`terms.py:82`). `"²".isdigit()` is
`True` and `int("²")` raises, so the guard itself raised `ValueError: invalid literal for int()` out
of `parse_challenge`, whose documented failure is `TermsError` — and since `TermsError` subclasses
`ValueError`, `except TermsError` does not catch it. `"٣"` was accepted and silently read as `3`.
Now matched against `^[0-9]+$`. Inside the retriever `search()` catches everything, so this was a
contract break for a direct caller rather than a crash for GPT Researcher.

## Not changed, worth knowing

- **`Feeless402Payer` — the only code here that moves money — is exercised by nothing.** `feeless402`
  is not installed in this sandbox and not in CI, so `Wallet(...).load()` and `wallet.send(...)` have
  never run. A fake-wallet test would at least pin the argument order and that `amount_raw` is passed
  as an integer; worth adding, but it belongs in a change that can be checked against the real
  package.
- `parse_challenge` raises on the first Nano-shaped offer with a malformed amount instead of trying
  the next `accepts` entry (`terms.py:81-86`). That refuses rather than overpays, so it is the safe
  direction. Unchanged, as in the previous audit.
- `payer_from_env` runs on every `_fetch`, so the `X402_PAYER` route rebuilds a payer (and for
  `Feeless402Payer` a `Wallet` and an `RPC`) per search. Wasteful, not wrong; `set_default_payer` is
  the documented way round it.
- A seller offering the `xrb_` form of a pinned `X402_PAY_TO` is refused by the string comparison at
  `retriever.py:140` even though it is the same account. Refusing is the safe direction.

## Could not verify

- No live call and no payment: no funded wallet, and `X402_LIVE` was not set.
- `https://search.paypercall.dev/...` (the default endpoint) could not be reached from this
  container — this environment's proxy answers `CONNECT tunnel failed, response 403` — which is the
  network policy, not evidence the endpoint is down.
- **PR #1 is still open and unreviewed** (`RETRIEVER=paypercall silently selected Tavily`), opened
  2026-09-30. It is the one finding on this repository that changes behaviour rather than refusing
  more, which is why two audits have now left it for the owner. Its base is behind this merge; it
  does not touch `terms.py`, so no conflict is expected.
