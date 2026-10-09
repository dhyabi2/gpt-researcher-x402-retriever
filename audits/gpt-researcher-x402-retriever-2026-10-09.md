# gpt-researcher-x402-retriever — audit 2026-10-09

Previous merged audit 2026-10-04. Read through one question: can an agent pay in XNO with this,
today, without being hurt?

HEAD audited: `fc5ca01` ("A 402 that arrived over plain HTTP was paid (#7)"). Python 3.11.

**No production line is touched.** The one defect found is the one `#8` is already open for, and
this run adds a measurement that makes it concrete rather than a second pull request.

## Checked

- **Install and suite, as the README prints them.** `pip install -e ".[test]"` then `pytest -q`:
  **52 passed, 3 skipped**. The three skips are `test_feeless402_payer.py` ("needs the feeless402
  extra"), `test_gpt_researcher_plugin.py` (GPT Researcher not installed) and `test_live.py`
  (`X402_LIVE=1` unset).
- **The skipped money-path tests, run.** `pip install -e ".[feeless402]"` succeeds here —
  **feeless402 0.2.13** — and the suite then reads **56 passed, 2 skipped**. So the four
  `Feeless402Payer` tests, which cover the only code path in this package that spends XNO, do run
  when the extra installs. CI installs it on a `|| echo` line that cannot fail the build, which is
  right for an optional extra and means green CI alone does not prove those four ran; they are
  confirmed run here.
- **`Feeless402Payer.pay` against the installed library, by signature rather than by reading ours.**
  `nano_pay.wallet.Wallet.send(self, rpc, to_addr: str, raw_amt: int, prework=False) -> str` — the
  third positional argument is **raw**, which is what `offer.amount_raw` is, and `prework`
  defaults off, so the payer does not block a request path solving work for the next block.
- **`terms.parse_challenge`.** Takes only `("exact", "nano:mainnet", "XNO")`; the amount must be a
  **string of ASCII digits** (`RAW_AMOUNT`, not `str.isdigit()`, which is also true for `²` and
  `٣`) and `> 0`; `payTo` must pass **shape and blake2b checksum**, so a single mistyped character
  cannot send XNO to an account nobody holds the key to. A malformed Nano offer **raises** rather
  than falling through to the next entry — a refusal, which is the safe direction.
- **`_check_offer`, the three refusals before money moves.** Price `>` `max_raw`; `payTo` not the
  pinned `X402_PAY_TO`; and the 402's own `response.url` scheme not HTTPS, which is the subject of
  `#7` — `requests` follows redirects and does not refuse a scheme downgrade, so checking only
  `self.endpoint` let a plain-HTTP responder's address be paid.
- **Paid-but-not-served.** `_fetch` raises rather than retrying, and keeps the block hash on
  `self.last_payment` for a refund claim. `search()` turns every exception into `[]` so one
  provider cannot abort a research run.
- **No payer, no payment.** `resolve_payer` returns `None` by default and the retriever reports the
  402 and returns `[]`. Nothing is paid unless a payer was plugged in.
- **Secrets.** None in the tree. This package holds no key: the seed stays in the feeless402
  wallet file and only a signed block leaves the machine.

## Found

**The spend cap `X402_MAX_XNO` is exact only when a dependency has set a `decimal` global, so the
same configured cap produces two different ceilings depending on whether the payer the README
recommends was imported.** This is the defect `#8` is open for, measured here from the other end.

`terms.xno_to_raw` computes `amount * RAW_PER_XNO` in the **ambient** `decimal` context.
`decimal.getcontext().prec` defaults to 28 significant digits; a raw amount reaches 39. `nano_pay`
— imported by `Feeless402Payer`, and the payer the README tells the user to install — sets that
global to 40. Measured on this tree:

| `X402_MAX_XNO` | cap without the payer imported | cap with it | the ask |
| --- | --- | --- | --- |
| `99.99999999999999999999999999999` | `100000000000000000000000000000000` | `99999999999999999999999999999990` | **10 raw above** vs exact |
| `1.00000000000000000000000000006` | `1000000000000000000000000000000` | `1000000000000000000000000000060` | **60 raw below** vs exact |

```
$ python -c "import decimal; from gpt_researcher_x402_retriever.terms import xno_to_raw; ..."
ambient prec = 28
  cap 99.99999999999999999999999999999 -> 100000000000000000000000000000000
$ python -c "import nano_pay.wallet; import decimal; ..."
ambient prec = 40
  cap 99.99999999999999999999999999999 -> 99999999999999999999999999999990
```

The `to_integral_value()` test in `xno_to_raw` cannot catch it, because **a value rounded at the
28th significant digit is still an integer**. The first row is the one that costs something: the
ceiling comes back *above* what the operator configured, so a price up to 10 raw over it is paid
and no refusal fires.

What is new here is not the arithmetic — `#8` has that, with the same two rows — but that the
answer depends on **import order and on whether an optional extra is installed**. A cap is not
something that should read differently in two processes running the same configuration.

## Fixed

Nothing, deliberately. **`#8` already fixes this** (its own `localcontext` at 60 digits with
`Inexact` trapped) and it is **correctly held open**: its docstring says so in as many words — the
`-60 raw` row *widens* what is paid, so a price between `10**30` and `10**30 + 60` raw is accepted
under that cap where it was refused before. That is past the Authority grant's "only adds a
refusal", so it is the owner's to merge and no routine's. Opening a second pull request over the
same lines would conflict with it for nothing.

This audit's contribution is the measurement above, so the decision on `#8` is made against what
the defect actually does rather than against a worked example.

## Could not verify

- **No live 402 was paid and no XNO moved.** `test_live.py` needs `X402_LIVE=1` and a reachable
  seller.
- **Whether the default endpoint is up.** `search.paypercall.dev` answers `403 Forbidden` with
  `X-Content-Type-Options: nosniff` from **this environment's agent proxy**, not from the host, on
  `/`, `/.well-known/x402` and `/api/v1/web-search` alike. `extract.paypercall.dev/.well-known/x402`
  answers 200 from here, so the policy allows one host of the pair and not the other. Nothing can
  be concluded about the search endpoint's health from this session.
- **The README's outbound links.** `docs.gptr.dev` and `x402.org` return no status through the
  proxy and `github.com/assafelovic/gpt-researcher` returns 403 from it; the four `dhyabi2` links
  and `pypi.org/project/feeless402/` answer 200. No link was shown to be dead, and none was shown
  to be live apart from those five.
- **`X402_PAY_TO` is compared as a string**, so pinning `xrb_…` against a seller offering the
  equivalent `nano_…` for the same account refuses a payment that would have been correct. That
  direction is safe (it refuses, never misdirects), and making it checksum-equal would *widen*
  what is paid, so it is written down rather than changed. `dual-rail`'s `verify.py` compares by
  decoded public key, which is the shape to copy if this is ever revisited.
- **GPT Researcher itself is not installed here**, so `register()`'s wrap of
  `gpt_researcher.actions.retriever.get_retriever` was read, not exercised. The README's claim that
  upstream 0.15.1 reads no entry points is a second-hand citation in this run.
