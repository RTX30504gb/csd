"""FastAPI app shell.

Phase 2: only /health and /chain-info. The block listener is started
in the lifespan context manager so it shares the process.

Phase 3: wires the contract-deployment detector as an ``on_block``
callback so every new block is scanned for contract creations.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.blockchain.listener import BlockListener
from app.blockchain.provider import HttpRpcProvider
from app.config import get_settings
from app.database.database import AsyncSessionLocal, engine
from app.database.models import Base
from app.discovery.deployments import make_on_block


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ensure schema exists (Phase 2 only; Alembic in later phase)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    provider = HttpRpcProvider()
    listener = BlockListener(provider=provider, session_factory=AsyncSessionLocal)
    # Phase 3: register the deployment detector. It is callback-shaped
    # so the listener remains chain-agnostic.
    listener.register_on_block(
        make_on_block(provider=provider, session_factory=AsyncSessionLocal)
    )
    await listener.start()

    app.state.block_listener = listener
    try:
        yield
    finally:
        await listener.stop()
        await engine.dispose()


app = FastAPI(title="rug-detector", lifespan=lifespan)
_settings = get_settings()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "env": _settings.app_env}


@app.get("/chain-info")
async def chain_info() -> dict:
    provider = HttpRpcProvider()
    head = await provider.get_latest_block_number()
    return {"chain_id": provider.chain_id, "latest_block": head}
