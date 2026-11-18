"""FLOP <-> HYPE/USDC atomic swap: FLOP leg on the simulated rail, HyperEVM leg on HashLockEscrow.

Alice has FLOP and wants HYPE (or USDC) on HyperEVM. Bob has HYPE and wants FLOP.
  1 Alice picks a 32-byte secret s, H = sha256(s), and locks FLOP for Bob until T_A (ms).
  2 Bob checks the FLOP lock exactly and the timing rule, then locks HYPE for Alice until T_B (s).
  3 Alice rebuilds the HyperEVM lock id from the agreed terms, checks it on chain, claims with s.
    The Claimed event publishes s.
  4 Bob reads s from the event and claims the FLOP before T_A.
Timing rule: T_A >= T_B * 1000 + margin_ms, so Bob always has `margin_ms` to use s on FLOP.
No bridge, no custodian. Local anvil + simulated FLOP only in milestone 1.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .flopsim import LockTerms, SimFlopRail

ARTIFACT = Path(__file__).resolve().parents[1] / "contracts" / "out" / "HashLockEscrow.sol" / "HashLockEscrow.json"
ZERO = "0x0000000000000000000000000000000000000000"


class SwapRefused(Exception):
    pass


def timing_ok(flop_refund_after_ms: int, evm_refund_after_s: int, margin_ms: int) -> bool:
    return flop_refund_after_ms >= evm_refund_after_s * 1000 + margin_ms


def new_secret() -> tuple[bytes, str]:
    s = os.urandom(32)
    return s, "0x" + hashlib.sha256(s).hexdigest()


def load_escrow(w3, address: str | None = None, deployer: str | None = None):
    art = json.loads(ARTIFACT.read_text())
    if address is None:
        c = w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"]["object"])
        tx = c.constructor().transact({"from": deployer})
        address = w3.eth.wait_for_transaction_receipt(tx)["contractAddress"]
    return w3.eth.contract(address=address, abi=art["abi"])


@dataclass
class Offer:
    flop_amount: str
    hype_wei: int
    flop_refund_after_ms: int
    evm_refund_after_s: int
    margin_ms: int
    statement: str  # 0x sha256


def bob_check_and_lock(w3, esc, rail: SimFlopRail, offer: Offer, flop_ref: str, expected: LockTerms,
                       bob: str, alice_evm: str) -> bytes:
    """Bob locks HYPE only if the FLOP lock is exactly what was agreed and the timing rule holds."""
    if not rail.verify_lock(expected, flop_ref):
        raise SwapRefused("FLOP lock does not match the agreed terms")
    if expected.statement != offer.statement or expected.refund_after_ms != offer.flop_refund_after_ms:
        raise SwapRefused("FLOP lock terms differ from the offer")
    if not timing_ok(offer.flop_refund_after_ms, offer.evm_refund_after_s, offer.margin_ms):
        raise SwapRefused("timing rule: FLOP refund must open at least margin_ms after the HyperEVM refund")
    now_ms = w3.eth.get_block("latest")["timestamp"] * 1000
    if now_ms + offer.margin_ms >= offer.flop_refund_after_ms:
        raise SwapRefused("too close to the FLOP refund to start")
    st = bytes.fromhex(offer.statement[2:])
    tx = esc.functions.lock(alice_evm, ZERO, offer.hype_wei, st, offer.evm_refund_after_s).transact(
        {"from": bob, "value": offer.hype_wei})
    w3.eth.wait_for_transaction_receipt(tx)
    return esc.functions.lockId(bob, alice_evm, ZERO, offer.hype_wei, st, offer.evm_refund_after_s).call()


def alice_verify_and_claim(w3, esc, offer: Offer, bob: str, alice_evm: str, secret: bytes,
                           min_confirmations: int = 1, safety_s: int = 60) -> str:
    """Alice never trusts an id Bob sends: she rebuilds it from the terms and reads the lock."""
    st = bytes.fromhex(offer.statement[2:])
    lid = esc.functions.lockId(bob, alice_evm, ZERO, offer.hype_wei, st, offer.evm_refund_after_s).call()
    payer, payee, token, refund_after, amount, statement, status = esc.functions.locks(lid).call()
    if status != 1 or payee != alice_evm or amount != offer.hype_wei or statement != st or token != ZERO:
        raise SwapRefused("HyperEVM lock missing or not as agreed")
    now = w3.eth.get_block("latest")["timestamp"]
    if now + safety_s >= refund_after:
        raise SwapRefused("too close to the HyperEVM refund; claiming now could race Bob's refund")
    tx = esc.functions.claim(lid, secret).transact({"from": alice_evm})
    return w3.eth.wait_for_transaction_receipt(tx)["transactionHash"].hex()


def bob_extract_secret(w3, esc, lock_id: bytes, from_block: int = 0) -> str | None:
    logs = esc.events.Claimed().get_logs(from_block=from_block, argument_filters={"id": lock_id})
    return ("0x" + logs[0]["args"]["preimage"].hex()) if logs else None


def receipt_summary(offer: Offer, flop_ref: str, lock_id: bytes, outcome: str) -> dict[str, Any]:
    """The facts an agentwork receipt for this swap would carry (signing is left to agentwork)."""
    return {"kind": "talents.swap/1", "pair": "FLOP/HYPE", "flop_amount": offer.flop_amount,
            "hype_wei": str(offer.hype_wei), "statement": offer.statement, "flop_lock": flop_ref,
            "hyperevm_lock": "0x" + lock_id.hex(), "outcome": outcome}
