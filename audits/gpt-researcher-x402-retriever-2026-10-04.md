# gpt-researcher-x402-retriever — audit 2026-10-04

Lens: can an agent pay for a web search in XNO with this, today, without being hurt?

Baseline on `main` `a4264cf`: `pip install -e '.[test]'` plus the optional `feeless402` extra
(0.2.13 installs here), **53 passed / 2 skipped**.

## Checked

- `retriever.py` end to end: `search` -> `_fetch` -> `parse_challenge` -> `_check_offer` ->
  `payer.pay` -> the paid retry -> `_results`.
- `terms.py`: `valid_nano_address` (shape **and** blake2b checksum), `xno_to_raw`,
  `parse_challenge`, `PaymentOffer`.
- `payers.py`: `resolve_payer` precedence, `payer_from_env`, `Feeless402Payer`.
- Every refusal the README promises, against the code that implements it.
- `register()` / `_install` against GPT Researcher's `get_retriever`.

## Found and fixed — a 402 that arrived over plain HTTP was paid

`_check_offer` refuses to "pay over a non-HTTPS endpoint" (`retriever.py:143-144`), but it read the
scheme off `self.endpoint` — the URL that was *asked for*. `self._session.get(...)` follows
redirects, which is `requests`' default, and `requests` does not refuse a scheme downgrade.

Measured 2026-10-04 against a local TLS server answering 302 to a local plain-HTTP one, through a
real `requests.Session`:

```
requested : https://localhost:44709/api/v1/web-search
final url : http://127.0.0.1:33733/api/v1/web-search
history   : [302]
status    : 402
payTo     : nano_1banexkcf...        <- read from the PLAIN-HTTP responder
```

So the Nano address that gets paid could come from an origin anyone on the network path could have
rewritten, while the guard that exists to prevent exactly that still read `https` off the configured
URL and allowed the send. A Nano send is irreversible and there is no chargeback. `X402_PAY_TO`
would have caught it, but it is documented as optional and is unset by default.

Fixed by applying the same test to `response.url` — where the 402 actually came from. It is a
refusal and nothing else: no amount, destination, rounding or key path is touched, and a redirect
that stays on HTTPS is still paid (a control test pins that, so the change cannot be mistaken for
"any redirect is refused").

Failing-then-passing, with the tests kept and `retriever.py` alone reverted to `main`:

```
FAILED tests/test_retriever.py::test_a_402_that_arrived_over_plain_http_is_not_paid
  AssertionError: nothing may be sent for a 402 that arrived over plain HTTP
```

After: **56 passed / 2 skipped**, `py_compile` clean. The README bullet that claimed "the endpoint is
HTTPS" is corrected in the same commit, because it was the claim the code did not support.

## Found, NOT fixed here — `xno_to_raw` rounds the spend cap at 28 digits

`terms.py:69-80` converts `X402_MAX_XNO` to raw in the **process-global** `decimal` context, whose
`prec` defaults to 28 and which this module never sets, while raw XNO reaches 39 digits. The
integrality check on line 78 cannot see the rounding, because *a value rounded at the 28th
significant digit is still an integer*. Measured:

| `X402_MAX_XNO` | the cap names | the code produced | error |
|---|---|---|---|
| `1.000000000000000000000000000001` | `1000000000000000000000000000001` | `1000000000000000000000000000000` | **−1 raw** |
| `12.3456789012345678901234567890123` | `12345678901234567890123456789012` | `12345678901234567890123456790000` | **+988 raw** |
| `123.456789012345678901234567890123` | `123456789012345678901234567890123` | `123456789012345678901234567900000` | **+9877 raw** |

`max_raw` is the ceiling in `_check_offer`, so a cap rounded UP is headroom nobody authorised and a
cap rounded DOWN refuses a price that is exactly the configured maximum.
`PaymentOffer.amount_xno` divides in the same context, so `10**30 + 1` raw is *displayed* as
`1.000000000000000000000000000` — the number an operator reads in the "needs payment (%s XNO)" log
line and in the `price %s XNO is above X402_MAX_XNO` refusal is not the number asked for.

This is the third instance of the same law in this swarm (`nano-mcp-public`'s `usd_to_xno_raw`,
`langchain-vend#5`'s `_parse_quote`). It is **not** fixed in this run's merged change because the fix
*changes an amount* — it moves a cap that gates a send in both directions — which is past the
Authority section's "only adds a refusal". It is reachable only from an `X402_MAX_XNO` carrying more
than 28 significant digits, which no shipped default does (`0.001` converts exactly).

## Could not verify

- **The live endpoint's redirect behaviour.** `search.paypercall.dev:443` is denied by this
  environment's network policy (`connect_rejected`), so whether the real seller redirects could not
  be measured from here. It does not change the fix: an HTTPS→HTTPS redirect is still paid, and a
  payment challenge redirected to plain HTTP is the thing that must be refused.
- **No end-to-end paid call.** `tests/test_live.py` is gated on `X402_LIVE=1` and needs a funded
  wallet; the two `live` tests skip. The `Feeless402Payer` money path is covered against the real
  `feeless402` with a stub RPC (added by #6), which is what this run re-ran, not a broadcast send.
