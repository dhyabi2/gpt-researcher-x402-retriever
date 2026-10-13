# gpt-researcher-x402-retriever — audit 2026-10-13

Lens: can an agent pay in XNO through this retriever today without being hurt? This package
spends real XNO on a stranger's 402, so the audit is spent on the decision to pay and on the
first step a new agent takes to be able to pay at all.

Starting point: `main` at `24f8757` ("refuse a 402 reached through a plain-HTTP redirect hop",
#11). `pip install -e ".[test]" && python3 -m pytest -q` → **76 passed, 3 skipped** before any
change, 79 after.

## Checked

- `_check_offer`, the whole pre-payment decision, in order: price cap, `X402_PAY_TO` pin,
  endpoint scheme, the scheme the 402 was actually served over, and every redirect hop
  (#11). All five run before `resolve_payer`, so nothing can be sent while one is pending.
  Re-read `response.history` semantics to confirm the hop list is complete: `history` holds
  the redirect *responses*, each `.url` being the URL requested at that hop, and
  `response.url` is the final one — together they cover every hop in a chain.
- `xno_to_raw`: 60-digit `localcontext` with `Inexact` trapped, `to_integral_value()` for a
  sub-raw cap, `localcontext` restoring the importer's precision. Re-derived the two cases
  the 10-10 audit measured (`+10 raw`, `-60 raw`): both exact now.
- `valid_nano_address`: checked the bit layout by hand (4 padding + 256 key + 40 checksum,
  with the regex pinning the first body character to `1`/`3` so the masked-away top bits are
  genuinely zero) and the blake2b-5 digest both ways. The three one-character-typo tests
  still refuse before the payer is asked.
- Every pattern ends in `\Z`, not `$` — re-confirmed, including the `"100\n"` case.
- `payers.py`: no default payer, `X402_PAYER` must produce an object with `pay(offer)`, the
  seed never leaves the wallet file.
- `_results`: scheme filter, dedupe, domain filter, `max_results`.
- The paid retry still carries the offer's own block hash, validated against `BLOCK_HASH`
  before it reaches a header.

## Found and fixed

**1. An absurd offer amount escaped as a bare `ValueError`** — `src/.../terms.py:164`
(now `:173`), the `int(amount) <= 0` leg.

`RAW_AMOUNT` tests the characters of the amount, not how many there are. `int()` refuses a
decimal string longer than 4300 digits outright
(`sys.int_info.str_digits_check_threshold`, CVE-2020-10735), so a seller quoting 4301
digits reached `int(amount)` and raised

```
ValueError: Exceeds the limit (4300 digits) for integer string conversion: value has 4301 digits
```

straight out of `parse_challenge`, whose documented failure is `TermsError`. Because
`TermsError` subclasses `ValueError`, `except TermsError` does not catch it. This is the
**same hole** the `"\u00b2"` case in `test_amount_must_be_a_positive_integer_raw_string` was
added for, reached by length instead of by script — the law was already written here, and
it had a gap. `PayPerCallSearch.search()` swallows it (it returns `[]` on anything), so no
money moves and no run dies; what an integrator calling `parse_challenge` in their own payer
loop gets is an exception their `except TermsError` misses, and a log line about
`sys.set_int_max_str_digits` rather than about a seller's price.

**Fix:** `MAX_AMOUNT_DIGITS = 48` folded into the shape test. The whole XNO supply is
`133248297000000000000000000000000000000` raw — **39 digits** — so nothing payable is
refused, and nine digits of room are left for a seller who pads with zeros. It only adds a
refusal: no amount, address, cap or rounding changes, and the refusal it adds is the one the
function already documents.

**Failing-then-passing:** two cases added to the existing parametrised law (`"1"*4301`,
`"9"*50000`) plus a control asserting 39 and 48 digits are still accepted and still convert
exactly. With the guard alone removed (the constant kept, so collection still works) exactly
those two fail with the `ValueError` above; with it, **79 passed, 3 skipped**.

**2. The README's `Paying` section opened with a command that cannot run** — README line 103.
Fixed in a separate pull request (no code change in it).

```
$ pip download --no-deps "gpt-researcher-x402-retriever[feeless402]"
ERROR: Could not find a version that satisfies the requirement
       gpt-researcher-x402-retriever[feeless402] (from versions: none)
ERROR: No matching distribution found for gpt-researcher-x402-retriever[feeless402]
```

This package is not on PyPI (`https://pypi.org/pypi/gpt-researcher-x402-retriever/json` →
**404**, measured this run), and neither is `openai-agents-nano-x402`, which the same line
offers as the alternative (**404**). Line 17 installs from git for exactly that reason. So
the first command an agent runs to become *able to pay* fails, two sections after a command
that works. `feeless402` itself is on PyPI (**0.2.13**) and does ship the `nano-pay` console
script the next line uses — verified from its wheel's `entry_points.txt` — so only the
project's own name was wrong. Replaced with the PEP 508 direct reference, verified by
`pip install --dry-run`: it resolves and would install `feeless402-0.2.13` alongside.

## Checked and left alone (unchanged from 10-12)

- **The paid retry is not hop-checked.** `_get({"X-PAYMENT": ...})` can itself be redirected
  through plain HTTP. The money has already moved by then, so refusing there throws away a
  paid call rather than preventing a loss, and a block hash is public ledger data.
- `X402_PAY_TO` is compared with `!=` and not normalised, so pinning an `xrb_` form against a
  seller quoting the equivalent `nano_` form refuses a legitimate payment. It fails safe.

## Could not verify

- No live 402. `https://search.paypercall.dev` is **not reachable from this container at
  all** — the agent proxy refuses the CONNECT (`curl: (56) CONNECT tunnel failed, response
  403`), which is this sandbox's allowlist and says nothing about the endpoint's health.
  `https://extract.paypercall.dev/.well-known/x402`, which the README also cites, does
  answer **200** from here. `tests/test_live.py` remains the opt-in path.
- `https://x402.org` and `https://docs.gptr.dev/...` could not be checked for the same proxy
  reason, not because they 404.
- The GPT Researcher resolution tests need a `gpt_researcher` checkout, which is not
  installed here; they are among the 3 skipped, not failing.
