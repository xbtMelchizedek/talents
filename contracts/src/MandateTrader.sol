// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

interface ICoreWriter {
    function sendRawAction(bytes calldata data) external;
}

/// @title MandateTrader: an agent's Hyperliquid trading account with spend caps enforced on-chain.
/// @notice The contract is the HyperCore account. The agent's hot key (`trader`) can only place and
///         cancel orders through CoreWriter, inside the mandate the owner set: allowed perps, a per-order
///         notional cap, a daily notional cap, an optional oracle-band check, an expiry and a pause.
///         The trader cannot move funds. Only the owner can send other actions (e.g. withdrawals).
///         Docs relied on (verified 2026-11-18): CoreWriter at 0x3333...3333, `sendRawAction(bytes)`,
///         encoding = version byte 1 | 3-byte big-endian action id | abi.encode(fields); action 1 =
///         limit order (uint32 asset, bool isBuy, uint64 limitPx, uint64 sz, bool reduceOnly,
///         uint8 tif, uint128 cloid) with px and sz as 1e8 * human value; action 10 = cancel by oid.
///         Read precompile 0x...0807 = perp oracle price. Unaudited. Testnet / local only.
contract MandateTrader {
    ICoreWriter public constant CORE_WRITER = ICoreWriter(0x3333333333333333333333333333333333333333);
    address public constant ORACLE_PX = 0x0000000000000000000000000000000000000807;

    uint24 internal constant ACTION_LIMIT_ORDER = 1;
    uint24 internal constant ACTION_CANCEL_OID = 10;
    uint8 internal constant TIF_ALO = 1;
    uint8 internal constant TIF_IOC = 3;

    struct AssetRule {
        bool allowed;
        uint8 szDecimals; // perp szDecimals, to scale the oracle price (6 - szDecimals decimals) to 1e8
        uint64 maxOrderNotional; // USD * 1e8, at the limit price
        uint16 maxOracleDeviationBps; // 0 = no oracle check
    }

    address public owner;
    address public trader;
    address public guardian; // may pause, never unpause
    uint64 public expiry; // unix seconds; orders refused at or after
    uint64 public dailyNotionalCap; // USD * 1e8, new-risk orders only
    bool public paused;
    bool public reduceOnlyMode;

    mapping(uint32 => AssetRule) public rules;
    mapping(uint256 => uint256) public spentOnDay; // day index => USD * 1e8 placed

    event MandateSet(address trader, address guardian, uint64 expiry, uint64 dailyNotionalCap, bool reduceOnlyMode);
    event RuleSet(uint32 indexed asset, AssetRule rule);
    event Paused(address by);
    event Unpaused();
    event OrderSent(uint32 indexed asset, bool isBuy, uint64 limitPx, uint64 sz, bool reduceOnly, uint8 tif, uint128 cloid, uint256 notional);
    event CancelSent(uint32 indexed asset, uint64 oid);
    event OwnerActionSent(uint24 indexed actionId);

    error NotOwner();
    error NotTrader();
    error NotGuardian();
    error IsPaused();
    error Expired();
    error AssetNotAllowed();
    error BadOrder();
    error OrderTooLarge();
    error DailyCapExceeded();
    error ReduceOnlyMode();
    error OracleBand();
    error OracleUnavailable();

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor(address owner_) {
        owner = owner_;
    }

    // ---------------- owner ----------------

    function setMandate(address trader_, address guardian_, uint64 expiry_, uint64 dailyNotionalCap_, bool reduceOnlyMode_)
        external onlyOwner
    {
        trader = trader_;
        guardian = guardian_;
        expiry = expiry_;
        dailyNotionalCap = dailyNotionalCap_;
        reduceOnlyMode = reduceOnlyMode_;
        emit MandateSet(trader_, guardian_, expiry_, dailyNotionalCap_, reduceOnlyMode_);
    }

    function setRule(uint32 asset, AssetRule calldata rule) external onlyOwner {
        rules[asset] = rule;
        emit RuleSet(asset, rule);
    }

    function unpause() external onlyOwner {
        paused = false;
        emit Unpaused();
    }

    /// @notice Owner-only escape hatch for any other CoreWriter action (transfers, withdrawals, ...).
    function ownerAction(uint24 actionId, bytes calldata abiFields) external onlyOwner {
        CORE_WRITER.sendRawAction(abi.encodePacked(uint8(1), actionId, abiFields));
        emit OwnerActionSent(actionId);
    }

    function transferOwnership(address next) external onlyOwner {
        owner = next;
    }

    // ---------------- guardian ----------------

    function pause() external {
        if (msg.sender != guardian && msg.sender != owner) revert NotGuardian();
        paused = true;
        emit Paused(msg.sender);
    }

    // ---------------- trader ----------------

    function placeLimitOrder(uint32 asset, bool isBuy, uint64 limitPx, uint64 sz, bool reduceOnly, uint8 tif, uint128 cloid)
        external
    {
        if (msg.sender != trader) revert NotTrader();
        if (paused) revert IsPaused();
        if (block.timestamp >= expiry) revert Expired();
        AssetRule memory r = rules[asset];
        if (!r.allowed) revert AssetNotAllowed();
        if (limitPx == 0 || sz == 0 || tif < TIF_ALO || tif > TIF_IOC) revert BadOrder();
        if (reduceOnlyMode && !reduceOnly) revert ReduceOnlyMode();

        uint256 notional = uint256(limitPx) * uint256(sz) / 1e8;
        if (notional > r.maxOrderNotional) revert OrderTooLarge();
        if (!reduceOnly) {
            uint256 day = block.timestamp / 1 days;
            uint256 spent = spentOnDay[day] + notional;
            if (spent > dailyNotionalCap) revert DailyCapExceeded();
            spentOnDay[day] = spent;
        }
        if (r.maxOracleDeviationBps != 0) _checkOracleBand(asset, r, limitPx);

        CORE_WRITER.sendRawAction(
            abi.encodePacked(uint8(1), ACTION_LIMIT_ORDER, abi.encode(asset, isBuy, limitPx, sz, reduceOnly, tif, cloid))
        );
        emit OrderSent(asset, isBuy, limitPx, sz, reduceOnly, tif, cloid, notional);
    }

    /// @notice Cancels lower risk, so they are allowed even when paused or expired.
    function cancelByOid(uint32 asset, uint64 oid) external {
        if (msg.sender != trader && msg.sender != owner && msg.sender != guardian) revert NotTrader();
        CORE_WRITER.sendRawAction(abi.encodePacked(uint8(1), ACTION_CANCEL_OID, abi.encode(asset, oid)));
        emit CancelSent(asset, oid);
    }

    // ---------------- views ----------------

    function remainingToday() external view returns (uint256) {
        uint256 spent = spentOnDay[block.timestamp / 1 days];
        return spent >= dailyNotionalCap ? 0 : dailyNotionalCap - spent;
    }

    function encodeLimitOrder(uint32 asset, bool isBuy, uint64 limitPx, uint64 sz, bool reduceOnly, uint8 tif, uint128 cloid)
        external pure returns (bytes memory)
    {
        return abi.encodePacked(uint8(1), ACTION_LIMIT_ORDER, abi.encode(asset, isBuy, limitPx, sz, reduceOnly, tif, cloid));
    }

    function _checkOracleBand(uint32 asset, AssetRule memory r, uint64 limitPx) internal view {
        (bool ok, bytes memory ret) = ORACLE_PX.staticcall(abi.encode(asset));
        if (!ok || ret.length < 32) revert OracleUnavailable();
        uint256 raw = abi.decode(ret, (uint64));
        if (raw == 0) revert OracleUnavailable();
        // oracle has (6 - szDecimals) decimals; scale to 1e8 human units
        uint256 px = raw * (10 ** (2 + uint256(r.szDecimals)));
        uint256 lo = px * (10_000 - r.maxOracleDeviationBps) / 10_000;
        uint256 hi = px * (10_000 + r.maxOracleDeviationBps) / 10_000;
        if (limitPx < lo || limitPx > hi) revert OracleBand();
    }
}
