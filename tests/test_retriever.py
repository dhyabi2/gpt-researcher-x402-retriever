import base64
import json
import logging
from decimal import Context, localcontext

import pytest

from gpt_researcher_x402_retriever import PayPerCallSearch, set_default_payer
from gpt_researcher_x402_retriever.retriever import DEFAULT_ENDPOINT
from gpt_researcher_x402_retriever.terms import (
    BLOCK_HASH,
    NANO_ADDRESS,
    RAW_AMOUNT,
    TermsError,
    parse_challenge,
    valid_nano_address,
    xno_to_raw,
)

from conftest import FakeResponse, FakeSession, RecordingPayer, fixture

VEND_PAY_TO = "nano_1yo6c1t64ahfjdw1dxizmbbnpdmbrckwhw9phbg5pdkeubrizga4qhnjmnx7"
PRICE_RAW = 10**26  # 0.0001 XNO


def ok():
    return FakeResponse(200, fixture("web_search_200.json"))


def challenge(**headers):
    return FakeResponse(402, fixture("web_search_402.json"), headers=headers)


def test_trial_answer_returns_gpt_researcher_shape_without_paying():
    session, payer = FakeSession(ok()), RecordingPayer()
    results = PayPerCallSearch("x402", payer=payer, session=session).search(max_results=3)
    assert len(results) == 3
    for r in results:
        assert set(r) == {"href", "body", "title"}
        assert r["href"].startswith("https://")
    assert payer.offers == []
    assert session.calls[0]["url"] == DEFAULT_ENDPOINT
    assert session.calls[0]["params"] == {"q": "x402"}
    assert "X-PAYMENT" not in session.calls[0]["headers"]


def test_declares_that_results_need_scraping():
    # Results are links + snippets; GPT Researcher must fetch the page for a citation.
    assert PayPerCallSearch.requires_scraping is True


def test_402_without_a_payer_pays_nothing_and_returns_empty():
    session = FakeSession(challenge())
    r = PayPerCallSearch("x402", session=session)
    assert r.search() == []
    assert len(session.calls) == 1  # no retry, nothing paid
    assert r.last_offer.pay_to == VEND_PAY_TO
    assert r.last_offer.amount_raw == PRICE_RAW
    assert r.last_payment is None


def test_402_with_a_payer_pays_the_exact_terms_once_and_retries_with_the_hash():
    session, payer = FakeSession(challenge(), ok()), RecordingPayer("b" * 64)
    r = PayPerCallSearch("x402", payer=payer, session=session)
    results = r.search(max_results=2)
    assert len(results) == 2
    assert len(payer.offers) == 1
    offer = payer.offers[0]
    assert (offer.pay_to, offer.amount_raw, offer.network, offer.asset, offer.scheme) == (
        VEND_PAY_TO, PRICE_RAW, "nano:mainnet", "XNO", "exact")
    assert session.calls[1]["headers"]["X-PAYMENT"] == "b" * 64
    assert session.calls[1]["params"] == session.calls[0]["params"]
    assert r.last_payment == "b" * 64


def test_paid_but_not_served_never_pays_twice():
    refused = FakeResponse(402, {"error": "payment_invalid"}, headers={"x-payment-message": "Block not found"})
    session, payer = FakeSession(challenge(), refused), RecordingPayer()
    r = PayPerCallSearch("x402", payer=payer, session=session)
    assert r.search() == []
    assert len(payer.offers) == 1
    assert len(session.calls) == 2
    assert r.last_payment == "A" * 64  # kept, for a claim against the seller


def test_price_above_the_cap_is_refused_before_the_payer_is_asked():
    session, payer = FakeSession(challenge()), RecordingPayer()
    assert PayPerCallSearch("x402", payer=payer, session=session, max_xno="0.00005").search() == []
    assert payer.offers == []


def test_cap_from_environment(monkeypatch):
    monkeypatch.setenv("X402_MAX_XNO", "0.00001")
    session, payer = FakeSession(challenge()), RecordingPayer()
    assert PayPerCallSearch("x402", payer=payer, session=session).search() == []
    assert payer.offers == []


