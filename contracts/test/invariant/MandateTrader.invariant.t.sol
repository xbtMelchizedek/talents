// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {MandateTrader} from "../../src/MandateTrader.sol";
import {CoreWriterRecorder, OraclePrecompileMock} from "../utils/Mocks.sol";
import {MandateTraderHandler} from "./MandateTraderHandler.sol";

/// @notice Stateful properties of the mandate. The handler's model is the second method.
contract MandateTraderInvariantTest is Test {
    address internal constant CORE_WRITER = 0x3333333333333333333333333333333333333333;
    address internal constant ORACLE_PX = 0x0000000000000000000000000000000000000807;
    uint64 internal constant DAILY_CAP = 500e8; // two max-size orders reach it

    MandateTrader internal trader;
    CoreWriterRecorder internal coreWriter;
    MandateTraderHandler internal handler;

    function setUp() public {
        vm.etch(CORE_WRITER, address(new CoreWriterRecorder()).code);
        vm.etch(ORACLE_PX, address(new OraclePrecompileMock()).code);
        coreWriter = CoreWriterRecorder(CORE_WRITER);
        OraclePrecompileMock oracle = OraclePrecompileMock(payable(ORACLE_PX));
        oracle.set(639_860); // BTC ~63,986 with szDecimals 5

        address owner = makeAddr("owner");
        address agent = makeAddr("agent");
        address guardian = makeAddr("guardian");
        trader = new MandateTrader(owner);
        vm.startPrank(owner);
        trader.setMandate(agent, guardian, uint64(block.timestamp + 3 days), DAILY_CAP, false);
        trader.setRule(
            3,
            MandateTrader.AssetRule({allowed: true, szDecimals: 5, maxOrderNotional: 250e8, maxOracleDeviationBps: 200})
        );
        trader.setRule(
            4,
            MandateTrader.AssetRule({allowed: true, szDecimals: 4, maxOrderNotional: 400e8, maxOracleDeviationBps: 0})
        );
        vm.stopPrank();

        handler = new MandateTraderHandler(trader, oracle, owner, agent, guardian);

        bytes4[] memory selectors = new bytes4[](9);
        selectors[0] = MandateTraderHandler.place.selector;
        selectors[1] = MandateTraderHandler.strangerPlace.selector;
        selectors[2] = MandateTraderHandler.strangerOwnerAction.selector;
        selectors[3] = MandateTraderHandler.cancel.selector;
        selectors[4] = MandateTraderHandler.pause.selector;
        selectors[5] = MandateTraderHandler.unpause.selector;
        selectors[6] = MandateTraderHandler.moveOracle.selector;
        selectors[7] = MandateTraderHandler.warp.selector;
        selectors[8] = MandateTraderHandler.placeWithinMandate.selector;
        targetContract(address(handler));
        targetSelector(FuzzSelector({addr: address(handler), selectors: selectors}));
    }

    /// New risk placed on any UTC day never exceeds the daily cap, and matches the model exactly.
    function invariant_dailySpendMatchesModelAndCap() public view {
        uint256 n = handler.touchedDayCount();
        for (uint256 i; i < n; ++i) {
            uint256 day = handler.touchedDay(i);
            uint256 spent = trader.spentOnDay(day);
            assertEq(spent, handler.ghostSpent(day));
            assertLe(spent, DAILY_CAP);
        }
    }

    function invariant_remainingTodayIsCapMinusSpent() public view {
        uint256 spent = trader.spentOnDay(block.timestamp / 1 days);
        assertEq(trader.remainingToday(), DAILY_CAP - spent);
    }

    /// Contract and model agree on every order: nothing extra accepted, nothing valid refused.
    function invariant_contractAgreesWithModel() public view {
        assertEq(handler.breaches(), 0, "accepted an order the mandate forbids");
        assertEq(handler.mismatches(), 0, "refused an order the mandate allows");
    }

    /// The only actions that reach CoreWriter are the orders and cancels we counted, with the
    /// documented header (version 1, action id 1 or 10). The trader key cannot smuggle a transfer.
    function invariant_onlyOrdersAndCancelsReachCoreWriter() public view {
        uint256 n = coreWriter.count();
        assertEq(n, handler.ghostOrders() + handler.ghostCancels());
        for (uint256 i; i < n; ++i) {
            bytes memory action = coreWriter.actions(i);
            assertEq(uint8(action[0]), 1);
            uint24 actionId =
                (uint24(uint8(action[1])) << 16) | (uint24(uint8(action[2])) << 8) | uint24(uint8(action[3]));
            assertTrue(actionId == 1 || actionId == 10, "unexpected CoreWriter action");
        }
    }
}
