# gpt-researcher-x402-retriever

A keyless web-search retriever for [GPT Researcher](https://github.com/assafelovic/gpt-researcher),
installed alongside it as a [retriever plugin](https://docs.gptr.dev/docs/gpt-researcher/search-engines/retriever-plugins).
It needs no API key. Each search is paid per call over [x402](https://x402.org) in Nano (XNO),
and it only pays when you plug in a payer.

> **Disclosure:** this package was written by the operator of the API it calls by default,
> Vend (`search.paypercall.dev`, `extract.paypercall.dev`). A paid search sends XNO to that
> operator. The endpoint is configurable (`X402_SEARCH_URL`), and the code will pay any x402
> seller whose 402 offers the `exact` scheme on `nano:mainnet`. It was built with an AI
> coding agent's help.

## Install and use

```bash
pip install "git+https://github.com/dhyabi2/gpt-researcher-x402-retriever"
```

**`RETRIEVER=paypercall` alone is not enough, and fails quietly.** GPT Researcher resolves a
retriever name in `gpt_researcher.actions.retriever.get_retriever`, a hardcoded `match` over its
built-in names ending in `case _: return None` — it reads no entry points (checked against 0.15.1,
the current release). `get_retrievers` then does `get_retriever(r) or get_default_retriever()`, so
an unrecognised name raises nothing and silently becomes **Tavily**, which needs the API key this
package exists to avoid. The `gpt_researcher.retrievers` entry point is declared here for the day
upstream reads one.

So the name has to be registered in the same process, which takes one line:

```python
import gpt_researcher_x402_retriever as x402
x402.register()                      # now "paypercall" resolves

from gpt_researcher import GPTResearcher
researcher = GPTResearcher(query="...")        # honours RETRIEVER=paypercall,duckduckgo
report = await researcher.conduct_research()
```

`register()` wraps `get_retriever` so `paypercall` answers this class and every other name keeps
its original answer — a combined `RETRIEVER=paypercall,duckduckgo` still works. It is idempotent,
and returns `False` instead of raising if GPT Researcher is not importable.

Or skip the name entirely and hand the class over directly:

```python
researcher = GPTResearcher(query="...")
researcher.retrievers = [x402.PayPerCallSearch]   # set after construction; there is no kwarg
```

If you run GPT Researcher as a server or CLI you do not launch yourself, `register()` has to run
inside that process — import it from your own entry point, or use the `researcher.retrievers`
assignment where the instance is built. There is no environment-only setup today.

## What happens on a call

1. `GET https://search.paypercall.dev/api/v1/web-search?q=<sub-query>`.
2. **200**: results come back. The seller gives each IP 5 free calls a day
   (see `trial` in [/.well-known/x402](https://extract.paypercall.dev/.well-known/x402)).
3. **402**: the retriever reads the x402 terms (JSON body, or the base64 `PAYMENT-REQUIRED` header).
   Today they are 0.0001 XNO (`100000000000000000000000000` raw), paid to
   `nano_1yo6c1t64ahfjdw1dxizmbbnpdmbrckwhw9phbg5pdkeubrizga4qhnjmnx7`. Before anything is paid,
   the terms must pass these checks:
   - the scheme is `exact`, the network is `nano:mainnet` and the asset is `XNO`;
   - the amount is a positive integer raw string, and `payTo` is a well-formed Nano address;
   - the price is at most `X402_MAX_XNO` (default `0.001`);
   - `payTo` equals `X402_PAY_TO`, if you set it;
   - the endpoint is HTTPS, **and so is the URL the 402 actually came from** -- `requests` follows
     redirects, so a 302 could otherwise hand the `payTo` address to a plain-HTTP origin and still
     pass a check made against the configured endpoint. A redirect that stays on HTTPS is paid.
4. **With no payer configured (the default), nothing is paid.** The price is logged, and
   `search()` returns `[]`, so GPT Researcher carries on with your other retrievers.
5. **With a payer**, the payer sends exactly that amount and returns the block hash. The retriever
   then retries once with `X-PAYMENT: <block hash>`. If the paid retry isn't served, it never pays
   again for that call. The hash stays on `retriever.last_payment` so you can take it up with the seller.

Results are `[{"href", "body", "title"}]`: links and snippets. The retriever declares
`requires_scraping = True`, so GPT Researcher fetches each page itself and keeps a real citation.
`query_domains` becomes `site:` filters and also filters the returned hosts. Every failure
returns `[]` instead of raising.

## Plugging in a payer

A payer is any object with `pay(offer) -> block_hash`. `offer` is a `PaymentOffer`
(`pay_to`, `amount_raw` as an integer, `amount_xno`, `network`, `asset`, `scheme`, `resource`).
The payer must send exactly `offer.amount_raw` raw to `offer.pay_to` and return the send block's
64-hex hash. The retriever never holds a key.

GPT Researcher constructs retrievers itself. Set the payer in one of two ways:

- `X402_PAYER=package.module:factory` in the environment (a class or a zero-argument function), or
- `gpt_researcher_x402_retriever.set_default_payer(payer)` in your own process.

### openai-agents-nano-x402 / feeless402

[openai-agents-nano-x402](https://github.com/dhyabi2/openai-agents-nano-x402) is a thin adapter
over [feeless402](https://pypi.org/project/feeless402/). Its self-custodied wallet can pay here
directly through the included `Feeless402Payer`:

```bash
pip install "gpt-researcher-x402-retriever[feeless402]"   # or install openai-agents-nano-x402
nano-pay init                                             # creates ~/.nano-pay/wallet.json locally
# fund that address with a small amount of XNO, then:
export X402_PAYER=gpt_researcher_x402_retriever.payers:Feeless402Payer
export X402_MAX_XNO=0.0001
```

The seed stays in the wallet file on your machine. Blocks are signed locally, and only the signed
block is published.

### nano-wallet-xno

[nano-wallet-xno](https://github.com/dhyabi2/nano-wallet-xno) refuses to send unless you set
`NANO_WALLET_ALLOW_SEND=1`. It also caps a single send (`NANO_WALLET_MAX_SEND_XNO`). Wrap its
`payments.send`:

```python
# my_payer.py  (nano-wallet-xno's directory on sys.path)
import uuid
import payments, nanonode, keystore, wallet

class NanoWalletPayer:
    def __init__(self):
        self.source = "<your nano_ address>"
        self.node = nanonode.HttpNanoNode("<your node RPC URL>")
        self.keys = keystore.KeyStore()          # load your key into it (see its README)
        self.sent = {}                           # idempotency record

    def pay(self, offer):
        result = payments.send(
            self.source, offer.pay_to, wallet.raw_to_xno(offer.amount_raw),
            idempotency_key=f"x402:{uuid.uuid4().hex}",
            node=self.node, keys=self.keys, sent=self.sent,
        )
        return result["block_hash"]
```

**The key has to be unique per search.** `nano-wallet-xno` treats a repeated
`idempotency_key` as the same payment and returns the first block hash again
without sending anything. A key built from the offer alone — the resource and
the amount — is the same string on every search of the same endpoint at the
same price, so with one long-lived payer (which is what `set_default_payer`
gives you) the second search would hand the seller a block hash it has already
spent, the seller would refuse it, and every paid search after the first would
come back empty.

The retriever calls `pay()` at most once per `search()` and never retries a
payment, so a fresh key per call is the right granularity. If you add your own
retry around `pay()`, compute the key once outside it and pass it in, so a
retry of *that* call reuses it and cannot pay twice.

```bash
export NANO_WALLET_ALLOW_SEND=1 X402_PAYER=my_payer:NanoWalletPayer
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `X402_SEARCH_URL` | `https://search.paypercall.dev/api/v1/web-search` | endpoint (`?q=` query) |
| `X402_MAX_XNO` | `0.001` | refuse any single call priced above this |
| `X402_PAY_TO` | unset | if set, only this Nano address is ever paid |
| `X402_PAYER` | unset | `module:factory` for a payer; unset means never pay |

## Tests

```bash
pip install -e ".[test]"
pytest                          # offline: mocked HTTP, recorded real 200/402 answers
X402_LIVE=1 pytest -m live      # one live check: the endpoint answers 402 with valid terms
```

The live check never pays. The seller gives each IP 5 free calls a day, so the check may use up
to 5 of yours before the 402 appears. It then confirms the 402 terms match the seller's published
`/.well-known/x402`. To run the two GPT Researcher resolution tests against a checkout (or against
an unpacked wheel), set `GPTR_RETRIEVER_PY=<gpt-researcher>/gpt_researcher/actions/retriever.py`;
without it they are skipped, and a stand-in copied from 0.15.1 covers the same two assertions.

## License

MIT