def test_pinned_pay_to_refuses_a_different_address(monkeypatch):
    monkeypatch.setenv("X402_PAY_TO", "nano_3t6k35gi95xu6tergt6p69ck76ogmitsa8mnijtpxm9fkcm736xtoncuohr3")
    session, payer = FakeSession(challenge()), RecordingPayer()
    assert PayPerCallSearch("x402", payer=payer, session=session).search() == []
    assert payer.offers == []


def test_pinned_pay_to_matching_pays():
    session, payer = FakeSession(challenge(), ok()), RecordingPayer()
    assert PayPerCallSearch("x402", payer=payer, session=session, pay_to=VEND_PAY_TO).search()
    assert len(payer.offers) == 1


def test_never_pays_over_plain_http():
    session, payer = FakeSession(challenge()), RecordingPayer()
    r = PayPerCallSearch("x402", payer=payer, session=session, endpoint="http://search.example/api")
    assert r.search() == []
    assert payer.offers == []


def test_a_payer_that_returns_no_block_hash_is_not_retried():
    session, payer = FakeSession(challenge()), RecordingPayer("not-a-hash")
    assert PayPerCallSearch("x402", payer=payer, session=session).search() == []
    assert len(session.calls) == 1


def test_default_payer_is_used_when_none_is_passed():
    payer = RecordingPayer()
    set_default_payer(payer)
    session = FakeSession(challenge(), ok())
    assert PayPerCallSearch("x402", session=session).search()
    assert len(payer.offers) == 1


def test_set_default_payer_rejects_an_object_without_pay():
    with pytest.raises(TypeError):
        set_default_payer(object())


def test_payer_loaded_from_environment(monkeypatch):
    import types, sys
    mod = types.ModuleType("my_test_payer_mod")
    made = []

    class MyPayer:
        def pay(self, offer):
            made.append(offer)
            return "C" * 64

    mod.MyPayer = MyPayer
    monkeypatch.setitem(sys.modules, "my_test_payer_mod", mod)
    monkeypatch.setenv("X402_PAYER", "my_test_payer_mod:MyPayer")
    session = FakeSession(challenge(), ok())
    assert PayPerCallSearch("x402", session=session).search()
    assert len(made) == 1 and session.calls[1]["headers"]["X-PAYMENT"] == "C" * 64


def test_query_domains_filter_results_and_scope_the_query():
    session = FakeSession(ok())
    results = PayPerCallSearch("x402 site:old.example", query_domains=["wikipedia.org"], session=session).search()
    assert results and all("wikipedia.org" in r["href"] for r in results)
    assert session.calls[0]["params"]["q"] == "x402 site:wikipedia.org"


def test_accepts_the_extra_keywords_gpt_researcher_passes():
    session = FakeSession(ok())
    r = PayPerCallSearch(query="x402", headers={}, query_domains=None, websocket=None, researcher=None, session=session)
    assert r.search()


@pytest.mark.parametrize("response", [
    FakeResponse(500, {"error": "boom"}),
    FakeResponse(200, None),
    FakeResponse(200, {"results": "nope"}),
    FakeResponse(200, ["not", "a", "dict"]),
    ConnectionError("down"),
])
def test_failures_return_empty_never_raise(response):
    assert PayPerCallSearch("x402", session=FakeSession(response)).search() == []


def test_malformed_items_are_skipped():
    body = {"results": [None, {"href": "javascript:alert(1)"}, {"href": "garbage"}, {"url": "https://a.example/x", "snippet": "s"},
                        {"href": "https://a.example/x"}, {"link": "http://b.example/", "title": "B"}]}
    results = PayPerCallSearch("q", session=FakeSession(FakeResponse(200, body))).search()
    assert [r["href"] for r in results] == ["https://a.example/x", "http://b.example/"]


def test_terms_from_payment_required_header_when_body_is_not_json():
    body = fixture("web_search_402.json")
    header = base64.b64encode(json.dumps({"x402Version": 2, "resource": body["resource"], "accepts": body["accepts"]}).encode()).decode()
    offer = parse_challenge(None, header, DEFAULT_ENDPOINT)
    assert (offer.pay_to, offer.amount_raw) == (VEND_PAY_TO, PRICE_RAW)
    assert offer.resource == DEFAULT_ENDPOINT


