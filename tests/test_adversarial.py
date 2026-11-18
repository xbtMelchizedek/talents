"""Adversarial and invariant checks: composition, exact ids, two-method notional."""
from __future__ import annotations

from talents import callout as co
from talents import didkey
from talents.mandate import DayState, Mandate, check_hl_order, check_pump_intent, check_swap
from talents.units import SCALE, deviation_bps, notional_e8, to_e8
from test_mandate import BASE, NOW, hl, m

T0 = 1_795_000_000_000


def test_asset_id_is_exact():
    """A valid size and price on a case-shifted or padded ticker must not pass."""
    assert hl(m(), coin="BTC").ok
    for coin in ("btc", "BTC ", " BTC", "Btc", "BTC\x00"):
        v = hl(m(), coin=coin)
        assert not v.ok, coin
        assert any("not in hl_assets" in r for r in v.reasons)


def test_two_rights_still_deny_when_composed():
    """Each field is individually plausible; together they must still fail the other checks."""
    mm = m()
    # In-band price, legal size, but the venue is missing — deny.
    v = check_swap(m(venues=["hl-perp"]), usd_value="1", now_ms=NOW)
    assert not v.ok and any("venue" in r for r in v.reasons)
    # In-band price, legal size, expired mandate — deny.
    v = hl(mm, now_ms=mm.expires_ms)
    assert not v.ok and any("expired" in r for r in v.reasons)
    # Creating a coin looks like a "buy" with a cap, but create is never allowed.
    ok = dict(mint="So1Test", sol_in="0.25", quoted_out="1000000", min_out="980000", now_ms=NOW)
    assert check_pump_intent(mm, action="buy", **ok).ok
    v = check_pump_intent(mm, action="create", **ok)
    assert not v.ok and any("creating coins is never allowed" in r for r in v.reasons)


def test_notional_two_methods_agree():
    """The Python helper, the long-hand product, and an accepted verdict must match."""
    px, sz = "64000", "0.003"
    a = notional_e8(to_e8(px), to_e8(sz))
    b = to_e8(px) * to_e8(sz) // SCALE
    c = 64_000 * 3 * 10**5  # 0.003 * 1e8 = 300_000; 64000e8 * 300_000 / 1e8 = 192e8
    assert a == b == c == 192 * SCALE
    v = hl(m(), px=px, sz=sz)
    assert v.ok and v.notional_e8 == a
    assert deviation_bps(to_e8("64000"), to_e8("63986")) == abs(to_e8("64000") - to_e8("63986")) * 10_000 // to_e8("63986")


def test_ok_verdict_satisfies_every_cap():
    """An accepted order is inside every cap at once; fuzz a few independent knobs."""
    mm = Mandate.from_json(BASE)
    for px, sz, used, reduce_only, coin in (
        ("64000", "0.003", 0, False, "BTC"),
        ("63986", "0.001", 900 * SCALE, True, "ETH"),
        ("63000", "0.003", 0, False, "BTC"),
    ):
        v = hl(mm, DayState(used_e8=used), coin=coin, px=px, sz=sz, reduce_only=reduce_only, mark_px="63986")
        assert v.ok, v.reasons
        assert coin in mm.hl_assets
        assert v.notional_e8 == notional_e8(to_e8(px), to_e8(sz))
        assert v.notional_e8 <= mm.per_order_usd_e8
        if not reduce_only:
            assert used + v.notional_e8 <= mm.daily_usd_e8
        assert deviation_bps(to_e8(px), to_e8("63986")) <= mm.max_px_deviation_bps


def test_callout_signature_binds_canonical_bytes():
    """A signature and a commitment bind the exact canonical body; a swapped field opens nothing."""
    k = didkey.key_from_seed(b"\x11" * 32)
    agent = didkey.did_of_key(k)
    call = {
        "v": co.V_CALL, "agent": agent, "venue": "hl-perp", "market": "BTC", "side": "long",
        "entry_px": "100", "invalidation_px": "95", "horizon_ms": 3_600_000, "size_bucket": "s",
        "ts_ms": T0, "nonce": "01",
    }
    salt = b"\x22" * 32
    commit = co.make_commit(k, call, salt, T0 + 1)
    reveal = co.make_reveal(k, call, salt, commit)
    other = {**call, "market": "ETH"}
    assert co.commitment(other, salt) != commit["commit"]
    honest = co.score(agent, [commit], [reveal], [], now_ms=T0 + 1_000)
    assert honest.calls == 1 and honest.closed == 0 and honest.hidden == 0 and not honest.rejected
    tampered = {**reveal, "call": other}
    rec = co.score(agent, [commit], [tampered], [], now_ms=T0 + 1_000)
    assert rec.calls == 0 and rec.closed == 0 and rec.wins == 0
    assert rec.rejected and rec.hidden == 0  # grace has not elapsed; the commit is simply unopened
