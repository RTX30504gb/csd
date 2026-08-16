"""FastAPI app shell.

Phase 2: only /health and /chain-info. The block listener is started
in the lifespan context manager so it shares the process.

Phase 3: wires the contract-deployment detector as an ``on_block``
callback so every new block is scanned for contract creations.

Phase 4: also wires the ERC-20 detector, which probes each
un-flagged deployment for name/symbol/decimals/totalSupply and
records confirmed tokens in the ``tokens`` table. Adds ``/tokens``
and ``/tokens/{address}`` endpoints so the frontend (and integration
tests) can read what the detector has classified.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from sqlalchemy import select

from app.blockchain.listener import BlockListener
from app.blockchain.provider import HttpRpcProvider
from app.config import get_settings
from app.database.database import AsyncSessionLocal, engine
from app.database.models import Base, ContractDeployment, Token
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


def _token_to_dict(t: Token, deployment: ContractDeployment | None = None) -> dict:
    """Shape one Token (+ optionally its underlying deployment) for JSON.

    Centralised so ``/tokens/recent`` and ``/tokens/{address}`` stay
    in lockstep. The frontend will be the primary consumer; the keys
    here are the wire contract.
    """
    # total_supply is stored as Numeric(78, 0) (uint256 range), which
    # SQLAlchemy surfaces as a Decimal. ``str(decimal)`` formats
    # with scientific notation for large values (1e18 -> '1E+18');
    # we need the plain integer string so the frontend can parse it
    # back as BigInt. ``int(d)`` does the conversion safely.
    if t.total_supply is None:
        total_supply_str: str | None = None
    else:
        total_supply_str = str(int(t.total_supply))
    out = {
        "contract_address": t.contract_address,
        "deployer": t.deployer,
        "name": t.name,
        "symbol": t.symbol,
        "decimals": t.decimals,
        "total_supply": total_supply_str,
        "creation_block": t.creation_block,
        "creation_timestamp": t.creation_timestamp.isoformat() if t.creation_timestamp else None,
        "detected_at": t.detected_at.isoformat() if t.detected_at else None,
    }
    if deployment is not None:
        out["deployment"] = {
            "creation_tx": deployment.creation_tx,
            "created_at": deployment.created_at.isoformat() if deployment.created_at else None,
            "is_erc20": deployment.is_erc20,
            "erc20_checked_at": (
                deployment.erc20_checked_at.isoformat() if deployment.erc20_checked_at else None
            ),
        }
    return out


@app.get("/tokens/recent")
async def tokens_recent(limit: int = 20) -> dict:
    """Phase 4: return the most recently detected tokens.

    Limited to avoid unbounded responses. Phase 5 will replace
    this with paginated + filterable queries.
    """
    limit = max(1, min(limit, 100))
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(Token).order_by(Token.detected_at.desc()).limit(limit)
            )
        ).scalars().all()
    return {
        "count": len(rows),
        "tokens": [_token_to_dict(t) for t in rows],
    }


@app.get("/tokens/{address}")
async def token_detail(address: str) -> dict:
    """Phase 4: full record for a single token, by contract address.

    Looks up by lower-cased EVM address (case-insensitive on the
    wire) and 404s if the contract has not been classified as an
    ERC-20. The corresponding ``contract_deployments`` row is
    joined in so the caller gets the deploy tx hash and the
    detector's bookkeeping flags in the same response.
    """
    # Normalise: strip, lower-case, but keep the 0x prefix so the
    # address validates as a 42-char EVM address.
    addr = address.strip().lower()
    if not addr.startswith("0x") or len(addr) != 42:
        raise HTTPException(
            status_code=400,
            detail=f"invalid EVM address: {address!r}",
        )
    async with AsyncSessionLocal() as session:
        token = (
            await session.execute(select(Token).where(Token.contract_address == addr))
        ).scalars().first()
        if token is None:
            raise HTTPException(status_code=404, detail=f"token not found: {addr}")
        deployment = (
            await session.execute(
                select(ContractDeployment).where(
                    ContractDeployment.contract_address == addr
                )
            )
        ).scalars().first()
    return _token_to_dict(token, deployment)
