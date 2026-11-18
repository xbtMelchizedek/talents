// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {Test} from "forge-std/Test.sol";
import {MandateTrader} from "../../src/MandateTrader.sol";
import {OraclePrecompileMock} from "../utils/Mocks.sol";

/// @dev Differential handler: every order attempt is first judged by an independent model of the
///      mandate, then sent. If the contract and the model disagree in either direction, that is
///      counted. Handler functions never revert, so the suite runs with fail_on_revert = true.
contract MandateTraderHandler is Test {
    uint16 internal constant MAX_BPS = 10_000;
    uint32 public constant BTC = 3;
    uint32 public constant ETH = 4;
    uint32 public constant NOT_LISTED = 7;

    MandateTrader public immutable trader;
    OraclePrecompileMock public immutable oracle;
    address public immutable owner;
    address public immutable agent;
    address public immutable guardian;
    address public immutable stranger;

    uint256 public ghostOrders;
    uint256 public ghostCancels;
    uint256 public breaches; // contract accepted what the model refused
    uint256 public mismatches; // contract refused what the model accepted
    mapping(uint256 => uint256) public ghostSpent;
    uint256[] internal touchedDays;
    mapping(uint256 => bool) internal touched;
    mapping(bytes32 => uint256) public calls;

    constructor(
        MandateTrader trader_,
        OraclePrecompileMock oracle_,
        address owner_,
        address agent_,
        address guardian_
    ) {
        trader = trader_;
        oracle = oracle_;
        owner = owner_;
        agent = agent_;
        guardian = guardian_;
        stranger = makeAddr("stranger");
    }

    // ---------------- handlers ----------------

    /// @dev Adversarial shapes: any asset, price, size and tif, including zero and out of band.
    function place(uint256 assetSeed, bool isBuy, uint256 px, uint256 sz, bool reduceOnly, uint256 tif) external {
        uint32 asset = _asset(assetSeed);
        uint64 limitPx = uint64(bound(px, 0, 150_000e8));
        uint64 size = uint64(bound(sz, 0, 1e8));
        _attempt(asset, isBuy, limitPx, size, reduceOnly, uint8(bound(tif, 0, 4)));
        ++calls["place"];
    }

    /// @dev Orders a real agent would send: listed asset, near the mark, $1 to the per-order cap.
    ///      These push daily spend up against the cap, where off-by-one bugs live.
    function placeWithinMandate(uint256 assetSeed, bool isBuy, uint256 px, uint256 notional, uint256 flags) external {
        bool btc = assetSeed % 2 == 0;
        uint64 limitPx;
        uint256 targetNotional;
        if (btc) {
            uint256 mark = uint256(oracle.raw()) * 1e7; // szDecimals 5 => 10 ** (2 + 5)
            limitPx = uint64(bound(px, mark * 985 / 1000, mark * 1015 / 1000));
            targetNotional = bound(notional, 1e8, 250e8);
        } else {
            limitPx = uint64(bound(px, 1_000e8, 5_000e8));
            targetNotional = bound(notional, 1e8, 400e8);
        }
        uint64 size = uint64(targetNotional * 1e8 / limitPx);
        if (size == 0) size = 1;
        _attempt(btc ? BTC : ETH, isBuy, limitPx, size, flags % 4 == 0, uint8(1 + flags % 3));
        ++calls["placeWithinMandate"];
    }

    function _attempt(uint32 asset, bool isBuy, uint64 limitPx, uint64 size, bool reduceOnly, uint8 tif) internal {
        (bool allowed, uint256 notional) = _model(asset, limitPx, size, reduceOnly, tif);
        vm.prank(agent);
        try trader.placeLimitOrder(asset, isBuy, limitPx, size, reduceOnly, tif, 0) {
            if (!allowed) {
                ++breaches;
                return;
            }
            ++ghostOrders;
            if (!reduceOnly) _spend(notional);
        } catch {
            if (allowed) ++mismatches;
        }
    }

    /// @dev Nobody but the trader key may place an order, even one the mandate would allow.
    function strangerPlace(uint256 senderSeed, uint256 notional) external {
        address sender = [stranger, owner, guardian][senderSeed % 3];
        uint64 size = uint64(bound(notional, 1e8, 400e8) * 1e8 / 2_000e8); // ETH at $2,000, inside caps
        vm.prank(sender);
        try trader.placeLimitOrder(ETH, true, 2_000e8, size, true, 1, 0) {
            ++breaches;
        } catch {}
        ++calls["strangerPlace"];
    }

    /// @dev Strangers may never send any other CoreWriter action.
    function strangerOwnerAction(uint24 actionId, bytes calldata fields) external {
        vm.prank(stranger);
        try trader.ownerAction(actionId, fields) {
            ++breaches;
        } catch {}
        vm.prank(agent);
        try trader.ownerAction(actionId, fields) {
            ++breaches;
        } catch {}
        ++calls["strangerOwnerAction"];
    }

    function cancel(uint64 oid) external {
        vm.prank(agent);
        trader.cancelByOid(BTC, oid);
        ++ghostCancels;
        ++calls["cancel"];
    }

    function pause(uint256 seed) external {
        if (seed % 16 != 0) return; // keep the system mostly live so orders get exercised
        vm.prank(guardian);
        trader.pause();
        ++calls["pause"];
    }

    function unpause() external {
        vm.prank(owner);
        trader.unpause();
        ++calls["unpause"];
    }

    function moveOracle(uint256 raw) external {
        oracle.set(uint64(bound(raw, 500_000, 800_000)));
        ++calls["moveOracle"];
    }

    function warp(uint256 seconds_) external {
        vm.warp(block.timestamp + bound(seconds_, 0, 4 hours));
        ++calls["warp"];
    }

    // ---------------- views for the invariant suite ----------------

    function touchedDayCount() external view returns (uint256) {
        return touchedDays.length;
    }

    function touchedDay(uint256 i) external view returns (uint256) {
        return touchedDays[i];
    }

    // ---------------- model ----------------

    /// @dev Written from the mandate's spec, not from the contract body. The oracle band uses a
    ///      different but equivalent integer form: lo = px - ceil(px*d/MAX), hi = px + floor(px*d/MAX).
    function _model(uint32 asset, uint64 limitPx, uint64 size, bool reduceOnly, uint8 tif)
        internal
        view
        returns (bool allowed, uint256 notional)
    {
        (bool listed, uint8 szDecimals, uint64 maxOrderNotional, uint16 deviationBps) = trader.rules(asset);
        if (trader.paused() || block.timestamp >= trader.expiry()) return (false, 0);
        if (!listed || limitPx == 0 || size == 0 || tif < 1 || tif > 3) return (false, 0);
        if (trader.reduceOnlyMode() && !reduceOnly) return (false, 0);

        notional = (uint256(limitPx) / 1e8) * size + (uint256(limitPx) % 1e8) * size / 1e8;
        if (notional > maxOrderNotional) return (false, notional);

        if (deviationBps != 0) {
            uint256 mark = uint256(oracle.raw()) * 10 ** (2 + uint256(szDecimals));
            uint256 band = mark * deviationBps;
            uint256 lo = mark - (band + MAX_BPS - 1) / MAX_BPS;
            uint256 hi = mark + band / MAX_BPS;
            if (limitPx < lo || limitPx > hi) return (false, notional);
        }

        if (!reduceOnly && ghostSpent[block.timestamp / 1 days] + notional > trader.dailyNotionalCap()) {
            return (false, notional);
        }
        return (true, notional);
    }

    function _spend(uint256 notional) internal {
        uint256 day = block.timestamp / 1 days;
        ghostSpent[day] += notional;
        if (!touched[day]) {
            touched[day] = true;
            touchedDays.push(day);
        }
    }

    function _asset(uint256 seed) internal pure returns (uint32) {
        uint256 pick = seed % 3;
        if (pick == 0) return BTC;
        if (pick == 1) return ETH;
        return NOT_LISTED;
    }
}
