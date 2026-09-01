"""Liquidity pool discovery (spec sec.8).

Algorithm
---------
1. On every block the listener hands us, scan ``tokens`` for rows
   where ``liquidity_checked_at IS NULL``, ordered by id, capped at
   ``batch_size`` (default 20) -- same batching rationale as the
   Phase 4 ERC-20 detector: keep a single tick bounded against the
   public RPC's rate limit, and let any backlog clear over
   subsequent blocks.
2. For each token, probe the well-known Base DEX factories for a
   pool against a short list of major paired assets (WETH, USDC):
     - Uniswap V2 Factory: ``getPair(tokenA, tokenB)``. A single
       fixed-fee pair contract; no revert on "doesn't exist", it
       just returns ``address(0)``.
     - Uniswap V3 Factory: ``getPool(tokenA, tokenB, fee)`` for each
       of the three standard fee tiers (0.05% / 0.3% / 1%). Same
       address(0)-means-missing convention.
3. For every non-zero pool address found:
     - V2: also call ``token0()`` and ``getReserves()`` on the pair
       itself so we can store the reserves oriented correctly
       (which side is our token vs. the paired asset).
     - V3: record the pool address and fee tier only. A single
       reserve number is not meaningful for concentrated liquidity
       (see ``LiquidityPool`` docstring); depth analysis is a later
       phase.
   Upsert into ``liquidity_pools`` (unique on ``pool_address``).
4. Set ``tokens.liquidity_checked_at = now()`` once all probes for a
   token complete -- regardless of whether any pool was found. We do
   NOT keep re-scanning a token that legitimately has no liquidity
   yet forever; if it later gets listed, the ongoing liquidity
   *monitoring* detector (Phase 9, not yet built) is a better place
   to catch that than an unbounded discovery retry loop here.
   If a transport-level RPC error occurs partway through a token's
   probes, we leave ``liquidity_checked_at`` untouched so the next
   tick retries that token from scratch.

We deliberately do NOT use web3.py contract classes here, for the
same reason as the Phase 4 detector: a handful of hard-coded
selectors keeps the dependency surface small and lets the unit
tests stub the provider with raw bytes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.blockchain.provider import BlockchainProvider
from app.database.models import LiquidityPool, Token

logger = logging.getLogger(__name__)

# --- well-known Base mainnet addresses --------------------------------
# Sources (verified against official docs, not memory):
#   Uniswap V2 Factory (Base): docs.uniswap.org/contracts/v2/reference/smart-contracts/v2-deployments
#   Uniswap V3 Factory (Base): github.com/Uniswap/docs .../v3/reference/deployments/Base-Deployments.md
#   WETH9 (Base):              same V3 deployments page, "Wrapped Native Token Addresses"
#   USDC (Base, native):       Circle's official Base deployment (also used in test_integration_erc20.py)
UNISWAP_V2_FACTORY = "0x8909dc15e40173ff4699343b6eb8132c65e18ec6"
UNISWAP_V3_FACTORY = "0x33128a8fc17869897dce68ed026d694621f6fdfd"
WETH_BASE = "0x4200000000000000000000000000000000000006"
USDC_BASE = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"

# Paired assets we search against, in priority order. Kept short and
# high-confidence deliberately -- expanding this list (e.g. cbBTC,
# DAI) is a cheap follow-up once the pipeline is proven out.
PAIR_ASSETS: list[str] = [WETH_BASE, USDC_BASE]

# Uniswap V3 standard fee tiers, in hundredths of a bip.
V3_FEE_TIERS: list[int] = [500, 3000, 10000]

# Function selectors, computed as keccak256(signature)[:4]. Hard-coded
# (rather than derived via web3/eth_utils at import time) for the same
# reason as tokens.py's SELECTOR_* constants: small dependency surface,
# stub-friendly tests.
SELECTOR_GET_PAIR = "0xe6a43905"      # getPair(address,address)
SELECTOR_GET_POOL = "0x1698ee82"      # getPool(address,address,uint24)
SELECTOR_GET_RESERVES = "0x0902f1ac"  # getReserves()
SELECTOR_TOKEN0 = "0x0dfe1681"        # token0()

ZERO_ADDRESS = "0x" + "00" * 20

DEFAULT_BATCH_SIZE = 20


def make_on_block(
    provider: BlockchainProvider,
    session_factory: async_sessionmaker[AsyncSession],
    batch_size: int = DEFAULT_BATCH_SIZE,
):
    """Return an ``on_block(block)`` callback that discovers liquidity pools.

    Mirrors ``app.discovery.tokens.make_on_block``'s shape so it can be
    registered on the same ``BlockListener`` alongside the deployment
    and ERC-20 detectors.
    """

    async def on_block(block: dict) -> None:
        await process_liquidity_discovery(block, provider, session_factory, batch_size)

    return on_block


async def process_liquidity_discovery(
    block: dict,
    provider: BlockchainProvider,
    session_factory: async_sessionmaker[AsyncSession],
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> None:
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(Token)
                .where(Token.liquidity_checked_at.is_(None))
                .order_by(Token.id.asc())
                .limit(batch_size)
            )
        ).scalars().all()
        if not rows:
            return

        found_total = 0
        checked = 0
        now = datetime.now(timezone.utc)
        for token in rows:
            pools, transport_failed = await _discover_pools_for_token(
                provider, token.contract_address, block
            )
            if transport_failed:
                # Leave liquidity_checked_at untouched; retry this
                # token on a later tick.
                continue
            checked += 1
            token.liquidity_checked_at = now
            if pools:
                found_total += len(pools)
                stmt = (
                    pg_insert(LiquidityPool)
                    .values(pools)
                    .on_conflict_do_nothing(index_elements=["pool_address"])
                )
                await session.execute(stmt)

        if checked:
            logger.info(
                "block %s: liquidity sweep %d token(s), %d pool(s) found",
                block["number"], checked, found_total,
            )
        await session.commit()


async def _discover_pools_for_token(
    provider: BlockchainProvider,
    token_address: str,
    block: dict,
) -> tuple[list[dict], bool]:
    """Probe every (factory, paired-asset) combination for one token.

    Returns ``(pools, transport_failed)``. ``pools`` is a list of
    ready-to-insert ``LiquidityPool`` row dicts (possibly empty --
    "checked, found nothing" is a normal, successful outcome).
    ``transport_failed`` is True if any probe hit a network/RPC
    error, in which case the caller should NOT mark the token as
    checked so the whole token is retried next tick. A pool simply
    not existing (address(0) response) is NOT a transport failure.
    """
    pools: list[dict] = []
    block_number = int(block["number"])
    now = datetime.now(timezone.utc)

    for pair_asset in PAIR_ASSETS:
        # --- Uniswap V2 -----------------------------------------------
        try:
            pool_addr = await _call_get_pair(
                provider, UNISWAP_V2_FACTORY, token_address, pair_asset
            )
        except Exception:  # noqa: BLE001
            return pools, True
        if pool_addr is not None:
            try:
                reserve_token, reserve_pair = await _get_v2_reserves(
                    provider, pool_addr, token_address
                )
            except Exception:  # noqa: BLE001
                return pools, True
            pools.append(
                {
                    "token_address": token_address,
                    "pool_address": pool_addr,
                    "dex": "uniswap_v2",
                    "pair_asset": pair_asset,
                    "fee_tier": None,
                    "reserve_token": reserve_token,
                    "reserve_pair": reserve_pair,
                    "discovered_block": block_number,
                    "discovered_at": now,
                }
            )

        # --- Uniswap V3 (one probe per fee tier) -----------------------
        for fee in V3_FEE_TIERS:
            try:
                pool_addr = await _call_get_pool(
                    provider, UNISWAP_V3_FACTORY, token_address, pair_asset, fee
                )
            except Exception:  # noqa: BLE001
                return pools, True
            if pool_addr is not None:
                pools.append(
                    {
                        "token_address": token_address,
                        "pool_address": pool_addr,
                        "dex": "uniswap_v3",
                        "pair_asset": pair_asset,
                        "fee_tier": fee,
                        "reserve_token": None,
                        "reserve_pair": None,
                        "discovered_block": block_number,
                        "discovered_at": now,
                    }
                )

    return pools, False


# --- low-level calls ---------------------------------------------------
async def _call_get_pair(
    provider: BlockchainProvider, factory: str, token_a: str, token_b: str
) -> str | None:
    data = SELECTOR_GET_PAIR + _enc_address(token_a) + _enc_address(token_b)
    raw = await provider.get_eth_call(factory, data)
    return _decode_address_or_none(raw)


async def _call_get_pool(
    provider: BlockchainProvider, factory: str, token_a: str, token_b: str, fee: int
) -> str | None:
    data = (
        SELECTOR_GET_POOL
        + _enc_address(token_a)
        + _enc_address(token_b)
        + _enc_uint(fee)
    )
    raw = await provider.get_eth_call(factory, data)
    return _decode_address_or_none(raw)


async def _get_v2_reserves(
    provider: BlockchainProvider, pool_address: str, token_address: str
) -> tuple[int, int]:
    """Return ``(reserve_token, reserve_pair)`` for a V2 pair.

    Uniswap V2 always orders ``token0 < token1`` by address, so we
    first ask the pair which side is ``token0`` to know how to
    orient ``getReserves()``'s ``(reserve0, reserve1)`` tuple.
    """
    token0_raw = await provider.get_eth_call(pool_address, SELECTOR_TOKEN0)
    token0 = _decode_address_or_none(token0_raw) or ZERO_ADDRESS

    reserves_raw = await provider.get_eth_call(pool_address, SELECTOR_GET_RESERVES)
    if len(reserves_raw) < 64:
        raise ValueError(f"getReserves() response too short: {len(reserves_raw)}")
    reserve0 = int.from_bytes(reserves_raw[0:32], "big")
    reserve1 = int.from_bytes(reserves_raw[32:64], "big")

    if token0.lower() == token_address.lower():
        return reserve0, reserve1
    return reserve1, reserve0


# --- ABI encode/decode helpers (no external dep) -----------------------
def _enc_address(addr: str) -> str:
    """Left-pad a 20-byte address to a 32-byte ABI word, as hex (no 0x)."""
    a = addr.lower().removeprefix("0x")
    return a.rjust(64, "0")


def _enc_uint(n: int, size: int = 32) -> str:
    """Encode an integer as a ``size``-byte ABI word, as hex (no 0x)."""
    return n.to_bytes(size, "big").hex()


def _decode_address_or_none(data: bytes) -> str | None:
    """Decode a 32-byte ABI address return.

    Returns ``None`` for a missing/empty response or the zero
    address -- both mean "no pool for this pair", not an error.
    """
    if not data or len(data) < 32:
        return None
    addr = "0x" + data[12:32].hex()
    if addr == ZERO_ADDRESS:
        return None
    return addr
