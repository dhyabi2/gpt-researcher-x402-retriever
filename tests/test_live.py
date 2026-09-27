"""One live check against the real endpoint. Opt in: X402_LIVE=1 pytest -m live.

It never pays. It calls the endpoint without payment until it answers 402 (the
seller grants 5 free trial calls per IP per day first, so this can use up to 5 of
yours), then checks the 402 carries valid Nano x402 terms that agree with the
seller's published /.well-known/x402.
"""
import os

import pytest
import requests

from gpt_researcher_x402_retriever import PayPerCallSearch
from gpt_researcher_x402_retriever.retriever import DEFAULT_ENDPOINT, USER_AGENT
from gpt_researcher_x402_retriever.terms import parse_challenge

pytestmark = [pytest.mark.live, pytest.mark.skipif(os.environ.get("X402_LIVE") != "1", reason="set X402_LIVE=1")]
WELL_KNOWN = "https://extract.paypercall.dev/.well-known/x402"


def test_endpoint_answers_402_with_valid_terms_matching_the_published_manifest():
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    manifest = requests.get(WELL_KNOWN, headers=headers, timeout=20).json()
    published = next(r for r in manifest["resources"] if r["url"] == DEFAULT_ENDPOINT)["accepts"][0]

    response = None
    for _ in range(6):  # up to 5 trial answers, then the 402
        response = requests.get(DEFAULT_ENDPOINT, params={"q": "x402 nano"}, headers=headers, timeout=20)
        if response.status_code == 402:
            break
        assert response.status_code == 200
    assert response.status_code == 402, "no 402 after the trial allowance"

    offer = parse_challenge(response.json(), response.headers.get("payment-required"), DEFAULT_ENDPOINT)
    assert offer.pay_to == published["payTo"]
    assert offer.amount_raw == int(published["amount"])
    assert (offer.scheme, offer.network, offer.asset) == ("exact", "nano:mainnet", "XNO")
    header_offer = parse_challenge(None, response.headers["payment-required"], DEFAULT_ENDPOINT)
    assert header_offer == offer

    # And the retriever, with no payer, turns that 402 into [] without paying.
    r = PayPerCallSearch("x402 nano")
    assert r.search() == []
    assert r.last_offer == offer and r.last_payment is None