@pytest.mark.parametrize("mutate", [
    lambda a: a.update(network="base"),
    lambda a: a.update(asset="USDC"),
    lambda a: a.update(scheme="upto"),
])
def test_offers_on_other_rails_are_not_payable(mutate):
    body = fixture("web_search_402.json")
    mutate(body["accepts"][0])
    with pytest.raises(TermsError):
        parse_challenge(body, None, DEFAULT_ENDPOINT)


@pytest.mark.parametrize("amount", ["0", "-1", "1e26", 1e26, "0.0001", "", "\u00b2", "\u0663", "100\n"])
def test_amount_must_be_a_positive_integer_raw_string(amount):
    """The last two are `str.isdigit()` but not raw.

    `"\u00b2"` is `isdigit()` and not `int()`-able, so the old check reached `int(amount)` and let a
    bare `ValueError` out of a function whose documented failure is `TermsError` - and `TermsError`
    subclasses `ValueError`, so `except TermsError` does not catch it.
    """
    body = fixture("web_search_402.json")
    body["accepts"][0]["amount"] = amount
    with pytest.raises(TermsError):
        parse_challenge(body, None, DEFAULT_ENDPOINT)


@pytest.mark.parametrize("pay_to", ["", "nano_short", "0xabc", VEND_PAY_TO + "x", VEND_PAY_TO.replace("nano_1", "nano_2")])
def test_pay_to_must_be_a_nano_address(pay_to):
    body = fixture("web_search_402.json")
    body["accepts"][0]["payTo"] = pay_to
    with pytest.raises(TermsError):
        parse_challenge(body, None, DEFAULT_ENDPOINT)


def _one_character_changed(address, index):
    return address[:index] + ("4" if address[index] != "4" else "5") + address[index + 1:]


@pytest.mark.parametrize("index", [12, 40, 64])   # key, key, last checksum character
def test_pay_to_with_a_broken_checksum_is_refused_before_the_payer_is_asked(index):
    """One mistyped character keeps the shape and breaks the checksum.

    A Nano address carries a 5-byte blake2b digest of its own public key for exactly this reason.
    Without checking it, a typo in a seller's 402 is paid to an account nobody holds the key to,
    and a Nano send cannot be reversed.
    """
    pay_to = _one_character_changed(VEND_PAY_TO, index)
    assert NANO_ADDRESS.match(pay_to), "the shape check alone still accepts this"
    body = fixture("web_search_402.json")
    body["accepts"][0]["payTo"] = pay_to
    with pytest.raises(TermsError):
        parse_challenge(body, None, DEFAULT_ENDPOINT)

    session, payer = FakeSession(FakeResponse(402, body), ok()), RecordingPayer("b" * 64)
    assert PayPerCallSearch("x402", payer=payer, session=session).search() == []
    assert payer.offers == []


def test_xno_to_raw_is_exact():
    assert xno_to_raw("0.0001") == PRICE_RAW
    assert xno_to_raw(0.0001) == PRICE_RAW
    assert xno_to_raw("1") == 10**30
    for bad in ("-1", "abc", "1e-31", "nan"):
        with pytest.raises(ValueError):
            xno_to_raw(bad)


def test_feeless402_payer_sends_exact_raw_from_the_local_wallet(monkeypatch):
    import sys, types
    sent = []

    class Wallet:
        def __init__(self, path=None):
            self.path = path

        def load(self):
            return self

        def send(self, rpc, to, raw):
            sent.append((rpc, to, raw))
            return "D" * 64

    class RPC:
        pass

    pkg = types.ModuleType("nano_pay")
    wallet_mod = types.ModuleType("nano_pay.wallet")
    rpc_mod = types.ModuleType("nano_pay.rpc")
    wallet_mod.Wallet, rpc_mod.RPC = Wallet, RPC
    for name, mod in (("nano_pay", pkg), ("nano_pay.wallet", wallet_mod), ("nano_pay.rpc", rpc_mod)):
        monkeypatch.setitem(sys.modules, name, mod)

    from gpt_researcher_x402_retriever import Feeless402Payer
    monkeypatch.setenv("X402_PAYER", "gpt_researcher_x402_retriever.payers:Feeless402Payer")
    session = FakeSession(challenge(), ok())
    assert PayPerCallSearch("x402", session=session).search()
    assert len(sent) == 1 and sent[0][1:] == (VEND_PAY_TO, PRICE_RAW)
    assert isinstance(Feeless402Payer().pay.__self__, Feeless402Payer)


