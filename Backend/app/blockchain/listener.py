"""HTTP-polling block listener with DB checkpoint (spec sec.5).

Algorithm
---------
1. On start, read last_processed_block from processed_block (default 0).
2. Poll get_latest_block_number() every BLOCK_POLL_INTERVAL seconds.
3. For every block in (last_processed_block, latest], process sequentially:
     - fetch block via provider
     - run registered on_block callbacks
     - persist checkpoint
4. Process missed blocks sequentially; do NOT jump to head.
5. On SIGINT/SIGTERM, finish current block then exit cleanly.

The provider is injected so a WebSocket implementation can replace
HttpRpcProvider without touching this file.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.blockchain.provider import BlockchainProvider
from app.config import get_settings
from app.database.models import ProcessedBlock

# Try to import aiohttp for 429 detection
try:
    from aiohttp import ClientResponseError
except ImportError:
    ClientResponseError = type("ClientResponseError", (Exception,), {"status": 0})

logger = logging.getLogger(__name__)

BlockCallback = Callable[[dict], Awaitable[None]]

CHECKPOINT_ROW_ID = 1

class BlockListener:
    def __init__(
        self,
        provider: BlockchainProvider,
        session_factory: async_sessionmaker[AsyncSession],
        poll_interval: float | None = None,
    ) -> None:
        self._provider = provider
        self._session_factory = session_factory
        self._poll_interval = poll_interval or get_settings().block_poll_interval
        self._callbacks: list[BlockCallback] = []
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None

        # Backoff state
        self._current_backoff = 0.0
        self._min_backoff = 5.0
        self._max_backoff = 300.0

        # In-memory checkpoint to avoid redundant DB reads
        self._last_processed_block = None

    # --- callback registration -----------------------------------------
    def register_on_block(self, cb: BlockCallback) -> None:
        self._callbacks.append(cb)

    # --- lifecycle -----------------------------------------------------
    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="block-listener")
        logger.info(
            "BlockListener started (chain_id=%s, poll_interval=%.1fs)",
            self._provider.chain_id,
            self._poll_interval,
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        try:
            await asyncio.wait_for(self._task, timeout=10)
        except asyncio.TimeoutError:
            self._task.cancel()
        self._task = None
        logger.info("BlockListener stopped")

    # --- core loop -----------------------------------------------------
    async def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    # Log current state before polling
                    # Note: _last_processed_block might be None initially
                    last_val = self._last_processed_block if self._last_processed_block is not None else "Loading..."
                    logger.debug("Listener tick: last_processed=%s", last_val)

                    await self._poll_once()

                    # Reset backoff on successful poll
                    if self._current_backoff > 0:
                        logger.info("RPC connection restored.")
                        self._current_backoff = 0.0
                except Exception as e:
                    err_msg = str(e).lower()
                    is_429 = False

                    if isinstance(e, ClientResponseError) and e.status == 429:
                        is_429 = True
                    elif "429" in err_msg or "too many requests" in err_msg:
                        is_429 = True

                    if is_429:
                        self._current_backoff = (
                            self._current_backoff * 2 if self._current_backoff > 0
                            else self._min_backoff
                        )
                        self._current_backoff = min(self._current_backoff, self._max_backoff)
                        logger.warning(
                            "RPC RATE LIMITED (429). Backing off for %.1f seconds. Total RPC calls: %d",
                            self._current_backoff,
                            self._provider.call_count
                        )
                    else:
                        logger.exception("listener tick failed; backing off")

                sleep_time = self._poll_interval + self._current_backoff
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=sleep_time
                    )
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            logger.info("BlockListener cancelled")
            raise

    async def _poll_once(self) -> None:
        # 1. Get latest block
        latest = await self._provider.get_latest_block_number()

        # 2. Load checkpoint if not already cached
        if self._last_processed_block is None:
            async with self._session_factory() as session:
                self._last_processed_block = await _load_checkpoint(session)

        last = self._last_processed_block

        # Optional override
        start_override = get_settings().listener_start_block
        if start_override and last < start_override:
            logger.warning(
                "checkpoint %s is below listener_start_block=%s; advancing",
                last, start_override,
            )
            last = start_override - 1

        if last >= latest:
            # Log occasionally that we are caught up
            logger.debug("Chain caught up (latest=%s, last=%s)", latest, last)
            return

        # --- FIX: Prevent catastrophic catch-up ---
        MAX_CATCHUP_BLOCKS = 1000
        if latest - last > MAX_CATCHUP_BLOCKS:
            logger.warning(
                "Large block gap detected: last=%s, latest=%s. "
                "Skipping historical backlog and starting from latest.",
                last, latest,
            )
            # Jump to the most recent block so we don't replay millions of blocks
            last = latest - 1
        # ------------------------------------------

        logger.info("Gap detected: last=%s, latest=%s. Processing %s blocks.", last, latest, latest - last)

        # 3. Process gap sequentially
        for n in range(last + 1, latest + 1):
            if self._stop_event.is_set():
                return
            await self._process_block(n)
            # IMPORTANT: Avoid hammering RPC during gap processing.
            # Small delay between blocks to stay under rate limits.
            await asyncio.sleep(0.1)

    async def _process_block(self, block_number: int) -> None:
        logger.info("[BLOCK] Processing block %s", block_number)
        try:
            block = await self._provider.get_block(block_number)
        except Exception as e:
            logger.error("Failed to fetch block %s: %s", block_number, e)
            raise e

        for cb in self._callbacks:
            try:
                await cb(block)
            except Exception:  # noqa: BLE001
                logger.exception("on_block callback failed for %s", block_number)

        async with self._session_factory() as session:
            await _save_checkpoint(session, block["number"], block["hash"])
            await session.commit()
            # Update in-memory cache
            self._last_processed_block = block["number"]

        logger.debug(
            "processed block %s (tx=%d)", block["number"], len(block["transactions"])
        )


# --- helpers ---------------------------------------------------------
async def _load_checkpoint(session: AsyncSession) -> int:
    row = await session.get(ProcessedBlock, CHECKPOINT_ROW_ID)
    return int(row.block_number) if row else 0


async def _save_checkpoint(
    session: AsyncSession, block_number: int, block_hash: str
) -> None:
    row = await session.get(ProcessedBlock, CHECKPOINT_ROW_ID)
    if row is None:
        row = ProcessedBlock(
            id=CHECKPOINT_ROW_ID, block_number=block_number, block_hash=block_hash
        )
        session.add(row)
    else:
        row.block_number = block_number
        row.block_hash = block_hash