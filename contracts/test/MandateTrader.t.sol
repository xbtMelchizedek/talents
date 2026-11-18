// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {MandateTrader} from "../src/MandateTrader.sol";

/// Stands in for HyperCore's CoreWriter system contract on a local chain: records raw actions.
contract MockCoreWriter {
    bytes[] public actions;
    function sendRawAction(bytes calldata data) external { actions.push(data); }
    function count() external view returns (uint256) { return actions.length; }
    function last() external view returns (bytes memory) { return actions[actions.length - 1]; }
}

/// Stands in for the perp oracle precompile: returns a fixed raw price for any asset.
contract MockOracle {
    uint64 public raw;
    function set(uint64 r) external { raw = r; }
    fallback(bytes calldata) external returns (bytes memory) { return abi.encode(raw); }
}

contract MandateTraderTest is Test {
    MandateTrader mt;
    MockCoreWriter cw;
    MockOracle oracle;
    address owner = makeAddr("owner");
    address agent = makeAddr("agent");
    address guardian = makeAddr("guardian");
    uint32 constant BTC = 3;
    uint32 constant ETH = 4;

    function setUp() public {
        vm.etch(0x3333333333333333333333333333333333333333, address(new MockCoreWriter()).code);
        cw = MockCoreWriter(0x3333333333333333333333333333333333333333);
        vm.etch(0x0000000000000000000000000000000000000807, address(new MockOracle()).code);
        oracle = MockOracle(payable(0x0000000000000000000000000000000000000807));
        oracle.set(639860); // BTC testnet read 2026-11-18: 0x9c374 -> 63986.0 with szDecimals 5

        mt = new MandateTrader(owner);
        vm.startPrank(owner);
        mt.setMandate(agent, guardian, uint64(block.timestamp + 7 days), 1_000e8, false); // $1,000/day new risk
        mt.setRule(BTC, MandateTrader.AssetRule({allowed: true, szDecimals: 5, maxOrderNotional: 250e8, maxOracleDeviationBps: 200}));
        vm.stopPrank();
    }

    // 0.003 BTC at 64,000 = $192
    function _order() internal {
        vm.prank(agent);
        mt.placeLimitOrder(BTC, true, 64_000e8, 300_000, false, 2, 0);
    }

    function test_encodingMatchesDocs() public {
        _order();
        bytes memory got = cw.last();
        bytes memory want = abi.encodePacked(hex"01000001", abi.encode(uint32(3), true, uint64(64_000e8), uint64(300_000), false, uint8(2), uint128(0)));
        assertEq(got, want);
        assertEq(got.length, 4 + 7 * 32);
        assertEq(mt.remainingToday(), 1_000e8 - 192e8);
    }

    function test_onlyTraderPlaces() public {
        vm.expectRevert(MandateTrader.NotTrader.selector);
        mt.placeLimitOrder(BTC, true, 64_000e8, 300_000, false, 2, 0);
    }

    function test_assetAllowlist() public {
        vm.prank(agent);
        vm.expectRevert(MandateTrader.AssetNotAllowed.selector);
        mt.placeLimitOrder(ETH, true, 3_000e8, 1e8, false, 2, 0);
    }

    function test_perOrderCap() public {
        vm.prank(agent);
        vm.expectRevert(MandateTrader.OrderTooLarge.selector);
        mt.placeLimitOrder(BTC, true, 64_000e8, 400_000, false, 2, 0); // $256 > $250
    }

    function test_dailyCapAndRollover() public {
        for (uint256 i; i < 5; i++) _order(); // 5 * 192 = 960
        vm.prank(agent);
        vm.expectRevert(MandateTrader.DailyCapExceeded.selector);
        mt.placeLimitOrder(BTC, true, 64_000e8, 300_000, false, 2, 0);
        // reduce-only orders do not consume new-risk budget
        vm.prank(agent);
        mt.placeLimitOrder(BTC, false, 63_000e8, 300_000, true, 3, 0);
        vm.warp(block.timestamp + 1 days);
        _order();
        assertEq(cw.count(), 7);
    }

    function test_oracleBand() public {
        vm.prank(agent);
        vm.expectRevert(MandateTrader.OracleBand.selector);
        mt.placeLimitOrder(BTC, true, 70_000e8, 100_000, false, 3, 0); // +9.4% vs oracle
        oracle.set(0);
        vm.prank(agent);
        vm.expectRevert(MandateTrader.OracleUnavailable.selector);
        mt.placeLimitOrder(BTC, true, 64_000e8, 100_000, false, 3, 0);
    }

    function test_pauseExpiryReduceOnly() public {
        vm.prank(guardian);
        mt.pause();
        vm.prank(agent);
        vm.expectRevert(MandateTrader.IsPaused.selector);
        mt.placeLimitOrder(BTC, true, 64_000e8, 100_000, false, 2, 0);
        vm.prank(guardian);
        vm.expectRevert(MandateTrader.NotOwner.selector);
        mt.unpause();
        // cancels still work while paused
        vm.prank(agent);
        mt.cancelByOid(BTC, 42);
        assertEq(cw.last(), abi.encodePacked(hex"0100000a", abi.encode(uint32(3), uint64(42))));

        vm.startPrank(owner);
        mt.unpause();
        mt.setMandate(agent, guardian, uint64(block.timestamp + 7 days), 1_000e8, true);
        vm.stopPrank();
        vm.prank(agent);
        vm.expectRevert(MandateTrader.ReduceOnlyMode.selector);
        mt.placeLimitOrder(BTC, true, 64_000e8, 100_000, false, 2, 0);

        vm.warp(block.timestamp + 8 days);
        vm.prank(agent);
        vm.expectRevert(MandateTrader.Expired.selector);
        mt.placeLimitOrder(BTC, false, 64_000e8, 100_000, true, 2, 0);
    }

    function test_traderCannotMoveFunds() public {
        vm.prank(agent);
        vm.expectRevert(MandateTrader.NotOwner.selector);
        mt.ownerAction(7, abi.encode(uint64(1e6), false)); // usd class transfer
        vm.prank(agent);
        vm.expectRevert(MandateTrader.NotOwner.selector);
        mt.setMandate(agent, guardian, type(uint64).max, type(uint64).max, false);
    }

    function test_badOrderShapes() public {
        vm.startPrank(agent);
        vm.expectRevert(MandateTrader.BadOrder.selector);
        mt.placeLimitOrder(BTC, true, 0, 1, false, 2, 0);
        vm.expectRevert(MandateTrader.BadOrder.selector);
        mt.placeLimitOrder(BTC, true, 64_000e8, 1, false, 4, 0); // tif out of range
        vm.stopPrank();
    }

    function testFuzz_neverExceedsDailyCap(uint64 px, uint64 sz, uint8 n) public {
        px = uint64(bound(px, 62_800e8, 65_200e8)); // inside the 2% band
        sz = uint64(bound(sz, 1, 390_000));
        n = uint8(bound(n, 1, 40));
        uint256 sent;
        for (uint256 i; i < n; i++) {
            vm.prank(agent);
            try mt.placeLimitOrder(BTC, true, px, sz, false, 2, 0) { sent += uint256(px) * sz / 1e8; } catch {}
        }
        assertLe(sent, 1_000e8);
    }

    /// spentOnDay + remainingToday always equals the daily cap after any accepted new-risk order.
    function test_spentAndRemainingConserveDailyCap() public {
        assertEq(mt.remainingToday(), 1_000e8);
        assertEq(mt.spentOnDay(block.timestamp / 1 days), 0);
        _order(); // $192
        uint256 day = block.timestamp / 1 days;
        assertEq(mt.spentOnDay(day) + mt.remainingToday(), 1_000e8);
        assertEq(mt.spentOnDay(day), 192e8);
        // reduce-only must not move the budget
        vm.prank(agent);
        mt.placeLimitOrder(BTC, false, 63_000e8, 300_000, true, 3, 0);
        assertEq(mt.spentOnDay(day) + mt.remainingToday(), 1_000e8);
        assertEq(mt.spentOnDay(day), 192e8);
    }

    /// An in-band, under-cap order still fails when paused or expired — composition, not just unit caps.
    function test_inBandUnderCapStillRespectsPauseAndExpiry() public {
        vm.prank(guardian);
        mt.pause();
        vm.prank(agent);
        vm.expectRevert(MandateTrader.IsPaused.selector);
        mt.placeLimitOrder(BTC, true, 64_000e8, 300_000, false, 2, 0);
        assertEq(mt.spentOnDay(block.timestamp / 1 days), 0);

        vm.prank(owner);
        mt.unpause();
        vm.warp(mt.expiry());
        vm.prank(agent);
        vm.expectRevert(MandateTrader.Expired.selector);
        mt.placeLimitOrder(BTC, true, 64_000e8, 300_000, false, 2, 0);
        assertEq(cw.count(), 0);
    }
}
