// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test, Vm} from "forge-std/Test.sol";
import {MandateTrader} from "../src/MandateTrader.sol";

/// Optional: runs against a LOCAL fork of HyperEVM testnet (chain 998). Nothing is broadcast.
/// HYPEREVM_TESTNET_FORK=1 forge test --match-contract CoreWriterFork --fork-url https://rpc.hyperliquid-testnet.xyz/evm
contract CoreWriterForkTest is Test {
    function test_realCoreWriterAcceptsOurOrderEncoding() public {
        if (!vm.envOr("HYPEREVM_TESTNET_FORK", false)) return;
        assertEq(block.chainid, 998, "testnet fork only");
        address cw = 0x3333333333333333333333333333333333333333;
        assertGt(cw.code.length, 0);
        address owner = makeAddr("owner");
        address agent = makeAddr("agent");
        MandateTrader mt = new MandateTrader(owner);
        vm.startPrank(owner);
        mt.setMandate(agent, owner, uint64(block.timestamp + 1 days), 1_000e8, false);
        mt.setRule(3, MandateTrader.AssetRule({allowed: true, szDecimals: 5, maxOrderNotional: 250e8, maxOracleDeviationBps: 0}));
        vm.stopPrank();
        vm.recordLogs();
        vm.prank(agent);
        mt.placeLimitOrder(3, true, 64_000e8, 300_000, false, 2, 0);
        Vm.Log[] memory logs = vm.getRecordedLogs();
        bool sawCore;
        for (uint256 i; i < logs.length; i++) {
            if (logs[i].emitter == cw) {
                sawCore = true;
                emit log_named_bytes32("corewriter topic0", logs[i].topics[0]);
                emit log_named_bytes("corewriter data", logs[i].data);
            }
        }
        assertTrue(sawCore, "CoreWriter emitted no log");
    }
}
