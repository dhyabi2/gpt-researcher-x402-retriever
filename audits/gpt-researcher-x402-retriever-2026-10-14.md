# gpt-researcher-x402-retriever — audit 2026-10-14

Lens: can an agent pay in XNO through this retriever today without being hurt? This package
spends real XNO on a stranger's 402, so the audit is spent on the decision to pay.

Starting point: `main` at `24f8757`. **Nothing changed this run** — no defect I could prove,
and the three open pull requests (#12, #13, #14) already hold this week's findings. What this
note adds is a re-run of the money-path tests on this head.

## The one code path that moves money, re-run

`tests/test_feeless402_payer.py` exists because `Feeless402Payer.pay` is "the one code path
that moves money", and every other test stubs it. It opens with
`pytest.importorskip("nanopy")` and `pytest.importorskip("nano_pay.wallet")`, so it runs only
when the `feeless402` extra is installed. CI does install it: `.github/workflows/test.yml:17`
runs `pip install -e ".[feeless402]" || echo "feeless402 unavailable; payer tests will skip"`
(added in `bb0d7e7`, #6), and the 10-09 note already ran these tests with the extra installed.

Measured on this head:

```
76 passed, 3 skipped      <- locally, without the feeless402 extra
80 passed, 2 skipped      <- with the feeless402 extra installed, as CI attempts
```

With the extra, four tests move from skipped to passing and exercise the line that matters:

```python
return self._wallet.send(self._rpc, offer.pay_to, offer.amount_raw)   # payers.py:89
```

`offer.amount_raw` is **integer raw**. Had `nano_pay.Wallet.send` expected a decimal XNO
amount, this line would send 10**30 times the price — 0.0001 XNO becoming 100 billion XNO.
It takes raw. The line is correct.

One real observation about CI: the install is best effort. Because of the `|| echo` fallback,
if the `feeless402` install ever failed, the four payer tests would turn into skips and the
build would still be green. That is a deliberate choice for an optional extra, and this note
does not claim it has happened; it means a green run alone does not prove the payer tests ran —
the skip count in the log does. `nanopy` also sets the process-global `decimal` precision to
40 at import, which `tests/test_retriever.py:414` already documents and works around, and the
suite is green either way.

## What else I checked

- The spend path end to end: `_fetch` → `parse_challenge` → `_check_offer` → `resolve_payer`
  → `payer.pay(offer)` → the paid retry. The refusal order still holds: the offer is fully
  checked **before** `resolve_payer` is called, so no payer is constructed for terms that
  will be refused, and `payer.pay`'s return is re-validated as 64 hex before it reaches a
  header.
- Amount handling. No float touches an amount. `RAW_AMOUNT` is `^[0-9]+\Z` (not
  `str.isdigit()`, which admits `٣`), `amount_raw` is a Python `int`, the cap comparison
  `offer.amount_raw > self.max_raw` is integer-to-integer, and `xno_to_raw` runs in its own
  60-digit `localcontext` with `Inexact` trapped. Every pattern in `terms.py` ends in `\Z`,
  not `$`, so a trailing newline cannot pass as an address, a hash or an amount.
- `valid_nano_address` verifies the blake2b checksum, not just the shape — the only thing
  that catches one mistyped character in an address a send cannot be taken back from. Worth
  noting against `tollstile-rail-nano`, where the equivalent construction guard (#22) had to
  implement blake2b by hand because `node:crypto` exposes no 40-bit digest; here it is one
  `hashlib.blake2b(..., digest_size=5)` call in a four-line check (`terms.py:55-58`).
- `offer.resource` is attacker-controlled (`parse_challenge` takes it from the challenge
  body's `resource.url`). I traced every use: it is carried on `PaymentOffer` and read by no
  payer and no header. Nothing acts on it. No change.
- The HTTPS guard's three legs — the configured endpoint, `response.url`, and every hop in
  `response.history` (#11, 10-12). All three still present and exact.

## Could not verify

- **No payment was made.** Everything above is against the fixtures and the local wallet; no
  XNO moved and no live seller was paid, which is the owner's call and not a routine's.
- **Three README link hosts could not be checked from here.** `docs.gptr.dev`,
  `search.paypercall.dev` and `x402.org` answer `403` to `CONNECT` through
  this container's egress proxy — a network-policy denial on the allowlist, **not** evidence
  that any of them is broken. For contrast, `pypi.org/project/feeless402/`,
  `extract.paypercall.dev/.well-known/x402` and `rpc.nano.to` are reachable and answer `200`
  from here, so the live x402 trial terms the README cites do resolve. A link sweep needs a
  session whose policy allows those hosts.
- The live endpoint was not exercised: a paid call needs a funded payer, and the free trial is
  per-IP.

## Open, not mine to merge

#12 (a 4301-digit offer amount leaving `parse_challenge` as a bare `ValueError`), #13 (the
Paying section opening with an install that cannot run) and #14 (the payment hash sent over a
plain-HTTP redirect hop on the paid retry) are all open against this tree. #14 in particular
is the one I would have gone looking for: the paid retry forwards `X-PAYMENT` through
redirects, and a block hash is a bearer credential for that one paid call. It is already
written. Nothing in this note overlaps them.
