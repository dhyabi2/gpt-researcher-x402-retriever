"""The one code path that moves money, run against the real feeless402 wallet.

``Feeless402Payer.pay`` is the only place this package spends XNO, and until now
nothing exercised it: ``test_retriever.py`` only checks that ``X402_PAYER``
resolves the class, and ``feeless402`` was not installed in CI, so the two lines
that call ``Wallet(...).load()`` and ``wallet.send(...)`` had never run.

These tests close that gap without a network and without spending anything. The
wallet is a throwaway with a fresh random seed in ``tmp_path``; the RPC is a stub
that answers the three calls ``Wallet.send`` makes and *records* the block
instead of broadcasting it. What is asserted is the part a unit test can own:
that the offer's payee and amount arrive in the signed block unchanged, that an
amount is integer raw throughout (1 XNO = 10**30 raw, so a float would lose
digits), and that a payment which cannot be made raises rather than returning a
hash nobody can check.

Skipped when ``feeless402`` is not installed, so the suite still runs without the
extra. ``test_signs_a_block_paying_exactly_the_offer`` computes real proof of work,
which takes a few seconds; it is the only test here that does.
"""
import pytest

from gpt_researcher_x402_retriever.terms import PaymentOffer

nanopy = pytest.importorskip("nanopy", reason="needs the feeless402 extra")
nano_pay_wallet = pytest.importorskip("nano_pay.wallet", reason="needs the feeless402 extra")

from gpt_researcher_x402_retriever.payers import Feeless402Payer  # noqa: E402

Wallet = nano_pay_wallet.Wallet
WalletError = nano_pay_wallet.WalletError

PAY_TO = "nano_1yo6c1t64ahfjdw1dxizmbbnpdmbrckwhw9phbg5pdkeubrizga4qhnjmnx7"
PRICE_RAW = 100000000000000000000000000  # 0.0001 XNO, the seller's advertised price
REPRESENTATIVE = "nano_3arg3asgtigae3xckabaaewkx3bzsh7nwz7jkmjos79ihyaxwphhm6qgjps4"


class StubRPC:
    """Answers only what ``Wallet.send`` asks, and broadcasts nothing."""

    def __init__(self, raw_balance=None):
        self.raw_balance = raw_balance
        self.processed = None

    def account_info(self, address):
        if self.raw_balance is None:
            return None  # unopened account
        return {
            "frontier": "A" * 64,
            "balance": str(self.raw_balance),
            "representative": REPRESENTATIVE,
        }

    def work_generate(self, root, difficulty):
        return None  # no work peer; the wallet solves it locally

    def process(self, block, subtype):
        self.processed = (block, subtype)
        return "B" * 64


def make_payer(tmp_path, rpc):
    path = tmp_path / "wallet.json"
    Wallet(path).create()  # fresh random seed, local file only
    return Feeless402Payer(wallet_path=str(path), rpc=rpc)


def offer(amount_raw=PRICE_RAW, pay_to=PAY_TO):
    return PaymentOffer(resource="https://search.paypercall.dev/search", pay_to=pay_to, amount_raw=amount_raw)


def test_constructs_against_the_real_wallet_without_touching_the_network(tmp_path):
    payer = make_payer(tmp_path, StubRPC())
    assert payer.pay.__self__ is payer


def test_an_unfundable_payment_raises_rather_than_returning_a_hash(tmp_path):
    """A payer that cannot pay must raise: a caller retries the 402 with X-PAYMENT."""
    payer = make_payer(tmp_path, StubRPC(raw_balance=None))
    with pytest.raises(WalletError, match="insufficient balance"):
        payer.pay(offer())


def test_a_balance_one_raw_short_is_still_refused(tmp_path):
    """The guard is on the exact integer, so one raw short of the price does not pay."""
    payer = make_payer(tmp_path, StubRPC(raw_balance=PRICE_RAW - 1))
    with pytest.raises(WalletError, match="insufficient balance"):
        payer.pay(offer())


def test_signs_a_block_paying_exactly_the_offer(tmp_path):
    """The payee and the amount in the signed block are the offer's, to the raw."""
    rpc = StubRPC(raw_balance=PRICE_RAW * 10)
    payer = make_payer(tmp_path, rpc)

    block_hash = payer.pay(offer())

    assert block_hash == "B" * 64  # whatever the node answered, returned unchanged
    block, subtype = rpc.processed
    assert subtype == "send"
    assert nanopy.Account(pk=block["link"]).addr == PAY_TO
    # A state block carries the balance left behind, so the amount sent is the difference.
    assert int(block["balance"]) == PRICE_RAW * 10 - PRICE_RAW
    assert block["signature"] and block["work"]
    # Guards the argument order of wallet.send(rpc, to_addr, raw_amt): the payee is
    # the offer's, not the wallet's own account.
    assert block["account"] != PAY_TO
