"""Reading an x402 payment challenge (HTTP 402) into one checked offer.

Only what this retriever can act on is accepted: the ``exact`` scheme on
``nano:mainnet`` in ``XNO``, with an integer raw amount and a well-formed Nano
address to pay. Anything else is refused before a payer is ever asked.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Optional

RAW_PER_XNO = 10**30
NANO_ADDRESS = re.compile(r"^(nano|xrb)_[13][13456789abcdefghijkmnopqrstuwxyz]{59}$")
BLOCK_HASH = re.compile(r"^[0-9A-Fa-f]{64}$")


class TermsError(ValueError):
    """The 402 did not carry terms this retriever can pay."""


@dataclass(frozen=True)
class PaymentOffer:
    """One payable offer from a 402 challenge. Amounts are integer raw (1 XNO = 10**30 raw)."""

    resource: str
    pay_to: str
    amount_raw: int
    network: str = "nano:mainnet"
    asset: str = "XNO"
    scheme: str = "exact"
    max_timeout_seconds: Optional[int] = None

    @property
    def amount_xno(self) -> Decimal:
        return Decimal(self.amount_raw) / Decimal(RAW_PER_XNO)


def xno_to_raw(value: Any) -> int:
    """Exact decimal XNO -> raw. Floats are read through ``str`` so ``0.0001`` means 0.0001."""
    try:
        amount = Decimal(str(value).strip())
    except Exception as exc:  # decimal.InvalidOperation and friends
        raise ValueError(f"not a decimal XNO amount: {value!r}") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"not a non-negative XNO amount: {value!r}")
    raw = amount * RAW_PER_XNO
    if raw != raw.to_integral_value():
        raise ValueError(f"more precision than 1 raw: {value!r}")
    return int(raw)


def _challenge_body(body: Any, header: Optional[str]) -> Mapping[str, Any]:
    if isinstance(body, Mapping) and isinstance(body.get("accepts"), list):
        return body
    if header:
        try:
            decoded = json.loads(base64.b64decode(header, validate=False))
        except (binascii.Error, ValueError) as exc:
            raise TermsError("PAYMENT-REQUIRED header is not base64 JSON") from exc
        if isinstance(decoded, Mapping) and isinstance(decoded.get("accepts"), list):
            return decoded
    raise TermsError("402 carries no x402 `accepts` list")


def parse_challenge(body: Any, payment_required_header: Optional[str], request_url: str) -> PaymentOffer:
    """Pick the Nano ``exact`` offer out of a 402 (JSON body first, then the ``PAYMENT-REQUIRED`` header)."""
    challenge = _challenge_body(body, payment_required_header)
    resource = challenge.get("resource")
    resource_url = resource.get("url") if isinstance(resource, Mapping) else None
    for accept in challenge["accepts"]:
        if not isinstance(accept, Mapping):
            continue
        if (accept.get("scheme"), accept.get("network"), accept.get("asset")) != ("exact", "nano:mainnet", "XNO"):
            continue
        amount = accept.get("amount")
        if not isinstance(amount, str) or not amount.isdigit() or int(amount) <= 0:
            raise TermsError(f"offer amount is not a positive integer raw string: {amount!r}")
        pay_to = accept.get("payTo")
        if not isinstance(pay_to, str) or not NANO_ADDRESS.match(pay_to):
            raise TermsError(f"offer payTo is not a Nano address: {pay_to!r}")
        timeout = accept.get("maxTimeoutSeconds")
        return PaymentOffer(
            resource=str(resource_url or request_url),
            pay_to=pay_to,
            amount_raw=int(amount),
            max_timeout_seconds=int(timeout) if isinstance(timeout, int) else None,
        )
    raise TermsError("402 has no `exact` / `nano:mainnet` / `XNO` offer")
