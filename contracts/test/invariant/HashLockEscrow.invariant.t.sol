// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {HashLockEscrow} from "../../src/HashLockEscrow.sol";
import {PlainToken} from "../utils/Mocks.sol";
import {HashLockEscrowHandler} from "./HashLockEscrowHandler.sol";

/// @notice Stateful properties of the escrow. The handler's ghost model is the second method;
///         the contract's storage and balances are the first. They must agree after every call.
contract HashLockEscrowInvariantTest is Test {
    HashLockEscrow internal escrow;
    PlainToken internal token;
    HashLockEscrowHandler internal handler;

    function setUp() public {
        escrow = new HashLockEscrow();
        token = new PlainToken();
        handler = new HashLockEscrowHandler(escrow, token);

        bytes4[] memory selectors = new bytes4[](6);
        selectors[0] = HashLockEscrowHandler.lockNative.selector;
        selectors[1] = HashLockEscrowHandler.lockToken.selector;
        selectors[2] = HashLockEscrowHandler.claim.selector;
        selectors[3] = HashLockEscrowHandler.claimWithGuess.selector;
        selectors[4] = HashLockEscrowHandler.refund.selector;
        selectors[5] = HashLockEscrowHandler.warp.selector;
        targetContract(address(handler));
        targetSelector(FuzzSelector({addr: address(handler), selectors: selectors}));
    }

    /// Escrow holds exactly what is still locked: no shortfall, no stranded dust.
    function invariant_nativeBalanceEqualsOpenLocks() public view {
        assertEq(address(escrow).balance, handler.ghostOpenNative());
    }

    function invariant_tokenBalanceEqualsOpenLocks() public view {
        assertEq(token.balanceOf(address(escrow)), handler.ghostOpenToken());
    }

    /// Value is only moved between actors and the escrow, never created or lost.
    function invariant_conservation() public view {
        uint256 n = handler.actorCount();
        uint256 eth = address(escrow).balance;
        uint256 tokens = token.balanceOf(address(escrow));
        for (uint256 i; i < n; ++i) {
            address actor = handler.actors(i);
            eth += actor.balance;
            tokens += token.balanceOf(actor);
        }
        assertEq(eth, n * handler.STARTING_ETH());
        assertEq(tokens, n * handler.STARTING_TOKENS());
    }

    /// Stored terms and status match the model for every lock ever made. A settled lock stays settled.
    function invariant_storageMatchesModel() public view {
        uint256 n = handler.lockCount();
        for (uint256 i; i < n; ++i) {
            HashLockEscrowHandler.GhostLock memory g = handler.ghostLock(i);
            (
                address payer,
                address payee,
                address token_,
                uint96 refundAfter,
                uint256 amount,
                bytes32 statement,
                HashLockEscrow.Status status
            ) = escrow.locks(g.id);
            assertEq(payer, g.payer);
            assertEq(payee, g.payee);
            assertEq(token_, g.token);
            assertEq(refundAfter, g.refundAfter);
            assertEq(amount, g.amount);
            assertEq(statement, sha256(abi.encodePacked(g.secret)));
            assertEq(uint8(status), g.status);
        }
    }

    /// No wrong preimage, late claim, early refund, double settle, or short payment ever succeeded.
    function invariant_noBreaches() public view {
        assertEq(handler.breaches(), 0);
    }
}
