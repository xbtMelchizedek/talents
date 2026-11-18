// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

interface IERC20Min {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
}

/// @title HashLockEscrow — HyperEVM leg of a FLOP <-> HYPE/USDC hash-lock swap. No bridge.
/// @notice One lock per id. The payee claims with a 32-byte preimage whose sha256 equals `statement`
///         (same rule as tclk and pearl-diver). The payer refunds at or after `refundAfter`.
///         Claim is refused at or after `refundAfter`, so the two paths never overlap.
///         Unaudited. Testnet / local only.
contract HashLockEscrow {
    error AlreadyExists();
    error BadTerms();
    error NotLocked();
    error TooLate();
    error TooEarly();
    error BadPreimage();
    error TransferFailed();

    event Locked(bytes32 indexed id, address indexed payer, address indexed payee, address token, uint256 amount, bytes32 statement, uint96 refundAfter);
    event Claimed(bytes32 indexed id, bytes32 preimage);
    event Refunded(bytes32 indexed id);

    enum Status { None, Locked, Claimed, Refunded }

    struct Lock {
        address payer;
        address payee;
        address token; // address(0) = native HYPE
        uint96 refundAfter;
        uint256 amount;
        bytes32 statement;
        Status status;
    }

    mapping(bytes32 => Lock) public locks;

    /// @notice The id commits to every term, so nobody can squat an id with different terms.
    function lockId(address payer, address payee, address token, uint256 amount, bytes32 statement, uint96 refundAfter)
        public pure returns (bytes32)
    {
        return keccak256(abi.encode("talents.hle/1", payer, payee, token, amount, statement, refundAfter));
    }

    function lock(address payee, address token, uint256 amount, bytes32 statement, uint96 refundAfter)
        external payable returns (bytes32 id)
    {
        if (payee == address(0) || amount == 0 || refundAfter <= block.timestamp) revert BadTerms();
        if (token == address(0)) {
            if (msg.value != amount) revert BadTerms();
        } else if (msg.value != 0) {
            revert BadTerms();
        }
        id = lockId(msg.sender, payee, token, amount, statement, refundAfter);
        if (locks[id].status != Status.None) revert AlreadyExists();
        locks[id] = Lock(msg.sender, payee, token, refundAfter, amount, statement, Status.Locked);
        if (token != address(0)) _pull(token, msg.sender, amount);
        emit Locked(id, msg.sender, payee, token, amount, statement, refundAfter);
    }

    /// @notice Anyone may submit the claim (a watchtower can); funds always go to the payee.
    function claim(bytes32 id, bytes32 preimage) external {
        Lock storage l = locks[id];
        if (l.status != Status.Locked) revert NotLocked();
        if (block.timestamp >= l.refundAfter) revert TooLate();
        if (sha256(abi.encodePacked(preimage)) != l.statement) revert BadPreimage();
        l.status = Status.Claimed;
        emit Claimed(id, preimage);
        _pay(l.token, l.payee, l.amount);
    }

    /// @notice Anyone may trigger the refund after the deadline; funds always go to the payer.
    function refund(bytes32 id) external {
        Lock storage l = locks[id];
        if (l.status != Status.Locked) revert NotLocked();
        if (block.timestamp < l.refundAfter) revert TooEarly();
        l.status = Status.Refunded;
        emit Refunded(id);
        _pay(l.token, l.payer, l.amount);
    }

    function _pull(address token, address from, uint256 amount) private {
        (bool ok, bytes memory ret) = token.call(abi.encodeCall(IERC20Min.transferFrom, (from, address(this), amount)));
        if (!ok || (ret.length != 0 && !abi.decode(ret, (bool)))) revert TransferFailed();
    }

    function _pay(address token, address to, uint256 amount) private {
        bool ok;
        bytes memory ret;
        if (token == address(0)) {
            (ok,) = to.call{value: amount}("");
        } else {
            (ok, ret) = token.call(abi.encodeCall(IERC20Min.transfer, (to, amount)));
            ok = ok && (ret.length == 0 || abi.decode(ret, (bool)));
        }
        if (!ok) revert TransferFailed();
    }
}
