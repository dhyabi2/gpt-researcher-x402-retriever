import base64
import json

import pytest

from gpt_researcher_x402_retriever import PayPerCallSearch, set_default_payer
from gpt_researcher_x402_retriever.retriever import DEFAULT_ENDPOINT
from gpt_researcher_x402_retriever.terms import NANO_ADDRESS, TermsError, parse_challenge, xno_to_raw

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


@pytest.mark.parametrize("amount", ["0", "-1", "1e26", 1e26, "0.0001", "", "\u00b2", "\u0663"])
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
