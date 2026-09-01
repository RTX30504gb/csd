"""Address classification (spec sec.13).

Classifies an address into one of the categories spec sec.13 lists,
in priority order (most specific / most confidently-known first):

  1. burn            -- 0x0 or the conventional 0x...dEaD burn address
  2. dex_pool         -- matches liquidity_pools.pool_address (we
                          discovered it ourselves in Phase 5/8)
  3. dex_router       -- matches a hard-coded, verified router address
  4. bridge           -- matches a hard-coded, verified bridge address
  5. deployer         -- appears as ContractDeployment.deployer
  6. deployer_associated -- has an edge in wallet_relationships to/from
                          a deployer (funds_token, co_deployed, etc.)
  7. contract         -- eth_getCode is non-empty, but none of the above
  8. eoa              -- eth_getCode is empty
  9. unknown          -- fallback (should be rare; only if get_code fails)

What this deliberately does NOT include: a curated "exchange" hot
wallet list. Centralized exchange hot wallets churn constantly and
are not something we can verify independently the way a factory-
deployed router or an official L2 bridge address can be verified
against docs. Shipping a stale/wrong exchange list in a security
tool is worse than leaving the category empty -- so "exchange" is a
defined category in the return type, but the address list backing
it is intentionally empty and documented as a TODO, not populated
with guesses.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.blockchain.provider import BlockchainProvider
from app.database.models import ContractDeployment, LiquidityPool, WalletRelationship

ZERO_ADDRESS = "0x" + "00" * 20
BURN_ADDRESSES: frozenset[str] = frozenset({
    ZERO_ADDRESS,
    "0x000000000000000000000000000000000000dead",
})

# Verified against official docs/Uniswap deployments pages, not memory
# recall -- see contract_risk.py / liquidity.py module comments for
# the same discipline applied to factory addresses.
KNOWN_DEX_ROUTERS: frozenset[str] = frozenset({
    "0x4752ba5dbc23f44d87826276bf6fd6b1c372ad24",  # Uniswap V2 Router02 (Base)
    "0x2626664c2603336e57b271c5c0b26f421741e481",  # Uniswap V3 SwapRouter02 (Base)
    "0x198ef79f1f515f02dfe9e3115ed9fc07183f02fc",  # Uniswap Universal Router (Base)
})

# Base's own canonical predeploy bridge -- verified against docs.base.org
# and the OP Stack spec. NOT an exhaustive third-party bridge list
# (Across, Stargate, Wormhole, etc. are not included -- same
# "don't guess" discipline as the exchange list).
KNOWN_BRIDGES: frozenset[str] = frozenset({
    "0x4200000000000000000000000000000000000010",  # L2StandardBridge (Base predeploy)
})

# Edge kinds from wallet_graph.py (spec sec.15) that indicate a
# meaningful relationship to a deployer, for "deployer_associated".
DEPLOYER_ASSOCIATION_EDGE_KINDS = (
    "funds_token",
    "co_deployed",
    "operates_pool",
    "transfer_recipient",
)

CATEGORY_BURN = "burn"
CATEGORY_DEX_POOL = "dex_pool"
CATEGORY_DEX_ROUTER = "dex_router"
CATEGORY_BRIDGE = "bridge"
CATEGORY_EXCHANGE = "exchange"  # defined, currently unpopulated -- see module docstring
CATEGORY_DEPLOYER = "deployer"
CATEGORY_DEPLOYER_ASSOCIATED = "deployer_associated"
CATEGORY_CONTRACT = "contract"
CATEGORY_EOA = "eoa"
CATEGORY_UNKNOWN = "unknown"


def _lower(addr: str) -> str:
    return addr.lower() if addr else addr


async def classify_address(
    address: str,
    session: AsyncSession,
    provider: BlockchainProvider | None = None,
) -> dict:
    """Classify a single address. Returns a dict with ``category`` plus
    any supporting detail (e.g. which pool/router it matched).

    ``provider`` is optional: if omitted, the EOA-vs-contract check
    (which needs ``eth_getCode``) is skipped and such addresses fall
    through to ``unknown`` with a note, rather than the caller being
    forced to have chain access just to check DB-only categories.
    """
    addr = _lower(address)

    if addr in BURN_ADDRESSES:
        return {"category": CATEGORY_BURN, "matched": addr}

    if addr in KNOWN_DEX_ROUTERS:
        return {"category": CATEGORY_DEX_ROUTER, "matched": addr}

    if addr in KNOWN_BRIDGES:
        return {"category": CATEGORY_BRIDGE, "matched": addr}

    pool = (
        await session.execute(
            select(LiquidityPool.pool_address).where(LiquidityPool.pool_address == addr)
        )
    ).scalars().first()
    if pool is not None:
        return {"category": CATEGORY_DEX_POOL, "matched": addr}

    deployment = (
        await session.execute(
            select(ContractDeployment.contract_address)
            .where(ContractDeployment.deployer == addr)
            .limit(1)
        )
    ).scalars().first()
    if deployment is not None:
        return {"category": CATEGORY_DEPLOYER, "matched": addr}

    edge = (
        await session.execute(
            select(WalletRelationship.kind)
            .where(
                ((WalletRelationship.a == addr) | (WalletRelationship.b == addr))
                & WalletRelationship.kind.in_(DEPLOYER_ASSOCIATION_EDGE_KINDS)
            )
            .limit(1)
        )
    ).scalars().first()
    if edge is not None:
        return {"category": CATEGORY_DEPLOYER_ASSOCIATED, "matched": addr, "via_edge": edge}

    if provider is not None:
        try:
            code = await provider.get_code(addr)
        except Exception:  # noqa: BLE001
            return {"category": CATEGORY_UNKNOWN, "reason": "get_code failed"}
        if len(code) > 0:
            return {"category": CATEGORY_CONTRACT}
        return {"category": CATEGORY_EOA}

    return {"category": CATEGORY_UNKNOWN, "reason": "no provider supplied for code check"}
