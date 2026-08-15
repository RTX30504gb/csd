"""FastAPI app shell.

Phase 2: only /health and /chain-info. The block listener is started
in the lifespan context manager so it shares the process.

Phase 3: wires the contract-deployment detector as an ``on_block``
callback so every new block is scanned for contract creations.

Phase 4: also wires the ERC-20 detector, which probes each
un-flagged deployment for name/symbol/decimals/totalSupply and
records confirmed tokens in the ``tokens`` table.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.blockchain.listener import BlockListener
from app.blockchain.provider import HttpRpcProvider
from app.config import get_settings
from app.database.database import AsyncSessionLocal, engine
from app.database.models import Base
from app.discovery.deployments import make_on_block
from app.discovery.tokens import make_on_block as make_token_on_block


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
    # Phase 4: also register the ERC-20 detector. It runs after the
    # deployment detector in the same tick (registration order), so
    # any deployment just observed in this block is already in the
    # DB by the time the token detector picks it up.
    listener.register_on_block(
        make_token_on_block(provider=provider, session_factory=AsyncSessionLocal)
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


@app.get("/tokens/recent")
async def tokens_recent(limit: int = 20) -> dict:
    """Phase 4: return the most recently detected tokens.

    Limited to avoid unbounded responses. Phase 5 will replace
    this with paginated + filterable queries.
    """
    from sqlalchemy import select

    from app.database.models import Token

    limit = max(1, min(limit, 100))
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(Token).order_by(Token.detected_at.desc()).limit(limit)
            )
        ).scalars().all()
    return {
        "count": len(rows),
        "tokens": [
            {
                "contract_address": t.contract_address,
                "deployer": t.deployer,
                "name": t.name,
                "symbol": t.symbol,
                "decimals": t.decimals,
                "total_supply": str(t.total_supply) if t.total_supply is not None else None,
                "creation_block": t.creation_block,
                "detected_at": t.detected_at.isoformat() if t.detected_at else None,
            }
            for t in rows
        ],
    }
