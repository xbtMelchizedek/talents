"""FLOP (simulated rail) <-> HYPE (HashLockEscrow on a local anvil chain) end to end.

anvil runs with chain id 998 (HyperEVM testnet's id) purely so addresses and ids look like the real
target; it is a local, empty chain. Its accounts are anvil's PUBLIC default dev accounts.
"""
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

from conftest import ROOT
from talents import swap
from talents.flopsim import LockTerms, RailError, SimFlopRail

ANVIL = shutil.which("anvil") or str(Path.home() / ".foundry/bin/anvil")
pytestmark = pytest.mark.skipif(not Path(ANVIL).exists() or not swap.ARTIFACT.exists(),
                                reason="needs anvil and `forge build` in contracts/")


def _port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


@pytest.fixture
def chain():
    from web3 import Web3
    port = _port()
    proc = subprocess.Popen([ANVIL, "--port", str(port), "--chain-id", "998", "--silent"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    w3 = Web3(Web3.HTTPProvider(f"http://127.0.0.1:{port}"))
    for _ in range(100):
        if w3.is_connected():
            break
        time.sleep(0.1)
    bob, alice = w3.eth.accounts[0], w3.eth.accounts[1]
    esc = swap.load_escrow(w3, deployer=bob)
    yield w3, esc, bob, alice
    proc.terminate(); proc.wait(5)


def _now_s(w3):
    return w3.eth.get_block("latest")["timestamp"]


def _warp(w3, t):
    w3.provider.make_request("evm_setNextBlockTimestamp", [t]); w3.provider.make_request("evm_mine", [])


def _setup(w3, margin_ms=600_000, evm_window_s=3600, flop_window_ms=None):
    secret, st = swap.new_secret()
    now = _now_s(w3)
    t_b = now + evm_window_s
    t_a = flop_window_ms if flop_window_ms is not None else t_b * 1000 + margin_ms + 1
    offer = swap.Offer(flop_amount="25", hype_wei=10**17, flop_refund_after_ms=t_a, evm_refund_after_s=t_b,
                       margin_ms=margin_ms, statement=st)
    rail = SimFlopRail(clock=lambda: _now_s(w3) * 1000)
    terms = LockTerms(contract=st, statement=st, amount="25", asset="FLOP", payer="did:key:alice",
                      payee="did:key:bob", claim_by_ms=t_a, refund_after_ms=t_a)
    return secret, offer, rail, terms


def test_happy_path(chain):
    w3, esc, bob, alice = chain
    secret, offer, rail, terms = _setup(w3)
    ref = rail.lock(terms)
    lid = swap.bob_check_and_lock(w3, esc, rail, offer, ref, terms, bob, alice)
    before = w3.eth.get_balance(alice)
    swap.alice_verify_and_claim(w3, esc, offer, bob, alice, secret)
    assert w3.eth.get_balance(alice) > before  # got the HYPE (minus gas)
    s = swap.bob_extract_secret(w3, esc, lid)
    assert s == "0x" + secret.hex()
    rail.claim(ref, s)
    assert rail.status(ref) == "claimed"
    r = swap.receipt_summary(offer, ref, lid, "settled")
    assert r["outcome"] == "settled" and r["statement"] == offer.statement


def test_bob_refuses_bad_timing(chain):
    w3, esc, bob, alice = chain
    secret, offer, rail, terms = _setup(w3)
    bad = swap.Offer(**{**offer.__dict__, "flop_refund_after_ms": offer.evm_refund_after_s * 1000})  # no margin
    t2 = LockTerms(**{**terms.__dict__, "refund_after_ms": bad.flop_refund_after_ms, "claim_by_ms": bad.flop_refund_after_ms})
    ref = rail.lock(t2)
    with pytest.raises(swap.SwapRefused, match="timing"):
        swap.bob_check_and_lock(w3, esc, rail, bad, ref, t2, bob, alice)


def test_bob_refuses_mismatched_flop_lock(chain):
    w3, esc, bob, alice = chain
    secret, offer, rail, terms = _setup(w3)
    ref = rail.lock(LockTerms(**{**terms.__dict__, "amount": "24"}))  # Alice locked less than agreed
    with pytest.raises(swap.SwapRefused, match="agreed"):
        swap.bob_check_and_lock(w3, esc, rail, offer, ref, terms, bob, alice)


def test_alice_refuses_short_lock(chain):
    w3, esc, bob, alice = chain
    secret, offer, rail, terms = _setup(w3)
    rail.lock(terms)
    # Bob locks a smaller amount than offered: Alice's rebuilt id finds nothing, she won't reveal s
    esc.functions.lock(alice, swap.ZERO, offer.hype_wei - 1, bytes.fromhex(offer.statement[2:]),
                       offer.evm_refund_after_s).transact({"from": bob, "value": offer.hype_wei - 1})
    with pytest.raises(swap.SwapRefused, match="not as agreed"):
        swap.alice_verify_and_claim(w3, esc, offer, bob, alice, secret)


def test_alice_walks_away_both_refund(chain):
    w3, esc, bob, alice = chain
    secret, offer, rail, terms = _setup(w3)
    ref = rail.lock(terms)
    lid = swap.bob_check_and_lock(w3, esc, rail, offer, ref, terms, bob, alice)
    _warp(w3, offer.evm_refund_after_s)
    esc.functions.refund(lid).transact({"from": bob})
    with pytest.raises(RailError):
        rail.refund(ref)  # FLOP refund opens later than the HyperEVM one, by design
    _warp(w3, offer.flop_refund_after_ms // 1000 + 1)
    rail.refund(ref)
    assert rail.status(ref) == "refunded"


def test_alice_will_not_claim_near_deadline(chain):
    w3, esc, bob, alice = chain
    secret, offer, rail, terms = _setup(w3)
    ref = rail.lock(terms)
    swap.bob_check_and_lock(w3, esc, rail, offer, ref, terms, bob, alice)
    _warp(w3, offer.evm_refund_after_s - 30)
    with pytest.raises(swap.SwapRefused, match="too close"):
        swap.alice_verify_and_claim(w3, esc, offer, bob, alice, secret)


def test_bob_never_locks_alice_refunds(chain):
    w3, esc, bob, alice = chain
    secret, offer, rail, terms = _setup(w3)
    ref = rail.lock(terms)
    _warp(w3, offer.flop_refund_after_ms // 1000 + 1)
    rail.refund(ref)
    assert rail.status(ref) == "refunded"
