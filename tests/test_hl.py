import json
import os

import pytest

from conftest import ROOT
from talents import hl
from talents.mandate import DayState, Mandate

NOW = 1_795_000_000_000
FIX = json.loads((ROOT / "vectors" / "hl_testnet_universe_sample.json").read_text())
M = Mandate.from_json({"agent": "did:key:zT", "network": "hl-testnet", "venues": ["hl-perp"], "expires_ms": NOW + 1,
                       "per_order_usd": "250", "daily_usd": "1000", "max_leverage": 3, "hl_assets": ["BTC"]})


def test_wire_matches_sdk_shape():
    w = hl.order_wire(3, True, "64000.0", "0.00300", False, "Gtc")
    assert w == {"a": 3, "b": True, "p": "64000", "s": "0.003", "r": False, "t": {"limit": {"tif": "Gtc"}}}
    a = hl.order_action([w], builder="0x" + "ab" * 20, fee_tenths_bp=10)
    assert a["builder"] == {"b": "0x" + "ab" * 20, "f": 10} and a["grouping"] == "na"


def test_builder_fee_caps():
    w = hl.order_wire(3, True, "1", "1", False, "Ioc")
    with pytest.raises(ValueError):
        hl.order_action([w], builder="0x" + "ab" * 20, fee_tenths_bp=101)  # > 0.1% perps
    hl.order_action([w], builder="0x" + "ab" * 20, fee_tenths_bp=1000, spot=True)
    with pytest.raises(ValueError):
        hl.order_action([w], builder="0xAB" + "ab" * 19)  # must be lowercase


def test_preflight_fixture():
    v, a = hl.preflight(M, DayState(), FIX, coin="BTC", is_buy=True, px="64000", sz="0.003", reduce_only=False,
                        leverage=2, tif="Gtc", now_ms=NOW)
    assert v.ok and a["orders"][0]["a"] == FIX["BTC"]["asset"]
    v, a = hl.preflight(M, DayState(), FIX, coin="BTC", is_buy=True, px="64000", sz="0.000001", reduce_only=False,
                        leverage=2, tif="Gtc", now_ms=NOW)
    assert not v.ok and a is None  # finer than szDecimals


def test_mainnet_url_refused():
    with pytest.raises(ValueError):
        hl.info({"type": "meta"}, url="https://api.hyperliquid.xyz/info")


@pytest.mark.skipif(not os.environ.get("TALENTS_LIVE_TESTNET"), reason="set TALENTS_LIVE_TESTNET=1 for read-only testnet calls")
def test_live_testnet_read_only():
    uni = hl.perp_universe(hl.info({"type": "metaAndAssetCtxs"}))
    assert "BTC" in uni and float(uni["BTC"]["markPx"]) > 0
    om = hl.info({"type": "outcomeMeta"})
    assert om["outcomes"]


@pytest.mark.parametrize("px,szd,ok", [("1234.5", 0, True), ("1234.56", 0, False), ("0.001234", 0, True),
                                       ("0.0012345", 0, False), ("0.01234", 1, True), ("0.012345", 1, False),
                                       ("123456", 5, True), ("12345.6", 0, False)])
def test_tick_rules_from_docs(px, szd, ok):
    assert hl.price_ok(px, szd) is ok
