// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {MandateTrader} from "../../src/MandateTrader.sol";
import {CoreWriterRecorder, OraclePrecompileMock} from "../utils/Mocks.sol";

/// @notice Stateless properties of the mandate. Each expected value is computed a second way.
contract MandateTraderFuzzTest is Test {
    address internal constant CORE_WRITER = 0x3333333333333333333333333333333333333333;
    address internal constant ORACLE_PX = 0x0000000000000000000000000000000000000807;
    uint32 internal constant ASSET = 3;
    uint256 internal constant MAX_BPS = 10_000;

    MandateTrader internal trader;
    OraclePrecompileMock internal oracle;
    address internal owner = makeAddr("owner");
    address internal agent = makeAddr("agent");

    function setUp() public {
        vm.etch(CORE_WRITER, address(new CoreWriterRecorder()).code);
        vm.etch(ORACLE_PX, address(new OraclePrecompileMock()).code);
        oracle = OraclePrecompileMock(payable(ORACLE_PX));
        trader = new MandateTrader(owner);
        vm.prank(owner);
        trader.setMandate(agent, makeAddr("guardian"), uint64(block.timestamp + 1 days), type(uint64).max, false);
    }

    /// The band is inclusive at both edges and closed one unit outside them.
    function testFuzz_oracleBandEdges(uint64 raw, uint8 szDecimals, uint16 deviationBps) public {
        raw = uint64(bound(raw, 1, 1e9));
        szDecimals = uint8(bound(szDecimals, 0, 5));
        deviationBps = uint16(bound(deviationBps, 1, 5_000));
        oracle.set(raw);
        _rule(szDecimals, type(uint64).max, deviationBps);

        uint256 mark = uint256(raw) * 10 ** (2 + uint256(szDecimals));
        uint256 band = mark * deviationBps;
        uint256 lo = mark - (band + MAX_BPS - 1) / MAX_BPS; // second form of floor(mark*(MAX-d)/MAX)
        uint256 hi = mark + band / MAX_BPS;

        _place(uint64(lo), 1, true);
        _place(uint64(hi), 1, true);
        if (lo > 1) _expectBand(uint64(lo - 1));
        _expectBand(uint64(hi + 1));
    }

    /// Spent notional equals limitPx * sz / 1e8, computed here without the full product.
    function testFuzz_notionalTwoWays(uint64 limitPx, uint64 sz) public {
        limitPx = uint64(bound(limitPx, 1, 1e15));
        sz = uint64(bound(sz, 1, 1e12));
        _rule(0, type(uint64).max, 0);
        uint256 expected = (uint256(limitPx) / 1e8) * sz + (uint256(limitPx) % 1e8) * sz / 1e8;

        _place(limitPx, sz, false);
        assertEq(trader.spentOnDay(block.timestamp / 1 days), expected);
    }

    /// Reduce-only orders never draw down the daily cap.
    function testFuzz_reduceOnlySpendsNothing(uint64 limitPx, uint64 sz) public {
        limitPx = uint64(bound(limitPx, 1, 1e15));
        sz = uint64(bound(sz, 1, 1e12));
        _rule(0, type(uint64).max, 0);
        _place(limitPx, sz, true);
        assertEq(trader.spentOnDay(block.timestamp / 1 days), 0);
    }

    /// Only the trader key places orders.
    function testFuzz_onlyTraderPlaces(address sender) public {
        vm.assume(sender != agent);
        _rule(0, type(uint64).max, 0);
        vm.prank(sender);
        vm.expectRevert(MandateTrader.NotTrader.selector);
        trader.placeLimitOrder(ASSET, true, 1e8, 1, false, 1, 0);
    }

    /// Only the owner reaches the raw CoreWriter escape hatch.
    function testFuzz_onlyOwnerSendsRawActions(address sender, uint24 actionId, bytes calldata fields) public {
        vm.assume(sender != owner);
        vm.prank(sender);
        vm.expectRevert(MandateTrader.NotOwner.selector);
        trader.ownerAction(actionId, fields);
    }

    function _rule(uint8 szDecimals, uint64 maxOrderNotional, uint16 deviationBps) internal {
        vm.prank(owner);
        trader.setRule(
            ASSET,
            MandateTrader.AssetRule({
                allowed: true,
                szDecimals: szDecimals,
                maxOrderNotional: maxOrderNotional,
                maxOracleDeviationBps: deviationBps
            })
        );
    }

    function _place(uint64 limitPx, uint64 sz, bool reduceOnly) internal {
        vm.prank(agent);
        trader.placeLimitOrder(ASSET, true, limitPx, sz, reduceOnly, 1, 0);
    }

    function _expectBand(uint64 limitPx) internal {
        vm.prank(agent);
        vm.expectRevert(MandateTrader.OracleBand.selector);
        trader.placeLimitOrder(ASSET, true, limitPx, 1, true, 1, 0);
    }
}
