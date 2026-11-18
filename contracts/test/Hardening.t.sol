// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {HashLockEscrow} from "../src/HashLockEscrow.sol";
import {MandateTrader} from "../src/MandateTrader.sol";
import {FeeOnTransferToken, PlainToken} from "./utils/Mocks.sol";

/// Regression tests: a lock records only what arrived, and token addresses must hold code.
contract HashLockEscrowHardeningTest is Test {
    HashLockEscrow esc;
    address payer = makeAddr("payer");
    address payee = makeAddr("payee");
    bytes32 statement = sha256(abi.encodePacked(keccak256("hardening")));

    function setUp() public {
        esc = new HashLockEscrow();
    }

    function test_feeOnTransferTokenIsRefused() public {
        FeeOnTransferToken fot = new FeeOnTransferToken();
        fot.mint(payer, 100e18);
        vm.startPrank(payer);
        fot.approve(address(esc), type(uint256).max);
        vm.expectRevert(HashLockEscrow.TransferFailed.selector);
        esc.lock(payee, address(fot), 100e18, statement, uint96(block.timestamp + 1 hours));
        vm.stopPrank();
        assertEq(fot.balanceOf(address(esc)), 0);
        assertEq(fot.balanceOf(payer), 100e18);
    }

    function test_tokenWithoutCodeIsRefused() public {
        address empty = makeAddr("not-deployed-yet");
        assertEq(empty.code.length, 0);
        vm.prank(payer);
        vm.expectRevert(HashLockEscrow.BadTerms.selector);
        esc.lock(payee, empty, 1e18, statement, uint96(block.timestamp + 1 hours));
    }

    function test_exactTokenStillLocks() public {
        PlainToken t = new PlainToken();
        t.mint(payer, 5e18);
        vm.startPrank(payer);
        t.approve(address(esc), type(uint256).max);
        esc.lock(payee, address(t), 5e18, statement, uint96(block.timestamp + 1 hours));
        vm.stopPrank();
        assertEq(t.balanceOf(address(esc)), 5e18);
    }

    function testFuzz_lockedAmountEqualsReceived(uint96 amount) public {
        vm.assume(amount > 0);
        PlainToken t = new PlainToken();
        t.mint(payer, amount);
        vm.startPrank(payer);
        t.approve(address(esc), type(uint256).max);
        bytes32 id = esc.lock(payee, address(t), amount, statement, uint96(block.timestamp + 1 hours));
        vm.stopPrank();
        (,,,, uint256 recorded,,) = esc.locks(id);
        assertEq(recorded, t.balanceOf(address(esc)));
    }
}

/// Regression tests: two-step ownership and a bounded oracle band.
contract MandateTraderHardeningTest is Test {
    MandateTrader mt;
    address owner = makeAddr("owner");
    address next = makeAddr("next");

    function setUp() public {
        mt = new MandateTrader(owner);
    }

    function test_constructorRejectsZeroOwner() public {
        vm.expectRevert(MandateTrader.ZeroAddress.selector);
        new MandateTrader(address(0));
    }

    function test_transferRejectsZero() public {
        vm.prank(owner);
        vm.expectRevert(MandateTrader.ZeroAddress.selector);
        mt.transferOwnership(address(0));
    }

    function test_twoStepHandover() public {
        vm.prank(owner);
        vm.expectEmit(true, true, false, false);
        emit MandateTrader.OwnershipHandoverStarted(owner, next);
        mt.transferOwnership(next);
        assertEq(mt.owner(), owner, "owner unchanged until accept");
        assertEq(mt.pendingOwner(), next);

        vm.prank(next);
        vm.expectEmit(true, true, false, false);
        emit MandateTrader.OwnershipTransferred(owner, next);
        mt.acceptOwnership();
        assertEq(mt.owner(), next);
        assertEq(mt.pendingOwner(), address(0));
    }

    function test_wrongAddressKeepsOwner() public {
        address typo = makeAddr("typo");
        vm.prank(owner);
        mt.transferOwnership(typo);
        vm.prank(makeAddr("someone"));
        vm.expectRevert(MandateTrader.NotOwner.selector);
        mt.acceptOwnership();
        // The owner can still act and can name the right address.
        vm.prank(owner);
        mt.transferOwnership(next);
        vm.prank(next);
        mt.acceptOwnership();
        assertEq(mt.owner(), next);
    }

    function test_cancelHandover() public {
        vm.startPrank(owner);
        mt.transferOwnership(next);
        mt.cancelOwnershipHandover();
        vm.stopPrank();
        vm.prank(next);
        vm.expectRevert(MandateTrader.NotOwner.selector);
        mt.acceptOwnership();
        assertEq(mt.owner(), owner);
    }

    function test_onlyOwnerStartsHandover() public {
        vm.prank(next);
        vm.expectRevert(MandateTrader.NotOwner.selector);
        mt.transferOwnership(next);
    }

    function testFuzz_bandAbove100PercentRejected(uint16 bps) public {
        bps = uint16(bound(bps, 10_001, type(uint16).max));
        vm.prank(owner);
        vm.expectRevert(MandateTrader.BadRule.selector);
        mt.setRule(3, MandateTrader.AssetRule({allowed: true, szDecimals: 5, maxOrderNotional: 1e8, maxOracleDeviationBps: bps}));
    }

    function test_bandAt100PercentAccepted() public {
        vm.prank(owner);
        mt.setRule(3, MandateTrader.AssetRule({allowed: true, szDecimals: 5, maxOrderNotional: 1e8, maxOracleDeviationBps: 10_000}));
        (,,, uint16 dev) = mt.rules(3);
        assertEq(dev, 10_000);
    }
}
