# gpt-researcher-x402-retriever - audit 2026-10-13

Base: `24f8757` (main, "refuse a 402 reached through a plain-HTTP redirect hop (#11)").
Environment: Python 3.11, `pip install -e '.[test]'`. Suite before any change: **76 passed, 3 skipped**.

## Checked

- The whole XNO spend path end to end: `PayPerCallSearch.search` -> `_fetch` -> `parse_challenge`
  -> `_check_offer` -> `payer.pay` -> the paid retry.
- `terms.py`: `valid_nano_address` (shape **and** blake2b checksum), `RAW_AMOUNT` /
  `NANO_ADDRESS` / `BLOCK_HASH` anchored with `\Z` rather than `$`, `xno_to_raw`'s 60-digit
  context with `Inexact` trapped, `parse_challenge`'s scheme/network/asset filter.
- `payers.py`: no default payer, `X402_PAYER` resolution, `Feeless402Payer` sending
  `offer.amount_raw` raw unmodified.
- `_results`: scheme filter, dedupe, `query_domains` host filter.
- The README's "What happens on a call" list against the code, step by step.

## Found and fixed

**The paid retry sent `X-PAYMENT` over a plain-HTTP redirect hop.**
`retriever.py:133` (pre-fix) was `paid = self._get({"X-PAYMENT": self.last_payment})`. Every
redirect guard in this repository is on the *challenge* request (`_check_offer`, hardened on
10-04 and again by #11 on 10-12). The retry had none, and it carries what the challenge does
not: the hash of a send that has already confirmed on-chain, which is the single-use claim on
the call just paid for. `requests` follows redirects by default, re-sends a custom header on
every hop, and does not refuse a scheme downgrade.

Measured against a local TLS endpoint that answered the **paid** request with a 302 to a local
plain-HTTP origin, driven through this class with a real `requests.Session`:

    endpoint        : https://localhost:34605/api/v1/web-search
    payer invoked   : 1 time -- 0.0001 XNO moved, irreversibly
    plain-HTTP origin saw X-PAYMENT:
                      B749B757EE750FC9AEA72F33CB429EACCD2ABEC9F2CCF59BF17AFAC304C9A58F
    results returned: the plain-HTTP origin's body, served as the paid answer

Two harms, both after the money moved. Anyone on the path at that hop reads the block hash and
can present it to the real seller, consuming the call the agent paid for -- the agent then gets
"paid but not served" for a search someone else received. And an unauthenticated origin's body
came back as the results a research run goes on to cite.

Fix: `_get_paid` follows the paid retry's redirects itself with `allow_redirects=False`, checking
each `Location`'s scheme **before** the header is sent, and refuses a non-HTTPS hop with a message
that names the block hash for a refund claim. A redirect that stays on HTTPS is still followed and
served, matching the existing controls for the challenge request. Hops are capped at
`MAX_PAID_REDIRECTS = 5`.

Tests: `test_the_payment_hash_is_never_sent_over_a_plain_http_redirect_hop` (fails on the pre-fix
tree, naming the leaked hash) and the control
`test_a_paid_retry_redirected_within_https_is_still_followed_and_served`. The two test fakes that
call `session.get` gained the `allow_redirects` keyword. Suite after: **78 passed, 3 skipped**.
The real-server probe above re-run against the fixed tree: `plain-HTTP origin saw X-PAYMENT: []`.

## Found, NOT changed

- `xno_to_raw` carries the maintainer's own note that correcting it moved an amount on the spend
  path in both directions and so "is not a routine's to merge". Read, re-derived, left alone.
- `parse_challenge` raises on the first Nano `exact` offer whose amount or `payTo` is malformed
  instead of trying the next entry in `accepts`. That refuses a payable challenge that also
  carries a broken one. It fails safe (nothing is paid) and the strictness looks deliberate;
  changing it would widen what gets paid, so it is noted rather than touched.

## Could not verify

- The live seller path (`tests/test_live.py`, `X402_LIVE=1`) was not run: it needs a funded wallet
  and a real payment. The 3 skipped tests are those plus the two optional-dependency cases.
- `Feeless402Payer` against a real `feeless402` wallet: the extra is not installed here, so only
  its call shape is covered (by the existing monkeypatched test).
