"""Signed trade callouts with commit-reveal, and a deterministic integer track record.

Why commit-reveal: a caller who posts the call before entering can be front-run, and a caller who
posts after the fact can cherry-pick. So the agent first publishes a signed COMMIT (a hash of the call
plus a salt) before trading, then REVEALs the call. A verifier checks the hash, the signatures and the
order of timestamps. Commits that are never revealed still count against the caller (see `score`).

All numbers are ints (1e8 scale) and all rounding is truncation toward zero, so an SP1 program in
Rust can recompute the exact same record (milestone 2: prove it).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from . import didkey
from .units import to_e8

V_CALL = "talents.callout/1"
V_EXIT = "talents.exit/1"
DOMAIN = b"talents/1|"
SIDES = {"long": 1, "short": -1, "yes": 1, "no": -1}
CALL_FIELDS = {"v", "agent", "venue", "market", "side", "entry_px", "invalidation_px", "horizon_ms", "size_bucket",
               "ts_ms", "nonce"}


def canonical(d: dict[str, Any]) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def cid(d: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical(d)).hexdigest()


def commitment(call: dict[str, Any], salt: bytes) -> str:
    if len(salt) != 32:
        raise ValueError("salt must be 32 bytes")
    return "sha256:" + hashlib.sha256(DOMAIN + b"commit|" + canonical(call) + b"|" + salt).hexdigest()


def _sign(key, kind: str, ref: str) -> str:
    return key.sign(DOMAIN + kind.encode() + b"|" + ref.encode()).hex()


def check_call(call: dict[str, Any]) -> None:
    if set(call) != CALL_FIELDS or call["v"] != V_CALL:
        raise ValueError("callout fields must be exactly " + ", ".join(sorted(CALL_FIELDS)))
    if call["side"] not in SIDES:
        raise ValueError("side must be long, short, yes or no")
    if call["size_bucket"] not in ("xs", "s", "m", "l"):
        raise ValueError("size_bucket must be xs, s, m or l")
    e, inv = to_e8(call["entry_px"]), to_e8(call["invalidation_px"])
    if e <= 0 or inv <= 0:
        raise ValueError("prices must be positive")
    if SIDES[call["side"]] * (e - inv) <= 0:
        raise ValueError("invalidation must be on the losing side of entry")
    if not isinstance(call["horizon_ms"], int) or call["horizon_ms"] <= 0:
        raise ValueError("horizon_ms must be a positive int")


def make_commit(key, call: dict[str, Any], salt: bytes, ts_ms: int) -> dict[str, Any]:
    check_call(call)
    c = {"v": "talents.commit/1", "agent": call["agent"], "commit": commitment(call, salt), "ts_ms": ts_ms}
    return {**c, "sig": _sign(key, "commit", cid(c))}


def make_reveal(key, call: dict[str, Any], salt: bytes, commit_doc: dict[str, Any]) -> dict[str, Any]:
    c = {"v": "talents.reveal/1", "call": call, "salt": salt.hex(), "commit": commit_doc["commit"]}
    return {**c, "sig": _sign(key, "reveal", cid(c))}


def make_exit(key, agent: str, call_cid: str, exit_px: str, ts_ms: int) -> dict[str, Any]:
    e = {"v": V_EXIT, "agent": agent, "call": call_cid, "exit_px": exit_px, "ts_ms": ts_ms}
    return {**e, "sig": _sign(key, "exit", cid(e))}


def _body(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in doc.items() if k != "sig"}


def _sig_ok(doc: dict[str, Any], kind: str, did: str) -> bool:
    try:
        return didkey.verify(did, bytes.fromhex(doc["sig"]), DOMAIN + kind.encode() + b"|" + cid(_body(doc)).encode())
    except (KeyError, ValueError, TypeError):
        return False


@dataclass
class Record:
    agent: str
    calls: int = 0
    closed: int = 0
    wins: int = 0
    sum_bps: int = 0
    max_drawdown_bps: int = 0
    hidden: int = 0  # commits never revealed by their deadline
    rejected: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"agent": self.agent, "calls": self.calls, "closed": self.closed, "wins": self.wins,
                "sum_bps": self.sum_bps, "max_drawdown_bps": self.max_drawdown_bps, "hidden": self.hidden,
                "rejected": self.rejected}


def ret_bps(side: str, entry_e8: int, exit_e8: int) -> int:
    num = SIDES[side] * (exit_e8 - entry_e8) * 10_000
    q = abs(num) // entry_e8
    return q if num >= 0 else -q  # truncate toward zero (matches Rust integer division)


def score(agent: str, commits: list[dict], reveals: list[dict], exits: list[dict], now_ms: int,
          reveal_grace_ms: int = 86_400_000, hidden_penalty_bps: int = 1_000) -> Record:
    """Recompute an agent's record from its signed docs only. Order is by commit time, then commit hash."""
    rec = Record(agent)
    good_commits = {}
    for c in commits:
        if c.get("agent") != agent or not _sig_ok(c, "commit", agent):
            rec.rejected.append(f"commit {str(c.get('commit'))[:20]}: bad signature or agent")
            continue
        good_commits.setdefault(c["commit"], c)
    revealed: dict[str, dict] = {}
    for r in reveals:
        try:
            call, salt = r["call"], bytes.fromhex(r["salt"])
            check_call(call)
            ok = (call["agent"] == agent and _sig_ok(r, "reveal", agent) and r["commit"] in good_commits
                  and commitment(call, salt) == r["commit"] and call["ts_ms"] <= good_commits[r["commit"]]["ts_ms"])
        except (KeyError, ValueError, TypeError):
            ok = False
        if not ok:
            rec.rejected.append("reveal: does not open a signed commit from this agent")
            continue
        revealed.setdefault(r["commit"], call)
    exit_by_call: dict[str, dict] = {}
    for e in sorted(exits, key=lambda e: (e.get("ts_ms", 0), cid(_body(e)))):
        if e.get("agent") == agent and _sig_ok(e, "exit", agent):
            exit_by_call.setdefault(e["call"], e)  # the first signed exit counts; later ones cannot rewrite it
    cum = peak = 0
    for h, c in sorted(good_commits.items(), key=lambda kv: (kv[1]["ts_ms"], kv[0])):
        call = revealed.get(h)
        if call is None:
            if now_ms > c["ts_ms"] + reveal_grace_ms:
                rec.hidden += 1
                cum -= hidden_penalty_bps
            peak = max(peak, cum)
            rec.max_drawdown_bps = max(rec.max_drawdown_bps, peak - cum)
            continue
        rec.calls += 1
        e = exit_by_call.get(cid(call))
        if e is None or e["ts_ms"] < c["ts_ms"]:
            continue
        rec.closed += 1
        r = ret_bps(call["side"], to_e8(call["entry_px"]), to_e8(e["exit_px"]))
        rec.wins += r > 0
        rec.sum_bps += r
        cum += r
        peak = max(peak, cum)
        rec.max_drawdown_bps = max(rec.max_drawdown_bps, peak - cum)
    return rec
