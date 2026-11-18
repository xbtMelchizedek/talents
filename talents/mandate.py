"""The mandate: the written policy an agent's keys trade under, checked before anything is signed.

Three layers enforce it (see docs/DESIGN.md):
  1. this engine, in the agent, before it builds or signs anything (soft, but always on);
  2. venue-native limits: a capped HL sub-account plus an API wallet that cannot withdraw, a Solana hot
     wallet that only holds the cap, or MandateTrader.sol on HyperEVM (hard, on-chain);
  3. simulation (Curve-Lab for pump.fun transactions; HL preflight against the live testnet book).

Milestone 1 refuses every mainnet network outright.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .units import notional_e8, to_e8

ALLOWED_NETWORKS_M1 = {"hl-testnet", "hyperevm-testnet", "solana-devnet", "flop-devnet", "flop-testnet",
                       "kalshi-demo", "local"}
VENUES = {"hl-perp", "hl-outcome", "pump", "kalshi", "polymarket", "htlc-swap"}


@dataclass
class Verdict:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    notional_e8: int = 0

    def deny(self, why: str) -> "Verdict":
        self.ok = False
        self.reasons.append(why)
        return self


@dataclass
class Mandate:
    agent: str  # did:key of the agent
    network: str
    venues: set[str]
    expires_ms: int
    per_order_usd_e8: int
    daily_usd_e8: int
    max_leverage: int = 1
    hl_assets: set[str] = field(default_factory=set)
    max_px_deviation_bps: int = 200  # vs mark
    reduce_only: bool = False
    pump_max_sol_in_e8: int = 0
    pump_max_slippage_bps: int = 300
    pump_mint_denylist: set[str] = field(default_factory=set)
    swap_max_usd_e8: int = 0
    paused: bool = False

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "Mandate":
        m = cls(
            agent=d["agent"], network=d["network"], venues=set(d["venues"]), expires_ms=int(d["expires_ms"]),
            per_order_usd_e8=to_e8(d["per_order_usd"]), daily_usd_e8=to_e8(d["daily_usd"]),
            max_leverage=int(d.get("max_leverage", 1)), hl_assets=set(d.get("hl_assets", [])),
            max_px_deviation_bps=int(d.get("max_px_deviation_bps", 200)), reduce_only=bool(d.get("reduce_only", False)),
            pump_max_sol_in_e8=to_e8(d.get("pump_max_sol_in", "0")),
            pump_max_slippage_bps=int(d.get("pump_max_slippage_bps", 300)),
            pump_mint_denylist=set(d.get("pump_mint_denylist", [])), swap_max_usd_e8=to_e8(d.get("swap_max_usd", "0")),
            paused=bool(d.get("paused", False)),
        )
        m.validate()
        return m

    @classmethod
    def load(cls, path: str | Path) -> "Mandate":
        return cls.from_json(json.loads(Path(path).read_text()))

    def validate(self) -> None:
        if self.network not in ALLOWED_NETWORKS_M1:
            raise ValueError(f"network {self.network!r} is not allowed in milestone 1 (testnet/devnet/local only)")
        bad = self.venues - VENUES
        if bad:
            raise ValueError(f"unknown venues {sorted(bad)}")
        if self.per_order_usd_e8 <= 0 or self.daily_usd_e8 < self.per_order_usd_e8:
            raise ValueError("need 0 < per_order_usd <= daily_usd")
        if not 1 <= self.max_leverage <= 50:
            raise ValueError("max_leverage must be 1..50")


@dataclass
class DayState:
    """New-risk notional already used today (the caller persists this per agent per UTC day)."""
    used_e8: int = 0


def _common(m: Mandate, venue: str, now_ms: int) -> Verdict:
    v = Verdict(ok=True)
    if m.paused:
        v.deny("mandate paused")
    if now_ms >= m.expires_ms:
        v.deny("mandate expired")
    if venue not in m.venues:
        v.deny(f"venue {venue} not in mandate")
    return v


def check_hl_order(m: Mandate, day: DayState, *, coin: str, is_buy: bool, px: str, sz: str, reduce_only: bool,
                   leverage: int, mark_px: str, now_ms: int, venue: str = "hl-perp") -> Verdict:
    v = _common(m, venue, now_ms)
    px_e8, sz_e8, mark_e8 = to_e8(px), to_e8(sz), to_e8(mark_px)
    if px_e8 <= 0 or sz_e8 <= 0 or mark_e8 <= 0:
        return v.deny("price, size and mark must be positive")
    if m.hl_assets and coin not in m.hl_assets:
        v.deny(f"{coin} not in hl_assets")
    if m.reduce_only and not reduce_only:
        v.deny("mandate is reduce-only")
    if venue == "hl-perp" and leverage > m.max_leverage:
        v.deny(f"leverage {leverage}x > {m.max_leverage}x")
    n = notional_e8(px_e8, sz_e8)
    v.notional_e8 = n
    if n > m.per_order_usd_e8:
        v.deny(f"order notional {n} > per-order cap {m.per_order_usd_e8} (USD e8)")
    if not reduce_only and day.used_e8 + n > m.daily_usd_e8:
        v.deny(f"daily new-risk cap: {day.used_e8} + {n} > {m.daily_usd_e8} (USD e8)")
    dev = abs(px_e8 - mark_e8) * 10_000 // mark_e8
    if dev > m.max_px_deviation_bps:
        v.deny(f"limit price {dev} bps from mark > {m.max_px_deviation_bps} bps")
    return v


def check_outcome_order(m: Mandate, day: DayState, *, outcome: int, side: int, px: str, sz: str, now_ms: int) -> Verdict:
    """HIP-4 outcome tokens are fully collateralized and priced in [0, 1] quote per share."""
    v = _common(m, "hl-outcome", now_ms)
    px_e8, sz_e8 = to_e8(px), to_e8(sz)
    if not 0 < px_e8 < 10**8:
        v.deny("outcome price must be strictly between 0 and 1")
    if side not in (0, 1):
        v.deny("side must be 0 (first side, usually Yes) or 1")
    n = notional_e8(px_e8, sz_e8)
    v.notional_e8 = n
    if n > m.per_order_usd_e8:
        v.deny(f"order notional {n} > per-order cap {m.per_order_usd_e8}")
    if day.used_e8 + n > m.daily_usd_e8:
        v.deny(f"daily cap: {day.used_e8} + {n} > {m.daily_usd_e8}")
    return v


def check_pump_intent(m: Mandate, *, action: str, mint: str, sol_in: str, quoted_out: str, min_out: str,
                      now_ms: int) -> Verdict:
    """A pump.fun buy/sell intent, before Curve-Lab simulates the built transaction."""
    v = _common(m, "pump", now_ms)
    if action not in ("buy", "sell"):
        return v.deny("pump: only buy/sell; creating coins is never allowed")
    if mint in m.pump_mint_denylist:
        v.deny("mint on denylist")
    sol_e8, q_e8, min_e8 = to_e8(sol_in), to_e8(quoted_out), to_e8(min_out)
    if action == "buy" and sol_e8 > m.pump_max_sol_in_e8:
        v.deny(f"SOL in {sol_e8} > cap {m.pump_max_sol_in_e8} (e8)")
    if q_e8 <= 0 or min_e8 <= 0 or min_e8 > q_e8:
        return v.deny("need 0 < min_out <= quoted_out")
    slip = (q_e8 - min_e8) * 10_000 // q_e8
    if slip > m.pump_max_slippage_bps:
        v.deny(f"slippage tolerance {slip} bps > {m.pump_max_slippage_bps} bps")
    return v


def check_swap(m: Mandate, *, usd_value: str, now_ms: int) -> Verdict:
    v = _common(m, "htlc-swap", now_ms)
    if to_e8(usd_value) > m.swap_max_usd_e8:
        v.deny("swap above swap_max_usd")
    return v
