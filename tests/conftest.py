import json
from pathlib import Path

import pytest

import gpt_researcher_x402_retriever.payers as payers

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


class FakeResponse:
    def __init__(self, status_code, body=None, headers=None, text=None, url=None, history=()):
        self.status_code = status_code
        self._body = body
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self._text = text
        #: Where the answer actually came FROM. ``requests`` sets this to the final
        #: URL after following redirects, which is not necessarily the one asked for.
        self.url = url
        #: The hops the redirect passed THROUGH, which is the only place they appear:
        #: ``url`` above is the final one only. ``requests`` fills this with the
        #: response objects of each redirect, so a hop is read as ``hop.url``.
        self.history = [_Hop(u) for u in history]

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class _Hop:
    """One redirect hop, as ``requests`` leaves it in ``response.history``."""

    def __init__(self, url):
        self.url = url


class FakeSession:
    """Answers GETs from a script; records every request. No network."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None, allow_redirects=True):
        self.calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {}),
                           "allow_redirects": allow_redirects})
        if not self.responses:
            raise AssertionError("unexpected extra request")
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class RecordingPayer:
    def __init__(self, block_hash="A" * 64):
        self.block_hash = block_hash
        self.offers = []

    def pay(self, offer):
        self.offers.append(offer)
        return self.block_hash


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("X402_PAYER", "X402_SEARCH_URL", "X402_MAX_XNO", "X402_PAY_TO"):
        monkeypatch.delenv(name, raising=False)
    payers.set_default_payer(None)
    yield
    payers.set_default_payer(None)
