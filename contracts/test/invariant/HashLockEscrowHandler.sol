// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {HashLockEscrow} from "../../src/HashLockEscrow.sol";
import {PlainToken} from "../utils/Mocks.sol";

/// @dev Drives HashLockEscrow with bounded, valid-looking calls and keeps an independent model
///      (ghost state) of every lock. Any call the model says must fail but succeeds is a breach.
///      Handler functions never revert, so the suite runs with fail_on_revert = true.
contract HashLockEscrowHandler is Test {
    uint8 internal constant OPEN = 1;
    uint8 internal constant CLAIMED = 2;
    uint8 internal constant REFUNDED = 3;
    uint256 internal constant ACTORS = 5;
    uint256 public constant STARTING_ETH = 1_000 ether;
    uint256 public constant STARTING_TOKENS = 1_000_000e6;

    struct GhostLock {
        bytes32 id;
        bytes32 secret;
        address payer;
        address payee;
        address token;
        uint256 amount;
        uint96 refundAfter;
        uint8 status;
    }

    HashLockEscrow public immutable escrow;
    PlainToken public immutable token;
    address[] public actors;
    GhostLock[] internal ghostLocks;

    uint256 public ghostOpenNative;
    uint256 public ghostOpenToken;
    uint256 public breaches;
    uint256 internal secretNonce;
    mapping(bytes32 => uint256) public calls;

    constructor(HashLockEscrow escrow_, PlainToken token_) {
        escrow = escrow_;
        token = token_;
        for (uint256 i; i < ACTORS; ++i) {
            address actor = makeAddr(string.concat("actor", vm.toString(i)));
            actors.push(actor);
            vm.deal(actor, STARTING_ETH);
            token.mint(actor, STARTING_TOKENS);
            vm.prank(actor);
            token.approve(address(escrow), type(uint256).max);
        }
    }

    // ---------------- handlers ----------------

    function lockNative(uint256 payerSeed, uint256 payeeSeed, uint256 amount, uint256 ttl) external {
        address payer = _actor(payerSeed);
        amount = bound(amount, 1, 10 ether);
        if (payer.balance < amount) return;
        _lock(payer, _actor(payeeSeed), address(0), amount, ttl);
        ghostOpenNative += amount;
        ++calls["lockNative"];
    }

    function lockToken(uint256 payerSeed, uint256 payeeSeed, uint256 amount, uint256 ttl) external {
        address payer = _actor(payerSeed);
        amount = bound(amount, 1, 10_000e6);
        if (token.balanceOf(payer) < amount) return;
        _lock(payer, _actor(payeeSeed), address(token), amount, ttl);
        ghostOpenToken += amount;
        ++calls["lockToken"];
    }

    /// @dev Submits the real preimage from a random caller. Valid only while open and before the deadline.
    function claim(uint256 lockSeed, uint256 callerSeed) external {
        if (ghostLocks.length == 0) return;
        GhostLock storage g = ghostLocks[lockSeed % ghostLocks.length];
        bool mayClaim = g.status == OPEN && block.timestamp < g.refundAfter;

        if (!mayClaim) {
            vm.prank(_actor(callerSeed));
            try escrow.claim(g.id, g.secret) {
                ++breaches;
            } catch {}
            return;
        }

        uint256 before = _balanceOf(g.token, g.payee);
        vm.prank(_actor(callerSeed));
        escrow.claim(g.id, g.secret);
        if (_balanceOf(g.token, g.payee) != before + g.amount) ++breaches;
        g.status = CLAIMED;
        _close(g);
        ++calls["claim"];
    }

    /// @dev Any guess other than the secret must never open a lock.
    function claimWithGuess(uint256 lockSeed, bytes32 guess) external {
        if (ghostLocks.length == 0) return;
        GhostLock storage g = ghostLocks[lockSeed % ghostLocks.length];
        if (guess == g.secret) return;
        try escrow.claim(g.id, guess) {
            ++breaches;
        } catch {}
        ++calls["claimWithGuess"];
    }

    /// @dev Valid only while open and at or after the deadline.
    function refund(uint256 lockSeed, uint256 callerSeed) external {
        if (ghostLocks.length == 0) return;
        GhostLock storage g = ghostLocks[lockSeed % ghostLocks.length];
        bool mayRefund = g.status == OPEN && block.timestamp >= g.refundAfter;

        if (!mayRefund) {
            vm.prank(_actor(callerSeed));
            try escrow.refund(g.id) {
                ++breaches;
            } catch {}
            return;
        }

        uint256 before = _balanceOf(g.token, g.payer);
        vm.prank(_actor(callerSeed));
        escrow.refund(g.id);
        if (_balanceOf(g.token, g.payer) != before + g.amount) ++breaches;
        g.status = REFUNDED;
        _close(g);
        ++calls["refund"];
    }

    function warp(uint256 seconds_) external {
        vm.warp(block.timestamp + bound(seconds_, 0, 1 days));
        ++calls["warp"];
    }

    // ---------------- views for the invariant suite ----------------

    function lockCount() external view returns (uint256) {
        return ghostLocks.length;
    }

    function ghostLock(uint256 i) external view returns (GhostLock memory) {
        return ghostLocks[i];
    }

    function actorCount() external pure returns (uint256) {
        return ACTORS;
    }

    // ---------------- internals ----------------

    function _lock(address payer, address payee, address token_, uint256 amount, uint256 ttl) internal {
        bytes32 secret = keccak256(abi.encode("talents.handler.secret", ++secretNonce));
        bytes32 statement = sha256(abi.encodePacked(secret));
        uint96 refundAfter = uint96(block.timestamp + bound(ttl, 1, 2 days));
        uint256 value = token_ == address(0) ? amount : 0;

        vm.prank(payer);
        bytes32 id = escrow.lock{value: value}(payee, token_, amount, statement, refundAfter);
        ghostLocks.push(GhostLock(id, secret, payer, payee, token_, amount, refundAfter, OPEN));
    }

    function _close(GhostLock storage g) internal {
        if (g.token == address(0)) ghostOpenNative -= g.amount;
        else ghostOpenToken -= g.amount;
    }

    function _actor(uint256 seed) internal view returns (address) {
        return actors[seed % actors.length];
    }

    function _balanceOf(address token_, address who) internal view returns (uint256) {
        return token_ == address(0) ? who.balance : token.balanceOf(who);
    }
}
