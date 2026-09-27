"""Payers: the one place money can move.

A payer is any object with ``pay(offer) -> str`` that sends ``offer.amount_raw``
raw to ``offer.pay_to`` and returns the 64-hex Nano block hash of that send.
The retriever never holds a key and never builds a block; it only calls this.

There is no default payer. Without one, a 402 is reported and the retriever
returns ``[]``: nothing is ever paid unless you plugged a payer in.
"""

from __future__ import annotations

import importlib
import os
from typing import Optional, Protocol, runtime_checkable

from .terms import PaymentOffer


@runtime_checkable
class Payer(Protocol):
    def pay(self, offer: PaymentOffer) -> str:
        """Send exactly ``offer.amount_raw`` raw to ``offer.pay_to``; return the send block's hash."""
        ...


_default_payer: Optional[Payer] = None


def set_default_payer(payer: Optional[Payer]) -> None:
    """Install the payer every retriever instance uses (GPT Researcher builds the instances itself)."""
    if payer is not None and not callable(getattr(payer, "pay", None)):
        raise TypeError("a payer needs a pay(offer) -> block_hash method")
    global _default_payer
    _default_payer = payer


def payer_from_env(env=None) -> Optional[Payer]:
    """``X402_PAYER=package.module:factory`` -> ``factory()``. Unset means no payer."""
    env = os.environ if env is None else env
    spec = (env.get("X402_PAYER") or "").strip()
    if not spec:
        return None
    module_name, _, attr = spec.partition(":")
    if not module_name or not attr:
        raise ValueError("X402_PAYER must look like 'package.module:factory'")
    factory = getattr(importlib.import_module(module_name), attr)
    # A class or a factory function is called; an object that already has pay() is used as is.
    payer = factory() if isinstance(factory, type) or not callable(getattr(factory, "pay", None)) else factory
    if not callable(getattr(payer, "pay", None)):
        raise TypeError(f"X402_PAYER={spec} did not produce an object with pay(offer)")
    return payer


def resolve_payer(explicit: Optional[Payer] = None) -> Optional[Payer]:
    if explicit is not None:
        return explicit
    if _default_payer is not None:
        return _default_payer
    return payer_from_env()


class Feeless402Payer:
    """Pays from a local feeless402 (``nano_pay``) wallet.

    feeless402 is what ``openai-agents-nano-x402`` wraps, so installing that
    package gives you this payer's dependency. The seed stays in the wallet file
    on your machine; blocks are signed locally and only the signed block is
    published.

    ``X402_PAYER=gpt_researcher_x402_retriever.payers:Feeless402Payer``
    """

    def __init__(self, wallet_path: Optional[str] = None, rpc=None):
        try:
            from nano_pay.rpc import RPC
            from nano_pay.wallet import Wallet
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise ImportError(
                "Feeless402Payer needs feeless402: pip install feeless402 "
                "(or openai-agents-nano-x402, which depends on it)"
            ) from exc
        from pathlib import Path

        self._wallet = (Wallet(Path(wallet_path)) if wallet_path else Wallet()).load()
        self._rpc = rpc if rpc is not None else RPC()

    def pay(self, offer: PaymentOffer) -> str:
        return self._wallet.send(self._rpc, offer.pay_to, offer.amount_raw)