# -- the 402 must come from somewhere it is safe to pay ----------------------
#
# `_check_offer` refuses to "pay over a non-HTTPS endpoint", but it read the
# scheme off `self.endpoint` -- the URL that was ASKED FOR. `requests` follows
# redirects by default and does not refuse a scheme downgrade. Measured
# 2026-10-04 against a local TLS server redirecting to a local plain-HTTP one:
#
#     requested : https://localhost:44709/api/v1/web-search
#     final url : http://127.0.0.1:33733/api/v1/web-search
#     history   : [302]
#     status    : 402
#     payTo     : nano_1banexkcf...   <- read from the PLAIN-HTTP responder
#
# So the challenge -- and the Nano address that gets paid -- can come from a
# different origin over plain HTTP while the guard still reads `https` off the
# configured endpoint and allows the send. `X402_PAY_TO` would catch it, but it
# is optional and unset by default.


def test_a_402_that_arrived_over_plain_http_is_not_paid():
    # The configured endpoint is HTTPS; the answer came from somewhere else, over
    # plain HTTP, exactly as `requests` leaves it after a 302.
    body = fixture("web_search_402.json")
    downgraded = FakeResponse(402, body, url="http://evil.example/api/v1/web-search")
    session, payer = FakeSession(downgraded), RecordingPayer()
    search = PayPerCallSearch("q", payer=payer, session=session)
    assert search.search() == []
    assert payer.offers == [], "nothing may be sent for a 402 that arrived over plain HTTP"
    assert search.last_payment is None
    # Only the one request was made: there is no paid retry to make.
    assert len(session.calls) == 1


def test_a_402_from_another_https_origin_is_still_paid_and_the_reason_is_recorded():
    # The control, so the refusal above cannot be mistaken for "any redirect is
    # refused". A redirect that stays on HTTPS is still paid: whoever answered
    # holds a valid certificate for that name, and `X402_PAY_TO` is the knob for
    # pinning the address. Narrowing that further would refuse a seller that
    # legitimately redirects to a CDN host, so it is deliberately left alone.
    body = fixture("web_search_402.json")
    moved = FakeResponse(402, body, url="https://cdn.paypercall.dev/api/v1/web-search")
    session, payer = FakeSession(moved, ok()), RecordingPayer("c" * 64)
    search = PayPerCallSearch("q", payer=payer, session=session)
    assert len(search.search()) > 0
    assert [o.amount_raw for o in payer.offers] == [PRICE_RAW]


def test_the_https_guard_still_refuses_a_plainly_http_endpoint():
    # The pre-existing half of the guard, which must keep holding: a response with
    # no `url` at all (anything that is not a `requests.Response`) falls back to the
    # configured endpoint rather than skipping the check.
    body = fixture("web_search_402.json")
    session, payer = FakeSession(FakeResponse(402, body)), RecordingPayer()
    search = PayPerCallSearch("q", payer=payer, session=session, endpoint="http://plain.example/search")
    assert search.search() == []
    assert payer.offers == []


# --- the spend cap was rounded at 28 digits -------------------------------------------------
#
# `xno_to_raw` ran the XNO -> raw multiplication in the PROCESS-GLOBAL decimal context, whose
# `prec` defaults to 28 significant digits, against a raw amount that reaches 39. Its one caller
# is `PayPerCallSearch.max_raw`, the ceiling a price is refused above, so a rounded answer was a
# ceiling nobody set -- and `to_integral_value()` could not catch the rounding, because a value
# rounded at the 28th significant digit is still an integer.
#
# Every law below runs under `decimal_default_context`, a fresh `Context()` (prec 28, the
# library default). Without it these laws only pass or fail by accident of import order:
# `nanopy` -- imported by the optional `feeless402` payer -- sets the PROCESS-GLOBAL `prec` to 40
# at import, and 40 digits happen to be enough, so in a full suite with `feeless402` installed
# the old code passed them too. Pinning the context makes them measure this module, not that one.


@pytest.fixture
def decimal_default_context():
    with localcontext(Context()) as context:
        assert context.prec == 28
        yield context


under_default_precision = pytest.mark.usefixtures("decimal_default_context")


def exact_raw(decimal_xno: str) -> int:
    """The raw amount a cap names, computed from its digits with no Decimal in the way."""
    whole, _, frac = decimal_xno.partition(".")
    assert len(frac) <= 30
    return int(whole or 0) * 10**30 + int(frac.ljust(30, "0") or 0)


