# gpt-researcher-x402-retriever — audit 2026-10-03

Python 3.11.15. On `main` at `88191ea`: `pytest -q` → **45 passed, 2 skipped**. No code change
this run. The work was verification: two of the three things earlier audits recorded as
unverifiable are now verified, one against the real released dependency and one against the real
upstream package.

## Verified this run (both were "could not verify" before)

**1. The upstream claim behind PR #1 is true, measured against the released wheel.**
Earlier passes asserted that GPT Researcher reads no entry points. This run downloaded
`gpt_researcher-0.15.1-py3-none-any.whl` from PyPI and read it:

- `gpt_researcher/actions/retriever.py` — `get_retriever` is a hardcoded `match` over fifteen
  built-in names, ending exactly in `case _: return None`.
- `get_retrievers` ends in `[get_retriever(r) or get_default_retriever() for r in retrievers]`,
  and `get_default_retriever()` returns `TavilySearch`.
- `grep -rn "entry_points|importlib.metadata|pkg_resources"` over every `.py` in the wheel:
  **no match**.
- `gpt_researcher/agent.py:175` is `self.retrievers = get_retrievers(self.headers, self.cfg)`;
  there is no retriever parameter on `GPTResearcher.__init__`.

So `RETRIEVER=paypercall` resolves to `None` and silently becomes Tavily — the one retriever that
needs the paid API key this package exists to avoid. Reproduced as a test failure on unmodified
`main`, pointing the repository's own test at that wheel's module:

    GPTR_RETRIEVER_PY=<wheel>/gpt_researcher/actions/retriever.py pytest -q
    FAILED tests/test_gpt_researcher_plugin.py::test_gpt_researcher_resolves_retriever_paypercall
    1 failed, 45 passed, 1 skipped

The repository's own test for this claim fails against the real package and is skipped everywhere
else, which is why CI is green. **PR #1 fixes exactly this and is still open** — see below.

**2. `Feeless402Payer` — the only line here that moves money — has the right contract.**
Both previous audits listed it as unexercised because `feeless402` was not installed. This run
read the published package (`feeless402-0.2.11-py3-none-any.whl`, `nano_pay`):

    wallet.py:36   Wallet.__init__(self, path: Path = None)
    wallet.py:62   Wallet.load(self)            -> returns self
    wallet.py:177  Wallet.send(self, rpc, to_addr: str, raw_amt: int, prework=False) -> str
    rpc.py:33      RPC.__init__(self, urls=None, timeout=20, ...)

`payers.py:85-89` is `(Wallet(Path(p)) if p else Wallet()).load()`, `RPC()`, and
`self._wallet.send(self._rpc, offer.pay_to, offer.amount_raw)`. Argument order matches, the
third parameter is **raw as an integer** (which is what `offer.amount_raw` is), `load()` returning
`self` is what the chained call needs, `RPC()` takes no required arguments, and `send` returns the
block hash string the payer contract promises. It is also correct to leave `prework` defaulted:
feeless402's own docstring says passing `True` blocks for minutes after the send has landed and
"never on a request path".

So the money-moving line is right. It remains covered by no test, which is a different statement
and still true.

## Also checked

- The default endpoint, price and payee against the seller's live manifest,
  `https://extract.paypercall.dev/.well-known/x402`. The `web-search` resource advertises
  `scheme: exact`, `network: nano:mainnet`, `asset: XNO`, `amount:
  100000000000000000000000000` (0.0001 XNO) and
  `payTo: nano_1yo6c1t64ahfjdw1dxizmbbnpdmbrckwhw9phbg5pdkeubrizga4qhnjmnx7` — matching
  `README.md:30-31` exactly, as does the 5-call-a-day per-IP trial.
- Re-read, unchanged and still correct: every amount is an integer (`PaymentOffer.amount_raw` is
  `int`, the cap is compared as `int`, `amount_xno` is a `Decimal` used only in messages); the
  price cap, the `X402_PAY_TO` pin and the HTTPS refusal all run before `resolve_payer`;
  `payer.pay` is called once per `search()` and a paid-but-unserved retry raises with the block
  hash rather than paying again.
- No secret, key or seed in the tree or history; no `exec`, `subprocess` or shell.

## Found

No new defect, and nothing changed. The one open defect on this repository is PR #1, below.

## Open, needs your decision — PR #1, now four days old

`#1 RETRIEVER=paypercall silently selected Tavily; register() makes it resolve`, opened
2026-09-30, `mergeable_state: clean`. Three audits have now left it open, and its own body says
"Not merging this one", for two stated reasons. **One of those two is now closed** (the
`Feeless402Payer` contract, above). The other stands: no live paid call was made, because
`search.paypercall.dev` is denied by this container's network policy (the proxy answers
`CONNECT tunnel failed, response 403`) and the live check spends the seller's free-trial calls.

On its branch, against the real 0.15.1 module: **50 passed, 1 skipped**. Under CI's configuration:
**49 passed, 2 skipped**. It touches `README.md`, `__init__.py` exports, two new module-level
functions in `retriever.py`, tests and an audit note — no payment logic, no key path.

It was not merged by this run either, and the reason is worth stating plainly rather than deferring
a fourth time: the diagnosis is no longer in doubt, so what is left is a judgement only you can
make. Without the change the package is never called at all and a user who followed the README is
quietly routed to a keyed competitor. With it, the retriever actually runs — and if a payer is
configured, XNO actually moves where previously none did. Nothing pays without `X402_PAYER` or
`set_default_payer` (there is still no default payer), but "the documented path starts working" is
a real behaviour change and the only finding here that is not purely a refusal. It wants one line
from you, in either direction.

## Could not verify

- No live call and no payment: `search.paypercall.dev` is blocked by this container's network
  policy, and `X402_LIVE` was not set. `extract.paypercall.dev` is reachable and still advertises
  the search endpoint, so there is no reason to think it is down.
- `Feeless402Payer` was verified by reading the published package's signatures, not by running it:
  `feeless402` was downloaded, not installed, and no wallet exists here to load.
