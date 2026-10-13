"""A keyless, pay-per-call web-search retriever for GPT Researcher.

It calls an x402 web-search endpoint (by default Vend's
``https://search.paypercall.dev/api/v1/web-search``). There is no API key: a
call is either inside the seller's free trial, or answered with HTTP 402 and
x402 terms. On a 402 the retriever asks a pluggable payer to send the exact
amount in Nano (XNO), then retries once with the block hash in ``X-PAYMENT``.

The ``gpt_researcher.retrievers`` entry point is declared for the day GPT
Researcher reads one. It does not read one today (see ``register`` below), so
``register()`` is what makes ``RETRIEVER=paypercall`` resolve.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests

from .payers import Payer, resolve_payer
from .terms import BLOCK_HASH, PaymentOffer, TermsError, parse_challenge, xno_to_raw

try:  # Optional: subclass GPT Researcher's documented contract when it is installed.
    from gpt_researcher.retrievers.base import BaseRetriever as _Base
except Exception:  # pragma: no cover - depends on the environment
    _Base = object  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "https://search.paypercall.dev/api/v1/web-search"
#: Refuse any single call priced above this. Vend charges 0.0001 XNO per search.
DEFAULT_MAX_XNO = "0.001"
USER_AGENT = "gpt-researcher-x402-retriever/0.1 (+https://github.com/dhyabi2/gpt-researcher-x402-retriever)"
_SITE = re.compile(r"\bsite:\S+", re.IGNORECASE)
#: The statuses ``requests`` follows as a redirect.
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
#: How many hops the paid retry follows. ``requests``' own ceiling is 30; a paid
#: search needs a couple, and a shorter leash costs nothing.
MAX_PAID_REDIRECTS = 5


class PayPerCallSearch(_Base):
    """GPT Researcher retriever over an x402 (Nano/XNO) web-search endpoint.

    Results are links plus snippets, so ``requires_scraping = True``: GPT
    Researcher fetches each page itself and keeps a real citation for it.

    Configuration (environment, because GPT Researcher constructs retrievers):

    * ``X402_SEARCH_URL``: endpoint, default Vend's web search.
    * ``X402_MAX_XNO``: the most one call may cost, default ``0.001``.
    * ``X402_PAY_TO``: optional; if set, only this Nano address is ever paid.
    * ``X402_PAYER``: ``package.module:factory`` returning a payer. Unset = never pay.
    """

    requires_scraping = True

    def __init__(
        self,
        query: str,
        query_domains: Optional[List[str]] = None,
        headers: Optional[Dict[str, str]] = None,
        *,
        payer: Optional[Payer] = None,
        endpoint: Optional[str] = None,
        max_xno: Optional[str] = None,
        pay_to: Optional[str] = None,
        session: Optional[Any] = None,
        timeout: float = 20.0,
        **_ignored: Any,  # GPT Researcher also passes websocket=, researcher=
    ):
        self.query = query
        self.query_domains = [d.strip().lower().strip(".") for d in (query_domains or []) if d and d.strip()]
        self.endpoint = endpoint or os.environ.get("X402_SEARCH_URL") or DEFAULT_ENDPOINT
        self.max_raw = xno_to_raw(max_xno or os.environ.get("X402_MAX_XNO") or DEFAULT_MAX_XNO)
        self.pay_to = pay_to or os.environ.get("X402_PAY_TO") or None
        self.timeout = timeout
        self._payer = payer
        self._session = session or requests.Session()
        #: The last offer seen, paid or not (for callers that want to show the price).
        self.last_offer: Optional[PaymentOffer] = None
        #: The block hash this instance paid with, if it paid.
        self.last_payment: Optional[str] = None

    # -- contract ---------------------------------------------------------

    def search(self, max_results: int = 7) -> List[Dict[str, Any]]:
        """Return ``[{href, body, title}]``; ``[]`` on any failure, never an exception."""
        try:
            payload = self._fetch()
        except Exception as exc:  # one failing provider must not abort a research run
            logger.warning("x402 search failed: %s", exc)
            return []
        return _results(payload, max_results, self.query_domains)

    # -- internals --------------------------------------------------------

    def _query_text(self) -> str:
        text = _SITE.sub("", self.query).strip() or self.query
        if self.query_domains:
            text = f"{text} " + " OR ".join(f"site:{d}" for d in self.query_domains)
        return text

    def _request(
        self,
        url: str,
        params: Optional[Dict[str, str]],
        extra_headers: Optional[Dict[str, str]] = None,
        *,
        allow_redirects: bool,
    ):
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT, **(extra_headers or {})}
        return self._session.get(
            url, params=params, headers=headers, timeout=self.timeout, allow_redirects=allow_redirects,
        )

    def _get(self, extra_headers: Optional[Dict[str, str]] = None):
        """The unpaid request. Redirects are followed, and `_check_offer` checks where it went."""
        return self._request(self.endpoint, {"q": self._query_text()}, extra_headers, allow_redirects=True)

    def _get_paid(self, block_hash: str):
        """The paid retry, which follows a redirect only while it stays on HTTPS.

        `_check_offer` refuses a 402 that arrived over, or through, a plain-HTTP hop,
        because the `payTo` address it carries could have been rewritten there. The paid
        retry one line later had no such guard, and it carries something the challenge
        did not: `X-PAYMENT`, the hash of a send that has already confirmed on-chain,
        which is the single-use claim on the call just paid for. `requests` follows
        redirects by default, keeps a custom header across them, and does not refuse a
        scheme downgrade. Measured 2026-10-13 against a local TLS endpoint that answered
        the *paid* request with a 302 to a local plain-HTTP origin, driven through this
        class with a real `requests.Session`:

            endpoint        : https://localhost:34605/api/v1/web-search
            payer invoked   : 1 time -- 0.0001 XNO moved, irreversibly
            plain-HTTP origin saw X-PAYMENT:
                              B749B757EE750FC9AEA72F33CB429EACCD2ABEC9F2CCF59BF17AFAC304C9A58F
            results returned: the plain-HTTP origin's body, served as the paid answer

        Two harms, both after the money moved. Anyone on the path at that hop reads the
        block hash and can present it to the real seller, consuming the call the agent
        paid for -- the agent then gets "paid but not served" for a search someone else
        received. And the body of an unauthenticated origin is handed back as the results
        a research run goes on to cite.

        So the hop is checked BEFORE the header is sent, not after: a redirect that stays
        on HTTPS is still followed, exactly as a redirect chain that stays on HTTPS is
        still paid above. Only the hop that cannot be trusted with the hash is refused.
        """
        headers = {"X-PAYMENT": block_hash}
        url: str = self.endpoint
        params: Optional[Dict[str, str]] = {"q": self._query_text()}
        for _ in range(MAX_PAID_REDIRECTS + 1):
            response = self._request(url, params, headers, allow_redirects=False)
            if response.status_code not in _REDIRECT_CODES:
                return response
            location = response.headers.get("location")
            if not location:
                return response
            # `requests` resolves a relative Location against the URL it asked for and
            # carries no params over: the Location is the whole of the next request.
            url = urljoin(getattr(response, "url", None) or url, location)
            params = None
            if urlparse(url).scheme != "https":
                raise RuntimeError(
                    f"paid (block {block_hash}) and the paid request was redirected to "
                    f"{urlparse(url).scheme or 'a schemeless url'} ({url}) -- not sending the payment "
                    f"hash over a hop anyone on the path could read. Keep the hash for a refund claim."
                )
        raise RuntimeError(
            f"paid (block {block_hash}) but the paid request was still being redirected after "
            f"{MAX_PAID_REDIRECTS} hops. Keep the hash for a refund claim."
        )

    def _fetch(self) -> Any:
        response = self._get()
        if response.status_code == 200:
            return response.json()
        if response.status_code != 402:
            raise RuntimeError(f"search endpoint answered HTTP {response.status_code}")

        offer = parse_challenge(_json_or_none(response), response.headers.get("payment-required"), self.endpoint)
        self.last_offer = offer
        self._check_offer(
            offer,
            getattr(response, "url", None),
            [h.url for h in (getattr(response, "history", None) or []) if isinstance(getattr(h, "url", None), str)],
        )
        payer = resolve_payer(self._payer)
        if payer is None:
            logger.info(
                "x402 search needs payment (%s XNO to %s) and no payer is configured; returning no results",
                offer.amount_xno, offer.pay_to,
            )
            return None

        block_hash = payer.pay(offer)
        if not isinstance(block_hash, str) or not BLOCK_HASH.match(block_hash):
            raise RuntimeError("payer did not return a 64-hex Nano block hash")
        self.last_payment = block_hash
        paid = self._get_paid(self.last_payment)
        if paid.status_code != 200:
            # Paid but not served: never pay again for this call. Keep the hash for a refund claim.
            raise RuntimeError(
                f"paid (block {self.last_payment}) but the endpoint answered HTTP {paid.status_code}: "
                f"{paid.headers.get('x-payment-message', '')}"
            )
        return paid.json()

    def _check_offer(
        self,
        offer: PaymentOffer,
        challenge_url: Optional[str] = None,
        redirect_urls: Optional[List[str]] = None,
    ) -> None:
        if offer.amount_raw > self.max_raw:
            raise TermsError(f"price {offer.amount_xno} XNO is above X402_MAX_XNO")
        if self.pay_to is not None and offer.pay_to != self.pay_to:
            raise TermsError(f"402 asks to pay {offer.pay_to}, not the pinned X402_PAY_TO")
        if urlparse(self.endpoint).scheme != "https":
            raise TermsError("refusing to pay over a non-HTTPS endpoint")
        # And the same test on where the 402 actually CAME FROM, which is not
        # necessarily what was asked for. `requests` follows redirects by default and
        # does not refuse a scheme downgrade; measured 2026-10-04 against a local TLS
        # server answering 302 to a local plain-HTTP one, a GET for
        # `https://localhost/api/v1/web-search` returned a 402 whose `url` was
        # `http://127.0.0.1:.../api/v1/web-search` and whose `payTo` was the
        # plain-HTTP responder's. Checking only `self.endpoint` let that be paid: the
        # Nano address came from an origin anyone on the path could have rewritten,
        # and a Nano send is irreversible. `X402_PAY_TO` would have caught it, but it
        # is optional and unset by default, so this cannot rely on it.
        #
        # A redirect that STAYS on HTTPS is still paid. Refusing a cross-origin HTTPS
        # redirect would refuse a seller that legitimately moves to a CDN host, and
        # whoever answers over HTTPS holds a certificate for that name; pinning the
        # address is what `X402_PAY_TO` is for.
        if challenge_url is not None and urlparse(challenge_url).scheme != "https":
            raise TermsError(
                f"the 402 was served over {urlparse(challenge_url).scheme or 'no'} scheme "
                f"({challenge_url}) -- refusing to pay an address that did not arrive over HTTPS"
            )
        # And every hop on the way, not just where it ended up. `response.url` is the
        # FINAL url only; the hops a redirect passed THROUGH live solely in
        # `response.history`, so a chain `https -> http -> https` leaves `.url` on
        # https and both checks above pass -- while whoever is on the path at the
        # plain-http hop chose the origin that then served the 402 and its `payTo`.
        # Measured 2026-10-12 against a local TLS -> plain-HTTP -> TLS chain, through
        # this class with a real `requests.Session`:
        #
        #     endpoint seen by guard : https://localhost:40955/api/v1/web-search
        #     challenge_url seen     : https://localhost:33313/api/v1/web-search
        #     PAID?                  : True
        #     amount_raw             : 100000000000000000000000000
        #     pay_to                 : nano_1yo6c1t64ahfjdw1dxizmbb...
        #
        # 0.0001 XNO, irreversibly, to an address chosen at the plain-http hop. That
        # is the harm the check above was added for, reached by a chain instead of a
        # single downgrade. A chain that stays on HTTPS throughout is still paid.
        for hop in redirect_urls or []:
            if urlparse(hop).scheme != "https":
                raise TermsError(
                    f"the 402 was reached through a {urlparse(hop).scheme or 'schemeless'} redirect "
                    f"({hop}) -- refusing to pay an address chosen on a hop anyone on the path could rewrite"
                )


def register() -> bool:
    """Make the name ``paypercall`` resolve to this retriever. Call it before ``GPTResearcher(...)``.

    GPT Researcher resolves a retriever name in ``gpt_researcher.actions.retriever.get_retriever``,
    which is a hardcoded ``match`` ending in ``case _: return None`` (checked against 0.15.1, the
    current release). It reads no entry points. Worse, ``get_retrievers`` then does
    ``get_retriever(r) or get_default_retriever()``, so an unrecognised name does not raise — it
    silently becomes Tavily, which needs the API key this package exists to avoid.

    Returns True once the name resolves, False if GPT Researcher is not installed. Idempotent, and
    it leaves every other name with its original answer, so a combined
    ``RETRIEVER=paypercall,duckduckgo`` keeps working.
    """
    try:
        from gpt_researcher.actions import retriever as _gptr
    except Exception:  # pragma: no cover - depends on the environment
        logger.warning("gpt_researcher is not importable; RETRIEVER=paypercall will not resolve")
        return False
    return _install(_gptr)


def _install(module) -> bool:
    """Wrap ``module.get_retriever`` so ``paypercall`` answers this class. Separated so it is testable."""
    previous = getattr(module, "get_retriever", None)
    if previous is None:
        logger.warning("%s has no get_retriever; RETRIEVER=paypercall will not resolve", module.__name__)
        return False
    if getattr(previous, "_paypercall_registered", False):
        return True

    def get_retriever(retriever: str):
        return PayPerCallSearch if retriever == "paypercall" else previous(retriever)

    get_retriever._paypercall_registered = True  # type: ignore[attr-defined]
    get_retriever.__doc__ = previous.__doc__
    module.get_retriever = get_retriever
    return True


def _json_or_none(response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _host_matches(url: str, domains: List[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in domains)


def _results(payload: Any, max_results: int, domains: List[str]) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    items = payload.get("results")
    if not isinstance(items, list):
        return []
    out: List[Dict[str, Any]] = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        url = item.get("href") or item.get("url") or item.get("link")
        if not isinstance(url, str) or urlparse(url).scheme not in ("http", "https") or url in seen:
            continue
        if domains and not _host_matches(url, domains):
            continue
        seen.add(url)
        out.append({
            "href": url,
            "body": str(item.get("snippet") or item.get("body") or ""),
            "title": str(item.get("title") or ""),
        })
        if len(out) >= max_results:
            break
    return out
