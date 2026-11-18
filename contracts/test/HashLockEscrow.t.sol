// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {HashLockEscrow} from "../src/HashLockEscrow.sol";

contract MockUSDC {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    function mint(address to, uint256 a) external { balanceOf[to] += a; }
    function approve(address s, uint256 a) external returns (bool) { allowance[msg.sender][s] = a; return true; }
    function transfer(address to, uint256 a) external returns (bool) { balanceOf[msg.sender] -= a; balanceOf[to] += a; return true; }
    function transferFrom(address f, address to, uint256 a) external returns (bool) {
        allowance[f][msg.sender] -= a; balanceOf[f] -= a; balanceOf[to] += a; return true;
    }
}

contract HashLockEscrowTest is Test {
    HashLockEscrow esc;
    MockUSDC usdc;
    address bob = makeAddr("bob"); // locks HYPE/USDC on HyperEVM
    address alice = makeAddr("alice"); // claims it with the secret
    bytes32 secret = keccak256("talents-test-secret");
    bytes32 statement;
    uint96 deadline;

    function setUp() public {
        esc = new HashLockEscrow();
        usdc = new MockUSDC();
        statement = sha256(abi.encodePacked(secret));
        deadline = uint96(block.timestamp + 1 hours);
        vm.deal(bob, 10 ether);
        usdc.mint(bob, 1_000e6);
    }

    function _lockNative(uint256 amt) internal returns (bytes32) {
        vm.prank(bob);
        return esc.lock{value: amt}(alice, address(0), amt, statement, deadline);
    }

    function test_claimNativeRevealsPreimage() public {
        bytes32 id = _lockNative(1 ether);
        vm.expectEmit(true, false, false, true);
        emit HashLockEscrow.Claimed(id, secret);
        vm.prank(makeAddr("watchtower")); // anyone can submit; funds go to payee
        esc.claim(id, secret);
        assertEq(alice.balance, 1 ether);
        (,,,,,, HashLockEscrow.Status s) = esc.locks(id);
        assertEq(uint8(s), uint8(HashLockEscrow.Status.Claimed));
    }

    function test_claimErc20() public {
        vm.startPrank(bob);
        usdc.approve(address(esc), 500e6);
        bytes32 id = esc.lock(alice, address(usdc), 500e6, statement, deadline);
        vm.stopPrank();
        esc.claim(id, secret);
        assertEq(usdc.balanceOf(alice), 500e6);
        assertEq(usdc.balanceOf(bob), 500e6);
    }

    function test_wrongPreimageRejected() public {
        bytes32 id = _lockNative(1 ether);
        vm.expectRevert(HashLockEscrow.BadPreimage.selector);
        esc.claim(id, bytes32(0));
    }

    function test_noClaimAtOrAfterDeadline() public {
        bytes32 id = _lockNative(1 ether);
        vm.warp(deadline);
        vm.expectRevert(HashLockEscrow.TooLate.selector);
        esc.claim(id, secret);
    }

    function test_refundTiming() public {
        bytes32 id = _lockNative(1 ether);
        vm.warp(deadline - 1);
        vm.expectRevert(HashLockEscrow.TooEarly.selector);
        esc.refund(id);
        vm.warp(deadline);
        esc.refund(id);
        assertEq(bob.balance, 10 ether);
        vm.expectRevert(HashLockEscrow.NotLocked.selector);
        esc.claim(id, secret);
    }

    function test_noDoubleClaimOrRefundAfterClaim() public {
        bytes32 id = _lockNative(1 ether);
        esc.claim(id, secret);
        vm.expectRevert(HashLockEscrow.NotLocked.selector);
        esc.claim(id, secret);
        vm.warp(deadline);
        vm.expectRevert(HashLockEscrow.NotLocked.selector);
        esc.refund(id);
    }

    function test_badTerms() public {
        vm.startPrank(bob);
        vm.expectRevert(HashLockEscrow.BadTerms.selector);
        esc.lock{value: 1}(alice, address(0), 2, statement, deadline); // value mismatch
        vm.expectRevert(HashLockEscrow.BadTerms.selector);
        esc.lock{value: 1}(alice, address(0), 1, statement, uint96(block.timestamp)); // refund window open
        vm.expectRevert(HashLockEscrow.BadTerms.selector);
        esc.lock{value: 1}(address(0), address(0), 1, statement, deadline);
        vm.expectRevert(HashLockEscrow.BadTerms.selector);
        esc.lock{value: 1}(alice, address(usdc), 1, statement, deadline); // value with token
        vm.stopPrank();
    }

    function test_sameTermsTwiceRejected() public {
        _lockNative(1 ether);
        vm.prank(bob);
        vm.expectRevert(HashLockEscrow.AlreadyExists.selector);
        esc.lock{value: 1 ether}(alice, address(0), 1 ether, statement, deadline);
    }

    function test_idCommitsToTerms() public view {
        bytes32 a = esc.lockId(bob, alice, address(0), 1, statement, deadline);
        assertTrue(a != esc.lockId(bob, alice, address(0), 2, statement, deadline));
        assertTrue(a != esc.lockId(alice, alice, address(0), 1, statement, deadline));
    }

    /// The sha256 here must equal the FLOP/tclk and Pearl statement for the same 32 bytes.
    function test_statementMatchesCrossChainRule() public pure {
        // sha256 of 32 zero bytes, a fixed cross-implementation vector
        assertEq(sha256(abi.encodePacked(bytes32(0))), 0x66687aadf862bd776c8fc18b8e9f8e20089714856ee233b3902a591d0d5f2925);
    }

    function testFuzz_onlyTheSecretOpens(bytes32 guess) public {
        vm.assume(guess != secret);
        bytes32 id = _lockNative(1 ether);
        vm.expectRevert(HashLockEscrow.BadPreimage.selector);
        esc.claim(id, guess);
    }

    /// A preimage that correctly opens lock B must not open lock A (valid piece, wrong id).
    function test_validPreimageOpensOnlyItsOwnLock() public {
        bytes32 secretB = keccak256("other-secret");
        bytes32 statementB = sha256(abi.encodePacked(secretB));
        bytes32 idA = _lockNative(1 ether);
        vm.prank(bob);
        bytes32 idB = esc.lock{value: 2 ether}(alice, address(0), 2 ether, statementB, deadline);
        vm.expectRevert(HashLockEscrow.BadPreimage.selector);
        esc.claim(idA, secretB);
        vm.expectRevert(HashLockEscrow.BadPreimage.selector);
        esc.claim(idB, secret);
        esc.claim(idA, secret);
        (,,,,,, HashLockEscrow.Status sa) = esc.locks(idA);
        (,,,,,, HashLockEscrow.Status sb) = esc.locks(idB);
        assertEq(uint8(sa), uint8(HashLockEscrow.Status.Claimed));
        assertEq(uint8(sb), uint8(HashLockEscrow.Status.Locked));
        assertEq(alice.balance, 1 ether);
    }

    /// sha256 is the statement rule; a keccak256 digest of the same preimage is a different lock and will not open it.
    function test_keccakDigestIsNotTheStatement() public {
        assertTrue(sha256(abi.encodePacked(secret)) != keccak256(abi.encodePacked(secret)));
        bytes32 id = _lockNative(1 ether);
        // "guess" that is the keccak of the secret, not the secret itself
        vm.expectRevert(HashLockEscrow.BadPreimage.selector);
        esc.claim(id, keccak256(abi.encodePacked(secret)));
        (,,,,,, HashLockEscrow.Status s) = esc.locks(id);
        assertEq(uint8(s), uint8(HashLockEscrow.Status.Locked));
    }
}
