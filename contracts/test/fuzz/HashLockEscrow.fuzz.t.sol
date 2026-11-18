// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {HashLockEscrow} from "../../src/HashLockEscrow.sol";
import {MissingReturnToken, ReturnsFalseToken} from "../utils/Mocks.sol";

/// @notice Stateless properties of the escrow, one property per test.
contract HashLockEscrowFuzzTest is Test {
    HashLockEscrow internal escrow;
    address internal payer = makeAddr("payer");
    address internal payee = makeAddr("payee");
    bytes32 internal secret = keccak256("talents.fuzz.secret");
    bytes32 internal statement;

    function setUp() public {
        escrow = new HashLockEscrow();
        statement = sha256(abi.encodePacked(secret));
        vm.deal(payer, type(uint128).max);
    }

    /// Changing any single term changes the id, so an id cannot be squatted with other terms.
    function testFuzz_idCommitsToEveryTerm(
        address payer_,
        address payee_,
        address token,
        uint256 amount,
        bytes32 statement_,
        uint96 refundAfter,
        uint8 field,
        uint96 delta
    ) public view {
        vm.assume(delta != 0);
        bytes32 id = escrow.lockId(payer_, payee_, token, amount, statement_, refundAfter);
        field = uint8(bound(field, 0, 5));
        address shift = address(uint160(delta));
        if (field == 0) payer_ = address(uint160(payer_) ^ uint160(shift));
        if (field == 1) payee_ = address(uint160(payee_) ^ uint160(shift));
        if (field == 2) token = address(uint160(token) ^ uint160(shift));
        if (field == 3) amount ^= delta;
        if (field == 4) statement_ ^= bytes32(uint256(delta));
        if (field == 5) refundAfter ^= delta;
        assertNotEq(escrow.lockId(payer_, payee_, token, amount, statement_, refundAfter), id);
    }

    /// At every moment exactly one exit is open: claim strictly before the deadline, refund at or after.
    function testFuzz_exactlyOneExitAtAnyTime(uint256 amount, uint256 ttl, uint256 elapsed) public {
        amount = bound(amount, 1, 1_000 ether);
        ttl = bound(ttl, 1, 30 days);
        uint96 refundAfter = uint96(block.timestamp + ttl);
        vm.prank(payer);
        bytes32 id = escrow.lock{value: amount}(payee, address(0), amount, statement, refundAfter);
        vm.warp(block.timestamp + bound(elapsed, 0, 2 * ttl));

        uint256 snapshot = vm.snapshotState();
        bool claimed = _tryClaim(id);
        vm.revertToState(snapshot);
        bool refunded = _tryRefund(id);

        assertTrue(claimed != refunded, "claim and refund windows must partition time");
        assertEq(claimed, block.timestamp < refundAfter);
    }

    /// Native locks need msg.value == amount exactly; token locks refuse any ether.
    function testFuzz_lockRejectsMismatchedValue(uint256 amount, uint256 value) public {
        amount = bound(amount, 1, 1_000 ether);
        value = bound(value, 0, 1_000 ether);
        vm.assume(value != amount);
        vm.prank(payer);
        vm.expectRevert(HashLockEscrow.BadTerms.selector);
        escrow.lock{value: value}(payee, address(0), amount, statement, uint96(block.timestamp + 1));
    }

    function testFuzz_tokenLockRefusesEther(uint256 value) public {
        value = bound(value, 1, 1_000 ether);
        MissingReturnToken token = new MissingReturnToken();
        vm.prank(payer);
        vm.expectRevert(HashLockEscrow.BadTerms.selector);
        escrow.lock{value: value}(payee, address(token), 1, statement, uint96(block.timestamp + 1));
    }

    /// USDT-style tokens that return nothing still lock and pay out exactly.
    function testFuzz_missingReturnTokenRoundTrips(uint256 amount) public {
        amount = bound(amount, 1, type(uint128).max);
        MissingReturnToken token = new MissingReturnToken();
        token.mint(payer, amount);
        vm.startPrank(payer);
        token.approve(address(escrow), amount);
        bytes32 id = escrow.lock(payee, address(token), amount, statement, uint96(block.timestamp + 1 hours));
        vm.stopPrank();
        assertEq(token.balanceOf(address(escrow)), amount);

        escrow.claim(id, secret);
        assertEq(token.balanceOf(payee), amount);
        assertEq(token.balanceOf(address(escrow)), 0);
    }

    /// A token that signals failure with `false` must not create a lock.
    function testFuzz_returnsFalseTokenCannotLock(uint256 amount) public {
        amount = bound(amount, 1, type(uint128).max);
        ReturnsFalseToken token = new ReturnsFalseToken();
        vm.prank(payer);
        vm.expectRevert(HashLockEscrow.TransferFailed.selector);
        escrow.lock(payee, address(token), amount, statement, uint96(block.timestamp + 1 hours));
    }

    function _tryClaim(bytes32 id) internal returns (bool) {
        try escrow.claim(id, secret) {
            return true;
        } catch {
            return false;
        }
    }

    function _tryRefund(bytes32 id) internal returns (bool) {
        try escrow.refund(id) {
            return true;
        } catch {
            return false;
        }
    }
}
