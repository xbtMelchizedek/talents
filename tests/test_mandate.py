import pytest

from talents.mandate import DayState, Mandate, check_hl_order, check_outcome_order, check_pump_intent, check_swap

NOW = 1_795_000_000_000
BASE = {"agent": "did:key:zTest", "network": "hl-testnet", "venues": ["hl-perp", "hl-outcome", "pump", "htlc-swap"],
        "expires_ms": NOW + 86_400_000, "per_order_usd": "250", "daily_usd": "1000", "max_leverage": 3,
        "hl_assets": ["BTC", "ETH"], "pump_max_sol_in": "0.5", "swap_max_usd": "100"}


def m(**kw):
    return Mandate.from_json({**BASE, **kw})


def hl(mm, day=None, **kw):
    args = dict(coin="BTC", is_buy=True, px="64000", sz="0.003", reduce_only=False, leverage=2, mark_px="63986", now_ms=NOW)
    args.update(kw)
    return check_hl_order(mm, day or DayState(), **args)


def test_mainnet_refused():
    for net in ("hl-mainnet", "solana-mainnet", "flop-mainnet", "polygon"):
        with pytest.raises(ValueError):
            m(network=net)


def test_ok_order_and_notional():
    v = hl(m())
    assert v.ok and v.notional_e8 == 192 * 10**8


@pytest.mark.parametrize("kw,why", [
    (dict(sz="0.004"), "per-order cap"),
    (dict(coin="DOGE"), "not in hl_assets"),
    (dict(leverage=5), "leverage"),
    (dict(px="70000"), "bps from mark"),
    (dict(now_ms=NOW + 86_400_000), "expired"),
])
def test_denials(kw, why):
    v = hl(m(), **kw)
    assert not v.ok and any(why in r for r in v.reasons)


def test_daily_cap_and_reduce_only():
    day = DayState(used_e8=900 * 10**8)
    assert not hl(m(), day).ok
    assert hl(m(), day, reduce_only=True, is_buy=False).ok  # risk-reducing orders don't use budget
    assert not hl(m(reduce_only=True)).ok


def test_paused_and_float_refused():
    assert not hl(m(paused=True)).ok
    with pytest.raises(TypeError):
        hl(m(), px=64000.0)


def test_outcome_orders():
    mm = m()
    assert check_outcome_order(mm, DayState(), outcome=20115, side=0, px="0.42", sz="100", now_ms=NOW).ok
    assert not check_outcome_order(mm, DayState(), outcome=20115, side=0, px="1", sz="100", now_ms=NOW).ok
    assert not check_outcome_order(mm, DayState(), outcome=20115, side=0, px="0.5", sz="600", now_ms=NOW).ok


def test_pump_intents():
    mm = m()
    ok = dict(mint="So1Test", sol_in="0.25", quoted_out="1000000", min_out="980000", now_ms=NOW)
    assert check_pump_intent(mm, action="buy", **ok).ok
    assert not check_pump_intent(mm, action="create", **ok).ok
    assert not check_pump_intent(mm, action="buy", **{**ok, "sol_in": "0.6"}).ok
    assert not check_pump_intent(mm, action="buy", **{**ok, "min_out": "900000"}).ok
    assert not check_pump_intent(m(pump_mint_denylist=["So1Test"]), action="buy", **ok).ok


def test_swap_cap_and_venue():
    assert check_swap(m(), usd_value="100", now_ms=NOW).ok
    assert not check_swap(m(), usd_value="101", now_ms=NOW).ok
    assert not check_swap(m(venues=["hl-perp"]), usd_value="1", now_ms=NOW).ok
