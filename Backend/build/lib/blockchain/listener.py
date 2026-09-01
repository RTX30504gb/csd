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
                    await self._poll_once()
                except Exception:  # noqa: BLE001
                    logger.exception("listener tick failed; backing off")
                # sleep, but wake promptly on stop
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self._poll_interval
                    )
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            logger.info("BlockListener cancelled")
            raise

    async def _poll_once(self) -> None:
        latest = await self._provider.get_latest_block_number()
        async with self._session_factory() as session:
            last = await _load_checkpoint(session)
        # Optional override: skip ahead if persisted checkpoint is below
        # listener_start_block (configured in .env). Production should
        # leave listener_start_block=0 so the listener always resumes
        # from the last checkpoint. Smoke tests / backfills set this to
        # a recent block so we don't try to walk the entire chain.
        start_override = get_settings().listener_start_block
        if start_override and last < start_override:
            logger.warning(
                "checkpoint %s is below listener_start_block=%s; advancing",
                last, start_override,
            )
            last = start_override - 1
        if last >= latest:
            return
        # process gap sequentially
        for n in range(last + 1, latest + 1):
            if self._stop_event.is_set():
                return
            await self._process_block(n)

    async def _process_block(self, block_number: int) -> None:
        block = await self._provider.get_block(block_number)
        # run callbacks
        for cb in self._callbacks:
            try:
                await cb(block)
            except Exception:  # noqa: BLE001
                logger.exception("on_block callback failed for %s", block_number)
        # persist checkpoint AFTER callbacks succeed
        async with self._session_factory() as session:
            await _save_checkpoint(session, block["number"], block["hash"])
            await session.commit()
        logger.info(
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