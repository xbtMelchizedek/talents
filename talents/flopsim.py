"""A simulated FLOP hash-lock escrow (the FLOP leg) until FLOP testnet is live.

Python port of pearl-diver's `simtclk`, itself a port of tclk's MemoryRail predicates and
cross-checked against tclk (commit 7d402db). tests/test_flopsim.py pins this port to the same
scenario results as the Go one. Nothing here touches a FLOP chain.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class LockTerms:
    contract: str
    statement: str
    amount: str
    asset: str
    payer: str
    payee: str
    claim_by_ms: int
    refund_after_ms: int
    lock: str = "hash"


@dataclass
class _Rec:
    terms: LockTerms
    status: str = "locked"
    secret: str = ""


def verify_secret(statement: str, secret: str) -> bool:
    try:
        p = bytes.fromhex(secret[2:] if secret.startswith("0x") else secret)
    except ValueError:
        return False
    return len(p) == 32 and "0x" + hashlib.sha256(p).hexdigest() == statement.lower()


class RailError(Exception):
    pass


@dataclass
class SimFlopRail:
    clock: Callable[[], int]
    locks: dict[str, _Rec] = field(default_factory=dict)

    def lock(self, t: LockTerms) -> str:
        if t.lock != "hash":
            raise RailError("only hash locks are simulated")
        if t.contract in self.locks:
            raise RailError(f"rail already holds a lock for {t.contract}")
        if self.clock() >= t.refund_after_ms:
            raise RailError("refusing to lock into an already-open refund window")
        self.locks[t.contract] = _Rec(t)
        return t.contract

    def verify_lock(self, t: LockTerms, ref: str) -> bool:
        h = self.locks.get(ref)
        return h is not None and h.status == "locked" and h.terms == t

    def _locked(self, ref: str, op: str) -> _Rec:
        h = self.locks.get(ref)
        if h is None:
            raise RailError(f"{op} on an unknown lock {ref}")
        if h.status != "locked":
            raise RailError(f"{op} on a {h.status} lock")
        return h

    def claim(self, ref: str, secret: str) -> None:
        h = self._locked(ref, "claim")
        if self.clock() >= h.terms.refund_after_ms:
            raise RailError("claim after refundAfterMs")
        if not verify_secret(h.terms.statement, secret):
            raise RailError("secret does not open the statement")
        h.status, h.secret = "claimed", secret.lower()

    def refund(self, ref: str) -> None:
        h = self._locked(ref, "refund")
        if self.clock() < h.terms.refund_after_ms:
            raise RailError("refund before refundAfterMs")
        h.status = "refunded"

    def status(self, ref: str) -> str:
        h = self.locks.get(ref)
        return h.status if h else ""