@under_default_precision
def test_a_cap_is_not_rounded_up_above_what_was_configured():
    """The direction that costs something: the cap came back 10 raw ABOVE the ask.

    29 decimal places is a legal whole number of raw, so this cap is one an operator may set and
    the conversion owes them the number they wrote. The shipped code answered
    ``100000000000000000000000000000000`` -- exactly 100 XNO -- so a price up to 10 raw above the
    configured ceiling was paid, and no refusal fired because the rounded value was still an
    integer.
    """
    cap = "99.99999999999999999999999999999"          # 29 decimal places
    assert xno_to_raw(cap) == exact_raw(cap) == 99999999999999999999999999999990
    assert xno_to_raw(cap) < 10**32


@under_default_precision
def test_a_cap_at_full_raw_precision_converts_exactly_and_not_sixty_raw_short():
    cap = "1.00000000000000000000000000006"           # 29 decimal places, a whole number of raw
    assert xno_to_raw(cap) == exact_raw(cap) == 1000000000000000000000000000060


@under_default_precision
def test_every_one_of_the_thirty_decimal_places_survives():
    cap = "1.000000000000000000000000000001"          # the 30th place is one raw
    assert xno_to_raw(cap) == 10**30 + 1


@under_default_precision
def test_a_cap_too_long_to_convert_exactly_is_refused():
    with pytest.raises(ValueError, match="too many digits to convert to raw exactly"):
        xno_to_raw("1." + "0" * 59 + "1")


@under_default_precision
def test_the_corrected_cap_reaches_the_retriever_that_reads_it():
    """The cap is read once, at construction, so the exactness has to land on `max_raw`."""
    session, payer = FakeSession(challenge()), RecordingPayer()
    retriever = PayPerCallSearch("x402", payer=payer, session=session,
                                 max_xno="99.99999999999999999999999999999")
    assert retriever.max_raw == 99999999999999999999999999999990
    assert payer.offers == []


@under_default_precision
def test_a_cap_that_cannot_be_converted_is_refused_at_construction():
    """Not reachable by rounding any more: too many digits is its own named refusal."""
    session, payer = FakeSession(challenge()), RecordingPayer()
    with pytest.raises(ValueError, match="too many digits to convert to raw exactly"):
        PayPerCallSearch("x402", payer=payer, session=session,
                         max_xno="1." + "0" * 59 + "1")
    assert payer.offers == []
    assert session.calls == []


def priced_challenge(amount_raw: int):
    body = fixture("web_search_402.json")
    body["accepts"][0]["amount"] = str(amount_raw)
    return FakeResponse(402, body)


@under_default_precision
def test_a_price_one_raw_over_an_exact_cap_is_still_refused():
    """The cap is exact, so the comparison at its boundary is exact too.

    The old code turned this cap into exactly 100 XNO, so a price one raw over the configured
    ceiling (and up to ten) was paid. It must be refused before the payer is asked.
    """
    cap_xno = "99.99999999999999999999999999999"     # 29 places: a whole number of raw
    cap_raw = 99999999999999999999999999999990
    session, payer = FakeSession(priced_challenge(cap_raw + 1)), RecordingPayer()
    search = PayPerCallSearch("x402", payer=payer, session=session, max_xno=cap_xno)
    assert search.search() == []
    assert payer.offers == []
    assert len(session.calls) == 1


@under_default_precision
def test_a_price_exactly_at_an_exact_cap_is_still_paid():
    """The other side of the same boundary: the cap itself is allowed."""
    cap_xno = "99.99999999999999999999999999999"
    cap_raw = 99999999999999999999999999999990
    session, payer = FakeSession(priced_challenge(cap_raw), ok()), RecordingPayer()
    assert PayPerCallSearch("x402", payer=payer, session=session, max_xno=cap_xno).search() != []
    assert [o.amount_raw for o in payer.offers] == [cap_raw]


# Controls: every cap that already converted exactly must be unchanged, so the refusal cannot
# quietly cost an operator a working configuration.


@under_default_precision
@pytest.mark.parametrize("cap", ["0.001", "0.0001", "0", "1", "100", "0.000000000000000000000000000001"])
def test_a_cap_that_already_converted_exactly_is_unchanged(cap):
    assert xno_to_raw(cap) == exact_raw(cap)


