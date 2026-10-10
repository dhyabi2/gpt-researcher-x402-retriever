# gpt-researcher-x402-retriever — audit 2026-10-10

Previous audit 2026-10-09 (`fc5ca01`). HEAD audited: `1c39484` ("The spend cap was rounded at 28
digits: a ceiling nobody set (#8)"), with the cap fix now on `main`. Read through one question: can
an agent pay in XNO with this, today, without being hurt?

**One defect found and fixed in this pull request.** It is a refusal that was not being made.

## Checked

- **Install and suite.** `pip install -e ".[test]"`, then `pytest -q` on `main`: **70 passed, 3
  skipped** (the skips are the `feeless402` extra, GPT Researcher not installed, and `X402_LIVE`
  unset — the same three as 10-09). With this branch: **74 passed, 3 skipped**.
- **Branches.** All nine remote branches have a pull request, every one merged; `git cherry` reads
  nonzero on three only because of the squash rewrite the Tier 0 note warns about. **0 orphans, 0
  open pull requests.**
- **The seller's published terms, against the code's defaults.** `extract.paypercall.dev
  /.well-known/x402` lists `https://search.paypercall.dev/api/v1/web-search` at
  `amount: "100000000000000000000000000"` (10**26 raw = 0.0001 XNO) to
  `nano_1yo6c1t64ahfjdw1dxizmbbnpdmbrckwhw9phbg5pdkeubrizga4qhnjmnx7`. That matches
  `DEFAULT_ENDPOINT`, the README's "0.0001 XNO per call", and sits a decimal place under
  `DEFAULT_MAX_XNO = "0.001"`, so the shipped cap pays the live price and nothing above it. The
  live `payTo` passes `valid_nano_address`; one character changed anywhere in it does not.
- **Amount handling.** Integer raw throughout (`amount_raw: int`, `RAW_PER_XNO = 10**30`). The one
  decimal conversion, `xno_to_raw`, runs in a 60-digit `localcontext` with `Inexact` trapped and
  no float touches an amount. `"1e26"`, `1e26`, `"0.0001"`, `" 100"`, `"+100"`, `True` and `1` are
  all refused as offer amounts.
- **The three refusals before money moves** (`_check_offer`): price over the cap, `payTo` not the
  pinned `X402_PAY_TO`, and the 402 not having arrived over HTTPS — checked on `response.url`, the
  origin after redirects, not just on `self.endpoint`.
- **No payer, no payment.** `resolve_payer` returns `None` by default; the 402 is reported and
  `search()` returns `[]`.

## Found and fixed

**`$` is not the end of the string, and three money-path patterns used it** —
`src/gpt_researcher_x402_retriever/terms.py:26-30`. In `re`, `$` also matches immediately before a
single trailing newline, so each of `NANO_ADDRESS`, `BLOCK_HASH` and `RAW_AMOUNT` accepted one.

- `payTo = "nano_...\n"` matched `NANO_ADDRESS`, so `valid_nano_address` (terms.py:42-51) went on to
  its base32 loop and `_B32["\n"]` raised **`KeyError('\n')`** — out of a function whose docstring
  promises a bool, and out of `parse_challenge`, whose documented failure is `TermsError`. The
  comment on terms.py:47 ("every character is in the alphabet: the regex checked") was not true.
  Measured on `1c39484`: `valid_nano_address(VEND_PAY_TO + "\n")` raises, and so does
  `parse_challenge` on a 402 carrying such a `payTo`. No XNO moves (an exception is still a
  refusal), but any consumer using this exported helper as a guard crashes instead of refusing, and
  the retriever's named refusal is replaced by `KeyError('\n')`.
- A payer returning `"<64 hex>\n"` passed the `BLOCK_HASH` check at `retriever.py:126` and the
  value went into the `X-PAYMENT` header, where `requests` 2.34.2 raises `InvalidHeader`
  ("return character(s) in header value") — **after `payer.pay` has sent the XNO**, with a message
  that never says a payment was made. A payer that shells out to a CLI and returns its stdout
  returns exactly that string.
- `amount = "100\n"` was accepted. `int()` reads the right number, so nothing was mispaid; the
  stated invariant was simply false.

Fixed by ending all three patterns in `\Z` instead of `$`. This only narrows what is accepted: no
amount, destination, rounding or key path changes. Four tests fail on `1c39484` and pass here.

## Found, not fixed (not this pull request's concern)

- **A malformed hash from a payer leaves no record of a payment that may have been made.**
  `retriever.py:127` raises `"payer did not return a 64-hex Nano block hash"` without the value, and
  `self.last_payment` is only set after the check — so if a payer sends the XNO and then returns
  something unparseable, nothing anywhere holds the hash for a refund claim. Putting `{block_hash!r}`
  in the message would keep the trail (a send hash is public ledger data, not a secret), but that is
  a change to the error path rather than to the newline refusal, so it is left for its own change.
- `maxTimeoutSeconds: true` becomes `1`, because `isinstance(True, int)` is true
  (`terms.py:167`). It is not an amount and not a destination; noted only.

## Could not verify

- `https://search.paypercall.dev/api/v1/web-search` is not reachable from this session — the agent
  proxy answers `403` for that host before the request leaves (`extract.paypercall.dev` is
  allowed, which is how the published terms above were read). So the live 402 was confirmed from
  the seller's own `.well-known/x402` manifest, not by calling the endpoint. `pytest -m live`
  remains the check a person runs.
- The four `Feeless402Payer` tests skip here: the `feeless402` extra is not installed in this
  session and installing it was not attempted on a money-path dependency.
