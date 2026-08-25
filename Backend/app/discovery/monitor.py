"""Liquidity monitoring (spec sec.9).

Unlike Phase 5 (a one-shot discovery sweep per token), this detector
repeatedly re-visits every known pool to catch reserve/liquidity
changes over time.

Algorithm
---------
1. On every block, select pools where ``last_synced_at`` is NULL or
   older than ``min_resync_interval_seconds`` (default 60s -- no
   point re-querying the same pool every ~2s block when its reserves
   realistically don't move that often), ordered oldest-synced-first
   so every pool gets revisited roughly round-robin, capped at
   ``batch_size``.
2. Re-fetch the tracked magnitude:
     - V2: ``getReserves()``, oriented via the pool's stored
       ``is_token0`` flag (captured once at Phase 5 discovery --
       V2 pairs are immutable, so this never needs re-querying).
     - V3: ``liquidity()`` -- the pool's current active liquidity.
       Not a token amount (concentrated liquidity has no single
       reserve figure), but a valid magnitude for detecting a large
       withdrawal.
3. Compare the new value against the pool's previously stored value.
   If it dropped by more than ``withdrawal_threshold`` (default 50%),
   or rose by more than ``addition_threshold`` (default 100%, i.e.
   more than doubled), record a ``LiquidityEvent``. No event on the
   very first sync (nothing to compare against) or when the previous
   value was zero (percent change is undefined/infinite).
4. Update the pool's stored value(s) and ``last_synced_at``
   regardless of whether an event fired. On a transport error, leave
   ``last_synced_at`` untouched so that pool is retried next tick
   rather than silently skipped.

Per spec sec.9, this module deliberately stops at "flag the change" --
it does NOT attempt to determine who withdrew, whether that wallet is
the deployer, or whether the price collapsed. That needs event-log
and wallet analysis the current ``BlockchainProvider`` doesn't expose
(no ``get_logs``), and belongs downstream of this detector, not in it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.blockchain.provider import BlockchainProvider
from app.database.models import LiquidityEvent, LiquidityPool

logger = logging.getLogger(__name__)

SELECTOR_LIQUIDITY = "0x1a686502"  # liquidity()  (Uniswap V3 pool)

DEFAULT_BATCH_SIZE = 20
DEFAULT_MIN_RESYNC_INTERVAL_SECONDS = 60
DEFAULT_WITHDRAWAL_THRESHOLD = 0.5   # 50% drop
DEFAULT_ADDITION_THRESHOLD = 1.0     # 100% increase (more than doubled)


def make_on_block(
    provider: BlockchainProvider,
    session_factory: async_sessionmaker[AsyncSession],
    batch_size: int = DEFAULT_BATCH_SIZE,
    min_resync_interval_seconds: int = DEFAULT_MIN_RESYNC_INTERVAL_SECONDS,
    withdrawal_threshold: float = DEFAULT_WITHDRAWAL_THRESHOLD,
    addition_threshold: float = DEFAULT_ADDITION_THRESHOLD,
):
    """Return an ``on_block(block)`` callback that monitors pool liquidity.

    Register alongside the Phase 5 discovery callback -- discovery
    finds new pools, this one watches pools that already exist.
    """

    async def on_block(block: dict) -> None:
        async with session_factory() as session:
            cutoff = datetime.now(timezone.utc) - timedelta(
                seconds=min_resync_interval_seconds
            )
            rows = (
                await session.execute(
                    select(LiquidityPool)
                    .where(
                        (LiquidityPool.last_synced_at.is_(None))
                        | (LiquidityPool.last_synced_at < cutoff)
                    )
                    .order_by(LiquidityPool.last_synced_at.asc().nulls_first())
                    .limit(batch_size)
                )
            ).scalars().all()
            if not rows:
                return

            synced = 0
            events = 0
            now = datetime.now(timezone.utc)
            block_number = int(block["number"])
            for pool in rows:
                try:
                    if pool.dex == "uniswap_v2":
                        new_value = await _resync_v2(provider, pool)
                    elif pool.dex == "uniswap_v3":
                        new_value = await _resync_v3(provider, pool)
                    else:
                        # Unknown dex -- nothing this detector knows
                        # how to monitor; mark synced so it doesn't
                        # get retried forever.
                        pool.last_synced_at = now
                        synced += 1
                        continue
                except Exception:  # noqa: BLE001
                    # Transport/decoding failure -- leave
                    # last_synced_at untouched, retry next tick.
                    continue

                old_value = pool.reserve_token
                if old_value is not None and int(old_value) > 0:
                    percent_change = (new_value - int(old_value)) / int(old_value)
                    event_type = None
                    if percent_change <= -withdrawal_threshold:
                        event_type = "withdrawal"
                    elif percent_change >= addition_threshold:
                        event_type = "addition"
                    if event_type is not None:
                        session.add(
                            LiquidityEvent(
                                pool_address=pool.pool_address,
                                event_type=event_type,
                                metric=(
                                    "reserve_token"
                                    if pool.dex == "uniswap_v2"
                                    else "v3_liquidity"
                                ),
                                value_before=int(old_value),
                                value_after=new_value,
                                percent_change=percent_change,
                                block_number=block_number,
                                detected_at=now,
                            )
                        )
                        events += 1

                pool.reserve_token = new_value
                pool.last_synced_at = now
                synced += 1

            if synced:
                logger.info(
                    "block %s: liquidity monitor synced %d pool(s), %d event(s)",
                    block_number, synced, events,
                )
            await session.commit()

    return on_block


async def _resync_v2(provider: BlockchainProvider, pool: LiquidityPool) -> int:
    """Re-fetch a V2 pair's reserves and return the token-side value.

    Also updates ``pool.reserve_pair`` in place (the caller commits).
    Orientation uses the ``is_token0`` flag captured at discovery --
    no need to re-query ``token0()``, V2 pairs are immutable.
    """
    raw = await provider.get_eth_call(pool.pool_address, "0x0902f1ac")  # getReserves()
    if len(raw) < 64:
        raise ValueError(f"getReserves() response too short: {len(raw)}")
    reserve0 = int.from_bytes(raw[0:32], "big")
    reserve1 = int.from_bytes(raw[32:64], "big")
    if pool.is_token0:
        reserve_token, reserve_pair = reserve0, reserve1
    else:
        reserve_token, reserve_pair = reserve1, reserve0
    pool.reserve_pair = reserve_pair
    return reserve_token


async def _resync_v3(provider: BlockchainProvider, pool: LiquidityPool) -> int:
    """Re-fetch a V3 pool's current active ``liquidity()`` value."""
    raw = await provider.get_eth_call(pool.pool_address, SELECTOR_LIQUIDITY)
    if len(raw) < 32:
        raise ValueError(f"liquidity() response too short: {len(raw)}")
    return int.from_bytes(raw[0:32], "big")
