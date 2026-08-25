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

Phase 5: also wires the liquidity discovery detector, which probes
Uniswap V2/V3 on Base for pools pairing each confirmed token against
WETH/USDC and records them in ``liquidity_pools``. Adds
``/tokens/{address}/pools``.

Phase 6: also wires the liquidity monitor, which repeatedly
re-checks known pools' reserves/liquidity and records a
``LiquidityEvent`` when one moves sharply. Adds
``/pools/{pool_address}/events``.

Phase 10: also wires the contract bytecode risk detector, which
scans each token's runtime bytecode for known dangerous function
selectors (mint, blacklist, pause, tax/limit setters, upgrade
hooks) and checks ownership status. Adds ``/tokens/{address}/risk``.

Phase 11: also wires the wallet-graph detector, which builds the
edge list ``(deployer -> token, token -> peer token, token -> pool,
token -> transfer_recipient)`` from the data already in the DB plus
one ``eth_getLogs`` call per analyzed token. Adds
``/wallets/{address}``, ``/wallets/{address}/relationships``, and
``/tokens/{address}/wallets`` endpoints.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from sqlalchemy import or_, select

from app.blockchain.listener import BlockListener
from app.blockchain.provider import HttpRpcProvider
from app.config import get_settings
from app.database.database import AsyncSessionLocal, engine
from app.database.models import (
    Base,
    ContractDeployment,
    ContractRiskFlags,
    LiquidityEvent,
    LiquidityPool,
    Token,
    Wallet,
    WalletRelationship,
)
from app.discovery.contract_risk import make_on_block as make_contract_risk_on_block
from app.discovery.deployments import make_on_block
from app.discovery.liquidity import make_on_block as make_liquidity_on_block
from app.discovery.monitor import make_on_block as make_monitor_on_block
from app.discovery.tokens import make_on_block as make_token_on_block
from app.discovery.wallet_graph import make_on_block as make_wallet_graph_on_block


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
    # Phase 5: liquidity pool discovery. Runs after the token
    # detector in the same tick (registration order), so a token
    # confirmed in this block is already in `tokens` by the time
    # the liquidity sweep picks it up.
    listener.register_on_block(
        make_liquidity_on_block(provider=provider, session_factory=AsyncSessionLocal)
    )
    # Phase 6: liquidity monitoring. Runs after discovery in the
    # same tick -- a pool found by discovery this block is already
    # in `liquidity_pools` by the time the monitor's staleness query
    # picks it up (its last_synced_at was set at discovery, so it
    # won't be re-synced again until min_resync_interval_seconds
    # has passed).
    listener.register_on_block(
        make_monitor_on_block(provider=provider, session_factory=AsyncSessionLocal)
    )
    # Phase 10: contract bytecode risk analysis. Independent of the
    # liquidity pipeline -- only depends on a token being confirmed
    # (Phase 4), so it's safe to run in any order relative to
    # discovery/monitor.
    listener.register_on_block(
        make_contract_risk_on_block(provider=provider, session_factory=AsyncSessionLocal)
    )
    # Phase 11: wallet-graph analysis. Runs after Phase 4 (it only
    # considers confirmed tokens) and after Phase 5/6 (it reads from
    # ``liquidity_pools`` for the operates_pool edge). Registration
    # order doesn't strictly matter -- the detector re-queries the
    # DB inside its tick -- but running it last keeps the per-tick
    # RPC budget focused on lighter work first.
    listener.register_on_block(
        make_wallet_graph_on_block(provider=provider, session_factory=AsyncSessionLocal)
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


def _pool_to_dict(p: LiquidityPool) -> dict:
    return {
        "pool_address": p.pool_address,
        "dex": p.dex,
        "pair_asset": p.pair_asset,
        "fee_tier": p.fee_tier,
        "reserve_token": str(int(p.reserve_token)) if p.reserve_token is not None else None,
        "reserve_pair": str(int(p.reserve_pair)) if p.reserve_pair is not None else None,
        "discovered_block": p.discovered_block,
        "discovered_at": p.discovered_at.isoformat() if p.discovered_at else None,
    }


@app.get("/tokens/{address}/pools")
async def token_pools(address: str) -> dict:
    """Phase 5: liquidity pools discovered for a single token.

    Returns an empty list (not 404) when the token exists but has no
    known pools yet -- "checked, found nothing" and "not checked
    yet" are both legitimate states while discovery is in progress.
    404 only when the token itself is unknown.
    """
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
        pools = (
            await session.execute(
                select(LiquidityPool).where(LiquidityPool.token_address == addr)
            )
        ).scalars().all()
    return {
        "token_address": addr,
        "liquidity_checked_at": (
            token.liquidity_checked_at.isoformat() if token.liquidity_checked_at else None
        ),
        "count": len(pools),
        "pools": [_pool_to_dict(p) for p in pools],
    }


def _event_to_dict(e: LiquidityEvent) -> dict:
    return {
        "event_type": e.event_type,
        "metric": e.metric,
        "value_before": str(int(e.value_before)),
        "value_after": str(int(e.value_after)),
        "percent_change": e.percent_change,
        "block_number": e.block_number,
        "detected_at": e.detected_at.isoformat() if e.detected_at else None,
    }


@app.get("/pools/{pool_address}/events")
async def pool_events(pool_address: str) -> dict:
    """Phase 6: liquidity events (large withdrawals/additions) for a pool.

    Returns an empty list (not 404) when the pool exists but has no
    events yet -- most pools never see a sharp move. 404 only when
    the pool itself is unknown.
    """
    addr = pool_address.strip().lower()
    if not addr.startswith("0x") or len(addr) != 42:
        raise HTTPException(
            status_code=400,
            detail=f"invalid EVM address: {pool_address!r}",
        )
    async with AsyncSessionLocal() as session:
        pool = (
            await session.execute(
                select(LiquidityPool).where(LiquidityPool.pool_address == addr)
            )
        ).scalars().first()
        if pool is None:
            raise HTTPException(status_code=404, detail=f"pool not found: {addr}")
        events = (
            await session.execute(
                select(LiquidityEvent)
                .where(LiquidityEvent.pool_address == addr)
                .order_by(LiquidityEvent.detected_at.desc())
            )
        ).scalars().all()
    return {
        "pool_address": addr,
        "last_synced_at": pool.last_synced_at.isoformat() if pool.last_synced_at else None,
        "count": len(events),
        "events": [_event_to_dict(e) for e in events],
    }


def _risk_flags_to_dict(f: ContractRiskFlags) -> dict:
    return {
        "has_mint": f.has_mint,
        "has_blacklist": f.has_blacklist,
        "has_pause": f.has_pause,
        "has_tax_control": f.has_tax_control,
        "has_max_tx_control": f.has_max_tx_control,
        "has_max_wallet_control": f.has_max_wallet_control,
        "has_fee_exclusion_control": f.has_fee_exclusion_control,
        "has_trading_control": f.has_trading_control,
        "is_upgradeable_proxy": f.is_upgradeable_proxy,
        "has_owner_function": f.has_owner_function,
        "owner_address": f.owner_address,
        "owner_renounced": f.owner_renounced,
        "bytecode_size": f.bytecode_size,
        "analyzed_block": f.analyzed_block,
        "analyzed_at": f.analyzed_at.isoformat() if f.analyzed_at else None,
    }


@app.get("/tokens/{address}/risk")
async def token_risk(address: str) -> dict:
    """Phase 10: bytecode-level risk flags for a token's contract.

    NOTE: these flags mean "this function selector is present in the
    contract", not "this token is dangerous" -- see
    ``ContractRiskFlags``'s docstring for the full caveats (no
    parameter-cap analysis, no proxy-implementation resolution).
    Returns 404 only when the token itself is unknown; a known token
    not yet analyzed returns ``analyzed: false``.
    """
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
        flags = (
            await session.execute(
                select(ContractRiskFlags).where(ContractRiskFlags.token_address == addr)
            )
        ).scalars().first()
    if flags is None:
        return {"token_address": addr, "analyzed": False}
    return {"token_address": addr, "analyzed": True, **_risk_flags_to_dict(flags)}


def _wallet_to_dict(w: Wallet) -> dict:
    return {
        "address": w.address,
        "tokens_deployed": w.tokens_deployed,
        "tokens_as_pool": w.tokens_as_pool,
        "tokens_as_transfer": w.tokens_as_transfer,
        "first_seen_block": w.first_seen_block,
        "last_seen_block": w.last_seen_block,
        "first_seen_at": w.first_seen_at.isoformat() if w.first_seen_at else None,
        "last_seen_at": w.last_seen_at.isoformat() if w.last_seen_at else None,
    }


def _edge_to_dict(e: WalletRelationship, *, direction: str) -> dict:
    """Shape one WalletRelationship for JSON.

    ``direction`` is "outgoing" (a == address) or "incoming"
    (b == address). The endpoint always uses the queried address as
    the focal point; the raw ``a``/``b`` columns are preserved so
    the consumer can recover the global edge direction without
    ambiguity.
    """
    return {
        "edge_id": e.id,
        "a": e.a,
        "b": e.b,
        "kind": e.kind,
        "direction": direction,
        "weight": e.weight,
        "first_seen_block": e.first_seen_block,
        "last_seen_block": e.last_seen_block,
        "evidence": e.evidence_json,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


@app.get("/wallets/{address}")
async def wallet_detail(address: str) -> dict:
    """Phase 11: counters and first/last-seen for a single address.

    Returns 404 only when the address has never been observed by
    any detector. "Observed" currently means: appears as a
    deployer, a token contract, a pool, or an ERC-20 Transfer
    recipient during a Phase 11 sweep.
    """
    addr = address.strip().lower()
    if not addr.startswith("0x") or len(addr) != 42:
        raise HTTPException(
            status_code=400,
            detail=f"invalid EVM address: {address!r}",
        )
    async with AsyncSessionLocal() as session:
        wallet = (
            await session.execute(select(Wallet).where(Wallet.address == addr))
        ).scalars().first()
    if wallet is None:
        raise HTTPException(status_code=404, detail=f"wallet not found: {addr}")
    return _wallet_to_dict(wallet)


@app.get("/wallets/{address}/relationships")
async def wallet_relationships(
    address: str,
    limit: int = 100,
    kind: str | None = None,
) -> dict:
    """Phase 11: incoming + outgoing edges for one address.

    ``limit`` caps each direction (outgoing/incoming) at the given
    value, capped to 500 to keep responses bounded. ``kind`` filters
    to a single edge kind (``funds_token`` / ``co_deployed`` /
    ``operates_pool`` / ``transfer_recipient``).

    Returns 404 only when the address is unknown to the wallet
    graph. A known address with zero edges returns an empty list --
    a wallet that just transacted once is still a wallet.
    """
    addr = address.strip().lower()
    if not addr.startswith("0x") or len(addr) != 42:
        raise HTTPException(
            status_code=400,
            detail=f"invalid EVM address: {address!r}",
        )
    limit = max(1, min(limit, 500))

    base_out = select(WalletRelationship).where(WalletRelationship.a == addr)
    base_in = select(WalletRelationship).where(WalletRelationship.b == addr)
    if kind is not None:
        base_out = base_out.where(WalletRelationship.kind == kind)
        base_in = base_in.where(WalletRelationship.kind == kind)

    async with AsyncSessionLocal() as session:
        wallet = (
            await session.execute(select(Wallet).where(Wallet.address == addr))
        ).scalars().first()
        if wallet is None:
            raise HTTPException(status_code=404, detail=f"wallet not found: {addr}")

        outgoing = (
            await session.execute(
                base_out.order_by(WalletRelationship.weight.desc()).limit(limit)
            )
        ).scalars().all()
        incoming = (
            await session.execute(
                base_in.order_by(WalletRelationship.weight.desc()).limit(limit)
            )
        ).scalars().all()

    return {
        "address": addr,
        "outgoing_count": len(outgoing),
        "incoming_count": len(incoming),
        "outgoing": [_edge_to_dict(e, direction="outgoing") for e in outgoing],
        "incoming": [_edge_to_dict(e, direction="incoming") for e in incoming],
    }


@app.get("/tokens/{address}/wallets")
async def token_wallets(address: str) -> dict:
    """Phase 11: wallets related to a token.

    Returns edges where the token is either endpoint (``a`` or
    ``b``). This is the token-centric counterpart to
    ``/wallets/{address}/relationships`` -- useful for the
    dashboard's "who is connected to this token?" view without
    forcing the frontend to also query by the deployer/pool
    addresses.
    """
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
        edges = (
            await session.execute(
                select(WalletRelationship)
                .where(
                    or_(
                        WalletRelationship.a == addr,
                        WalletRelationship.b == addr,
                    )
                )
                .order_by(WalletRelationship.kind.asc(), WalletRelationship.weight.desc())
            )
        ).scalars().all()
    return {
        "token_address": addr,
        "wallet_graph_analyzed_at": (
            token.wallet_graph_analyzed_at.isoformat()
            if token.wallet_graph_analyzed_at else None
        ),
        "count": len(edges),
        "relationships": [
            {
                "a": e.a,
                "b": e.b,
                "kind": e.kind,
                "weight": e.weight,
                "first_seen_block": e.first_seen_block,
                "last_seen_block": e.last_seen_block,
                "evidence": e.evidence_json,
            }
            for e in edges
        ],
    }
