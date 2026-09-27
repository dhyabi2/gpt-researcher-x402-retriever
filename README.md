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
export RETRIEVER=paypercall          # or combine: RETRIEVER=paypercall,duckduckgo
```

GPT Researcher finds the retriever through the `gpt_researcher.retrievers` entry point. You don't
need to change GPT Researcher.

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
   - the endpoint is HTTPS.
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
            idempotency_key=f"x402:{offer.resource}:{offer.amount_raw}"[:64],
            node=self.node, keys=self.keys, sent=self.sent,
        )
        return result["block_hash"]
```

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
`/.well-known/x402`. To run the GPT Researcher resolution test against a checkout, set
`GPTR_RETRIEVER_PY=<gpt-researcher>/gpt_researcher/actions/retriever.py`.

## License

MIT