@under_default_precision
def test_the_default_cap_still_pays_the_live_vend_price():
    session, payer = FakeSession(challenge(), ok()), RecordingPayer()
    assert PayPerCallSearch("x402", payer=payer, session=session).search() != []
    assert [o.amount_raw for o in payer.offers] == [PRICE_RAW]


@under_default_precision
def test_a_negative_cap_is_still_refused():
    with pytest.raises(ValueError, match="not a non-negative XNO amount"):
        xno_to_raw("-1")


@under_default_precision
def test_a_sub_raw_cap_is_still_refused_not_rounded_to_one_raw():
    with pytest.raises(ValueError, match="more precision than 1 raw"):
        xno_to_raw("0.0000000000000000000000000000001")   # 31 places: a fraction of a raw


@under_default_precision
def test_the_decimal_context_does_not_leak_out_of_the_conversion():
    import decimal

    before_prec = decimal.getcontext().prec
    before_traps = dict(decimal.getcontext().traps)
    xno_to_raw("0.0001")
    assert decimal.getcontext().prec == before_prec
    assert dict(decimal.getcontext().traps) == before_traps


# -- a trailing newline is not part of an address, a hash or an amount -------------------------
#
# `re`'s `$` matches before a single trailing newline, so `NANO_ADDRESS.match`, `BLOCK_HASH.match`
# and `RAW_AMOUNT.match` all accepted one. Each pattern now ends in `\Z`.


def test_a_pay_to_with_a_trailing_newline_is_refused_and_does_not_raise_keyerror():
    """`valid_nano_address` is documented to return a bool, and it raised `KeyError('\n')`.

    `"nano_...\n"` matched `^...$`, so `valid_nano_address` ran its base32 loop over the newline
    and `_B32['\n']` raised - out of a function every caller uses as a guard, and out of
    `parse_challenge`, whose documented failure is `TermsError`. A seller whose 402 carries a
    newline-terminated `payTo` (a template, a hand-edited JSON) got a `KeyError` in place of the
    named refusal.
    """
    assert valid_nano_address(VEND_PAY_TO + "\n") is False

    body = fixture("web_search_402.json")
    body["accepts"][0]["payTo"] = VEND_PAY_TO + "\n"
    with pytest.raises(TermsError):
        parse_challenge(body, None, DEFAULT_ENDPOINT)

    session, payer = FakeSession(FakeResponse(402, body), ok()), RecordingPayer("b" * 64)
    assert PayPerCallSearch("x402", payer=payer, session=session).search() == []
    assert payer.offers == []


class HeaderCheckingSession(FakeSession):
    """A `FakeSession` that refuses an illegal header value, as `requests` does.

    `requests.PreparedRequest` raises `InvalidHeader` on a value containing a return character, so
    a hash with a trailing newline cannot reach the wire. It reaches that check only *after*
    `payer.pay` has sent the XNO, and `InvalidHeader`'s message does not say a payment was made.
    """

    def get(self, url, params=None, headers=None, timeout=None):
        for name, value in (headers or {}).items():
            if isinstance(value, str) and ("\n" in value or "\r" in value):
                raise ValueError(f"return character(s) in header value: {name}")
        return super().get(url, params=params, headers=headers, timeout=timeout)


def test_a_block_hash_with_a_trailing_newline_is_refused_before_it_reaches_a_header(caplog):
    session = HeaderCheckingSession(challenge(), ok())
    retriever = PayPerCallSearch("x402", payer=RecordingPayer("b" * 64 + "\n"), session=session)

    with caplog.at_level(logging.WARNING):
        assert retriever.search() == []
    assert len(session.calls) == 1              # the paid retry is never attempted
    # The refusal is this package's own, naming what was wrong. Before, the hash went into
    # `X-PAYMENT` and the only thing the operator saw was `requests` complaining about a header.
    assert "payer did not return a 64-hex Nano block hash" in caplog.text


def test_the_three_patterns_do_not_end_at_a_newline():
    assert not NANO_ADDRESS.match(VEND_PAY_TO + "\n")
    assert not BLOCK_HASH.match("a" * 64 + "\n")
    assert not RAW_AMOUNT.match("100\n")
