"""Reading an x402 payment challenge (HTTP 402) into one checked offer.

Only what this retriever can act on is accepted: the ``exact`` scheme on
``nano:mainnet`` in ``XNO``, with an integer raw amount and a well-formed Nano
address to pay. Anything else is refused before a payer is ever asked.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal, Inexact, localcontext
from typing import Any, Mapping, Optional

RAW_PER_XNO = 10**30
#: Digits for the XNO -> raw conversion, in a context of its own. A price carries up to 30
#: decimal places and a raw amount reaches 39 digits; `decimal`'s process-global default is 28,
#: which is fewer than either. 60 covers both with room, and `Inexact` is trapped rather than
#: rounded, because a value rounded at the 28th significant digit is still an INTEGER and so
#: `to_integral_value()` could not see that anything had been lost.
CONVERSION_PREC = 60
NANO_ADDRESS = re.compile(r"^(nano|xrb)_[13][13456789abcdefghijkmnopqrstuwxyz]{59}$")
BLOCK_HASH = re.compile(r"^[0-9A-Fa-f]{64}$")
#: An amount in raw is ASCII decimal digits. ``str.isdigit()`` is not that test: it is also true
#: for "\u00b2", which ``int()`` then refuses, and for "\u0663", which is not what a seller wrote.
RAW_AMOUNT = re.compile(r"^[0-9]+$")
_B32 = {c: i for i, c in enumerate("13456789abcdefghijkmnopqrstuwxyz")}


def valid_nano_address(address: Any) -> bool:
    """Shape **and** checksum of a ``nano_``/``xrb_`` address.

    The shape alone is not enough to pay to. A Nano address is a 260-bit base32 encoding of the
    32-byte public key followed by a 5-byte blake2b digest of that key, and the digest is the only
    thing that catches a single mistyped character: ``NANO_ADDRESS`` matches such a string just as
    happily, and a send to it is irreversible and lands on an account nobody holds the key to.
    """
    if not isinstance(address, str) or not NANO_ADDRESS.match(address):
        return False
    body = address.split("_", 1)[1]            # 60 characters = 4 padding bits + 256 + 40
    number = 0
    for char in body:
        number = (number << 5) | _B32[char]    # every character is in the alphabet: the regex checked
    public_key = ((number >> 40) & (1 << 256) - 1).to_bytes(32, "big")
    checksum = number & (1 << 40) - 1
    want = int.from_bytes(hashlib.blake2b(public_key, digest_size=5).digest()[::-1], "big")
    return checksum == want


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
    """Exact decimal XNO -> raw. Floats are read through ``str`` so ``0.0001`` means 0.0001.

    The multiplication runs in its own 60-digit context with ``Inexact`` trapped, and not in the
    process-global one. ``decimal.getcontext().prec`` defaults to **28** significant digits; an XNO
    amount carries up to **30** decimal places and a raw amount reaches **39** digits, so the
    multiplication rounded -- and the ``to_integral_value()`` test below could not catch it, because
    *a value rounded at the 28th significant digit is still an integer*.

    This function's one caller is ``PayPerCallSearch.max_raw``, the ceiling a price is refused
    above, so a rounded answer was a ceiling nobody set. Both caps below are 29 decimal places --
    a legal whole number of raw, which the conversion owes back unchanged. Measured on the shipped
    code:

    ===================================  ====================================  ==============
    ``X402_MAX_XNO``                     the cap it produced                   against the ask
    ===================================  ====================================  ==============
    ``99.99999999999999999999999999999`` ``100000000000000000000000000000000``  **+10 raw**
    ``1.00000000000000000000000000006``  ``1000000000000000000000000000000``    **-60 raw**
    ===================================  ====================================  ==============

    The first is the one that costs something: the ceiling came back *above* what was configured,
    so a price up to 10 raw over it was paid, and no refusal fired because the rounded value was
    still an integer. The second refuses a price the operator meant to allow.

    Both now convert exactly. A cap too long to convert even at 60 digits gets its own named
    refusal rather than being rounded into a number nobody chose, and a sub-raw cap keeps the
    ``to_integral_value()`` refusal below. ``localcontext`` restores the caller's precision and
    traps, so importing this library does not change arithmetic in the program that imported it.

    Note for a reviewer: this is **not** a refusal-only change. The ``+10 raw`` case narrows the
    cap, but the ``-60 raw`` case widens it -- a price between ``10**30`` and ``10**30 + 60`` raw
    is paid under a cap of ``1.00000000000000000000000000006`` where it was refused before. It is
    the correct number either way, and it is the number the operator asked for, but it moves an
    amount on the spend path and so is not a routine's to merge.
    """
    try:
        amount = Decimal(str(value).strip())
    except Exception as exc:  # decimal.InvalidOperation and friends
        raise ValueError(f"not a decimal XNO amount: {value!r}") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"not a non-negative XNO amount: {value!r}")
    with localcontext() as context:
        context.prec = CONVERSION_PREC
        context.traps[Inexact] = True
        try:
            raw = amount * RAW_PER_XNO
        except Inexact:
            raise ValueError(
                f"too many digits to convert to raw exactly: {value!r}"
            ) from None
        # `to_integral_value` signals neither Inexact nor Rounded, so the trap above does not
        # stand in for this test: a sub-raw amount still gets its own named refusal.
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
        if not isinstance(amount, str) or not RAW_AMOUNT.match(amount) or int(amount) <= 0:
            raise TermsError(f"offer amount is not a positive integer raw string: {amount!r}")
        pay_to = accept.get("payTo")
        if not valid_nano_address(pay_to):
            raise TermsError(f"offer payTo is not a Nano address (shape and checksum): {pay_to!r}")
        timeout = accept.get("maxTimeoutSeconds")
        return PaymentOffer(
            resource=str(resource_url or request_url),
            pay_to=pay_to,
            amount_raw=int(amount),
            max_timeout_seconds=int(timeout) if isinstance(timeout, int) else None,
        )
    raise TermsError("402 has no `exact` / `nano:mainnet` / `XNO` offer")
