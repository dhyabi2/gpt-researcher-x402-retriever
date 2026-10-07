# gpt-researcher-x402-retriever - audit 2026-10-07

Previous audit 2026-10-04. Read through one question: can an agent pay in XNO with this, today,
without being hurt?

HEAD audited: `origin/main` at the time of the branch (see the pull request). Python 3.11.

## Checked

- **Install and suite, as CI runs them.** `pip install -e ".[test]"`, `pip install -e
  ".[feeless402]"`, `pytest -q`: **56 passed, 2 skipped** on `main`; **73 passed, 2 skipped** after
  this branch. The two skips are the ones CI also skips - GPT Researcher is not installed and
  `X402_LIVE` is unset - and both are deliberate, with their reasons printed.
  *Environment note, not a repository defect:* in this container `pip` is Python 3.13's while
  `python3` is 3.11, so a plain `pip install -e .` installs where `python3` cannot import it.
  `python3 -m pip` is the working form here. CI uses a single interpreter and is unaffected.
- **The seller's price, which is the amount that actually gets paid.** `parse_challenge` takes
  `accept["amount"]`, requires `^[0-9]+$` (not `str.isdigit()`, which is also true for `²` and
  `٣`), requires `> 0`, and converts with `int()`. No `Decimal`, no float, exact. Clean - the
  price path is not what this branch touches.
- **`payTo` is checksum-validated, not just shape-matched.** `valid_nano_address` decodes the
  base32 body, rebuilds the 5-byte blake2b digest of the public key and compares it. A single
  mistyped character in an address matches the regex just as happily, and a Nano send to it is
  irreversible. Verified the digest is byte-reversed before comparison, which is the part that is
  easy to get wrong.
- **Refusals before any payer is asked.** `_check_offer` runs the cap, the pinned `X402_PAY_TO`,
  the endpoint's scheme **and** the scheme of the URL the 402 actually arrived on. That last one is
  the good check: `requests` follows redirects and does not refuse a scheme downgrade, so a 302
  could otherwise hand a `payTo` from a plain-HTTP origin to a check made against the configured
  HTTPS endpoint. A redirect that stays on HTTPS is still paid, with the reasoning written down
  beside it.
- **Paid-but-not-served never pays twice.** `_fetch` raises after the single retry and keeps the
  hash on `last_payment` for a claim. `test_paid_but_not_served_never_pays_twice` holds.
- **The payer contract holds no key.** `pay(offer) -> block_hash`, and the returned hash is
  re-checked against `^[0-9A-Fa-f]{64}$` before it is put in an `X-PAYMENT` header.
- **The README's central warning is true.** `RETRIEVER=paypercall` alone does silently fall back to
  Tavily: `get_retrievers` is `get_retriever(r) or get_default_retriever()`, and `get_retriever` is
  a hardcoded match ending `case _: return None`. `register()` wraps it idempotently and leaves
  every other name's answer alone. The README does not overclaim here.
- **Secrets.** None in the tree. No key, no seed; the wallet file is the payer's and stays on the
  operator's machine.

## Found and fixed (open for review - it moves an amount)

**The spend cap was rounded at 28 significant digits**
(`src/gpt_researcher_x402_retriever/terms.py:69`, `xno_to_raw`), fixed on
`fix/the-spend-cap-was-rounded-at-28-digits`.

The XNO -> raw multiplication ran in the **process-global** `Decimal` context.
`decimal.getcontext().prec` defaults to **28** significant digits and this module never set it,
while a raw amount reaches **39** digits. So the multiplication rounded - and the
`raw != raw.to_integral_value()` test could not catch it, because *a value rounded at the 28th
significant digit is still an integer.*

`xno_to_raw` has exactly one caller: `PayPerCallSearch.max_raw` (`retriever.py:74`), the ceiling a
price is refused above (`retriever.py:139`). So a rounded answer was a ceiling nobody set.
Measured on the shipped code, with both caps at 29 decimal places - a legal whole number of raw,
which the conversion owes back unchanged:

| `X402_MAX_XNO` | the cap it produced | against the ask |
| --- | --- | --- |
| `99.99999999999999999999999999999` | `100000000000000000000000000000000` | **+10 raw** |
| `1.00000000000000000000000000006` | `1000000000000000000000000000000` | **-60 raw** |

The first is the one that costs something: the ceiling came back *above* what was configured, so a
price up to 10 raw over it was paid, and nothing refused it because the rounded value was still an
integer. The second refuses a price the operator meant to allow.

This is the same defect class as `nano-mcp-public`'s `usd_to_xno_raw()` and `langchain-vend`'s
`_parse_quote` (#5, still open) - a process-global `prec` of 28 against raw's 39 digits - reached
here through a third path.

Fixed by running the conversion in its own `localcontext` at 60 digits with `Inexact` trapped: a
cap at full raw precision converts exactly, a cap too long to convert even at 60 digits gets its
own named refusal instead of being rounded into a number nobody chose, a sub-raw cap keeps the
`to_integral_value()` refusal, and `localcontext` restores the caller's precision and traps so
importing this library does not change arithmetic in the importing program.

Fifteen tests added. Six fail with `terms.py` alone reverted to `main`:

```
test_a_cap_is_not_rounded_up_above_what_was_configured
test_a_cap_at_full_raw_precision_converts_exactly_and_not_sixty_raw_short
test_every_one_of_the_thirty_decimal_places_survives
test_a_cap_too_long_to_convert_exactly_is_refused
test_the_corrected_cap_reaches_the_retriever_that_reads_it
test_a_cap_that_cannot_be_converted_is_refused_at_construction
```

The other nine are controls that must hold either way, so the change cannot quietly cost a working
configuration: every cap that already converted exactly is unchanged (including `0.001`, the
default, and one raw), the default cap still pays the live Vend price of `10**26` raw, a negative
cap is still refused, a sub-raw cap is still refused rather than rounded up to one raw, and the
Decimal context does not leak out of the conversion.

**Not merged by this run, and it is not a close call.** The `+10 raw` case narrows the cap, but the
`-60 raw` case **widens** it: a price between `10**30` and `10**30 + 60` raw is paid under a cap of
`1.00000000000000000000000000006` where it was refused before. That is past the Authority
section's "only adds a refusal" on the send path, whichever way the arithmetic is more nearly
correct. One sentence of review: should a cap the operator wrote be honoured exactly, including
where that raises it by 60 raw?

## Could not verify

- **No live x402 endpoint was paid.** Every figure here is measured against `FakeSession` and the
  repository's recorded 402 fixture; `tests/test_live.py` needs `X402_LIVE=1` and a funded wallet,
  and this environment's network policy reaches neither. No XNO moved.
- **The cap values that trigger it are pathological** (29 decimal places), so whether any operator
  has ever set one is unknowable from here. The arithmetic is wrong at any such value; the exposure
  is conditional on the configuration.
- **GPT Researcher itself was not installed**, so `register()` was exercised only against the
  repository's stand-in module (`_install`), not against the real
  `gpt_researcher.actions.retriever`. The README's claim about 0.15.1's `match` statement is
  second-hand here.
