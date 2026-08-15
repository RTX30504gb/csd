"""Smoke test for the block listener.

Prereqs:
    docker compose up -d           # postgres + redis
    pip install -e .[dev]          # or use uv
    cp ../.env.example .env        # or set env vars manually

Run:
    python -m tests.test_listener
or:
    pytest tests/test_listener.py -s

What it does
------------
Resets the DB checkpoint, then runs the listener for ~15s against a
small RECENT window of Base (head-50 .. head). This lets us verify
end-to-end that the listener + deployment detector see real, dense
blocks (in seconds, not days). Production should leave
LISTENER_START_BLOCK=0 so the listener always resumes from its last
checkpoint.
"""
import asyncio
import logging
import os

# Set BEFORE any app import that triggers get_settings()'s lru_cache.
# Window size: walk the last N blocks of Base. 50 blocks is ~100s of
# real time and guaranteed to contain a deployment on a healthy day.
WINDOW = int(os.environ.get("SMOKE_WINDOW", "50"))
os.environ.setdefault("LISTENER_START_BLOCK", "0")  # filled in main() after we know head
os.environ.setdefault("BLOCK_POLL_INTERVAL", "1.0")

from sqlalchemy import func, select  # noqa: E402

from app.blockchain.listener import BlockListener  # noqa: E402
from app.blockchain.provider import HttpRpcProvider  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.database.database import AsyncSessionLocal, engine  # noqa: E402
from app.database.models import Base, ContractDeployment, ProcessedBlock  # noqa: E402
from app.discovery.deployments import make_on_block  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("test_listener")


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    provider = HttpRpcProvider()
    head = await provider.get_latest_block_number()
    start_block = max(1, head - WINDOW)
    log.info("head=%s, smoke window: blocks %s..%s (%s blocks)",
             head, start_block, head, head - start_block + 1)

    # Wipe the checkpoint so listener_start_block takes effect this run.
    async with AsyncSessionLocal() as session:
        await session.execute(ProcessedBlock.__table__.delete())
        await session.commit()

    # Now set the override and rebuild the settings cache.
    os.environ["LISTENER_START_BLOCK"] = str(start_block)
    get_settings.cache_clear()

    listener = BlockListener(provider=provider, session_factory=AsyncSessionLocal)
    listener.register_on_block(
        make_on_block(provider=provider, session_factory=AsyncSessionLocal)
    )

    seen: list[int] = []

    async def on_block(block: dict) -> None:
        log.info("callback: block=%s tx=%d", block["number"], len(block["transactions"]))
        seen.append(block["number"])

    listener.register_on_block(on_block)
    await listener.start()
    try:
        await asyncio.sleep(15)  # run for ~15s
    finally:
        await listener.stop()
        await engine.dispose()

    # verify checkpoint persisted
    async with AsyncSessionLocal() as session:
        row = (await session.execute(select(ProcessedBlock))).scalar_one_or_none()
        total = (
            await session.execute(select(func.count()).select_from(ContractDeployment))
        ).scalar_one()
        recent = (
            await session.execute(
                select(ContractDeployment.contract_address, ContractDeployment.deployer)
                .order_by(ContractDeployment.creation_block.desc())
                .limit(5)
            )
        ).fetchall()
    log.info("checkpoint: %s", row.block_number if row else None)
    log.info("blocks seen by callback: %d (first/last: %s/%s)",
             len(seen), seen[0] if seen else None, seen[-1] if seen else None)
    log.info("total deployments in DB: %d", total)
    for r in recent:
        log.info("  recent deployment: addr=%s deployer=%s", r.contract_address, r.deployer)


if __name__ == "__main__":
    asyncio.run(main())
