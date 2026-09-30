# Audit 2026-09-30

First audit of this repository.

## Checked

- `pip install -e ".[test]" && pytest -q`: 40 passed, 2 skipped before any change.
- The default endpoint, price and payee against the seller's live published manifest at
  `https://extract.paypercall.dev/.well-known/x402`. All three match the README exactly:
  `https://search.paypercall.dev/api/v1/web-search`, `100000000000000000000000000` raw
  (0.0001 XNO) and `nano_1yo6c1t64ahfjdw1dxizmbbnpdmbrckwhw9phbg5pdkeubrizga4qhnjmnx7`, with a
  5-call-a-day per-IP trial. The disclosure about who operates that endpoint is present and
  accurate.
- `terms.parse_challenge` against the recorded real 402 body, which carries both an `accepts`
  array and loose top-level fields: it reads only `accepts`, and refuses anything that is not
  `exact` / `nano:mainnet` / `XNO`, or whose amount is not a positive integer raw string, or
  whose `payTo` is not a well-formed Nano address.
- `xno_to_raw` at the boundaries: `NaN` and `Infinity` are refused (the `is_finite` check
  short-circuits before a comparison that would raise), negatives are refused, and anything
  finer than 1 raw is refused rather than rounded.
- `_check_offer`: the price cap, the `X402_PAY_TO` pin and the HTTPS requirement all run before
  a payer is ever called, which is the order the README claims.
- The paid retry: one retry only, and a paid-but-unserved response raises with the block hash
  kept on `last_payment` instead of paying again.
- `payers.resolve_payer`: with no payer configured nothing is paid and `search()` returns `[]`.
  There is no default payer anywhere in the tree, and the package holds no key material.

## Found and fixed

**`RETRIEVER=paypercall`, the whole documented way to use this package, silently selected Tavily
instead — so the package a user installed was never called, and they landed on the one retriever
that needs a paid API key.** GPT Researcher resolves a retriever name in
`gpt_researcher.actions.retriever.get_retriever`, which is a hardcoded `match` over its built-in
names ending in `case _: return None`. It reads no entry points: grepping the whole 0.15.1 wheel
for `entry_points`, `importlib.metadata` and `pkg_resources` finds nothing, and
`GPTResearcher.__init__` has no retriever parameter either. `get_retrievers` then does
`get_retriever(r) or get_default_retriever()`, so an unknown name does not raise — it becomes
`TavilySearch`. Verified against the released module itself:

    get_retriever('paypercall')            -> None
    RETRIEVER=paypercall actually resolves -> ['TavilySearch']

The README said "GPT Researcher finds the retriever through the `gpt_researcher.retrievers` entry
point. You don't need to change GPT Researcher." That is not true of any released version. The
repository's own test for this (`test_gpt_researcher_resolves_retriever_paypercall`) skips unless
GPT Researcher is installed, so CI never caught it.

Fixed with `register()`, which wraps `get_retriever` so `paypercall` answers this class and every
other name keeps its original answer, and by correcting the README to say plainly that the entry
point is not read, that there is no environment-only setup today, and what the two working paths
are. The entry point is kept, declared for the day upstream reads one.

## Could not verify

- `https://search.paypercall.dev/...`, `https://x402.org` and `https://docs.gptr.dev/...` are
  denied by this sandbox's network policy (the gateway answers 403 to CONNECT), so their liveness
  is unknown from here. `extract.paypercall.dev` is reachable and healthy, and its manifest still
  advertises the search endpoint, so there is no reason to think it is down.
- `Feeless402Payer` needs the `feeless402` package, which is not installed here, so the one line
  that calls `Wallet(...).load()` and `wallet.send(...)` is unexercised. It is covered by no test
  in the repository either.
- The live check (`X402_LIVE=1 pytest -m live`) was not run: it spends the seller's free trial
  calls from this IP.
