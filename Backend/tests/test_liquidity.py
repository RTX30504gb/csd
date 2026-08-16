"""Unit tests for the liquidity pool discovery detector.

Mirrors ``test_tokens.py``'s per-test PG schema isolation and
stub-provider approach.

Prereqs:
    docker compose up -d

Run:
    pytest tests/test_liquidity.py -v
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.discovery.liquidity import (
    PAIR_ASSETS,
    UNISWAP_V2_FACTORY,
    UNISWAP_V3_FACTORY,
    V3_FEE_TIERS,
    ZERO_ADDRESS,
    _decode_address_or_none,
    _enc_address,
    _enc_uint,
    make_on_block,
)
from app.database.models import Base, ContractDeployment, LiquidityPool, Token

# Number of eth_call probes made per token when nothing is found:
# len(PAIR_ASSETS) * (1 V2 getPair + len(V3_FEE_TIERS) getPool calls)
CALLS_PER_TOKEN_NO_HITS = len(PAIR_ASSETS) * (1 + len(V3_FEE_TIERS))


# ---------- stub provider ---------------------------------------------------
class StubProvider:
    """Returns canned bytes for each eth_call, keyed by (to, data-prefix).

    Simpler than a strict queue (unlike StubProvider in test_tokens.py)
    because the liquidity detector's call order/count varies with how
    many pools are found. Responses are looked up by exact (to, data)
    match; ``default_response`` (zero-address) covers every combination
    the test doesn't care about.
    """

    def __init__(
        self,
        responses: dict[tuple[str, str], Any] | None = None,
        default_response: Any = None,
    ) -> None:
        self.responses = dict(responses or {})
        self.default_response = (
            default_response if default_response is not None else (bytes(32))
        )
        self.calls: list[tuple[str, str]] = []

    @property
    def chain_id(self) -> int:
        return 8453

    async def get_eth_call(self, to: str, data: str) -> bytes:
        self.calls.append((to.lower(), data))
        key = (to.lower(), data)
        if key in self.responses:
            resp = self.responses[key]
        else:
            resp = self.default_response
        if resp == "transient":
            raise OSError("simulated network failure")
        if isinstance(resp, BaseException):
            raise resp
        return resp

    async def get_block(self, block_number: int, full_transactions: bool = False) -> dict:
        return {
            "number": block_number,
            "hash": f"0x{block_number:064x}",
            "timestamp": 1_700_000_000 + block_number,
            "transactions": [],
        }


def _addr_response(addr: str) -> bytes:
    """32-byte ABI-encoded address, as raw response bytes."""
    return bytes(12) + bytes.fromhex(addr.lower().removeprefix("0x"))


def _reserves_response(reserve0: int, reserve1: int) -> bytes:
    return (
        reserve0.to_bytes(32, "big")
        + reserve1.to_bytes(32, "big")
        + (0).to_bytes(32, "big")  # blockTimestampLast, unused by decoder
    )


def _get_pair_call(token_a: str, token_b: str) -> str:
    from app.discovery.liquidity import SELECTOR_GET_PAIR
    return SELECTOR_GET_PAIR + _enc_address(token_a) + _enc_address(token_b)


def _get_pool_call(token_a: str, token_b: str, fee: int) -> str:
    from app.discovery.liquidity import SELECTOR_GET_POOL
    return (
        SELECTOR_GET_POOL
        + _enc_address(token_a) + _enc_address(token_b) + _enc_uint(fee)
    )


def _token0_call() -> str:
    from app.discovery.liquidity import SELECTOR_TOKEN0
    return SELECTOR_TOKEN0


def _get_reserves_call() -> str:
    from app.discovery.liquidity import SELECTOR_GET_RESERVES
    return SELECTOR_GET_RESERVES


# ---------- schema fixture (per-test isolation) ---------------------------
@pytest_asyncio.fixture
async def session_factory():
    settings = get_settings_safe()
    schema = "t_" + uuid.uuid4().hex[:12]
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        await conn.execute(text(f'SET search_path TO "{schema}"'))
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory, schema
    async with engine.begin() as conn:
        await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    await engine.dispose()


def get_settings_safe():
    from app.config import get_settings
    return get_settings()


async def _seed_token(
    factory, schema, address: str = "0x" + "11" * 20,
) -> Token:
    async with factory() as session:
        await session.execute(text(f'SET search_path TO "{schema}"'))
        dep = ContractDeployment(
            contract_address=address,
            deployer="0x" + "22" * 20,
            creation_tx="0x" + address[2:].rjust(64, "0"),
            creation_block=100,
            is_erc20=True,
            erc20_checked_at=datetime.now(timezone.utc),
        )
        token = Token(
            contract_address=address,
            deployer="0x" + "22" * 20,
            name="Test",
            symbol="TST",
            decimals=18,
            total_supply=10**24,
            creation_block=100,
            creation_timestamp=datetime.now(timezone.utc),
        )
        session.add_all([dep, token])
        await session.commit()
        await session.refresh(token)
        return token


class TestDecodeAddress:
    def test_zero_address_is_none(self):
        assert _decode_address_or_none(bytes(32)) is None

    def test_short_response_is_none(self):
        assert _decode_address_or_none(b"") is None
        assert _decode_address_or_none(bytes(10)) is None

    def test_valid_address_decodes(self):
        addr = "0x" + "ab" * 20
        raw = bytes(12) + bytes.fromhex("ab" * 20)
        assert _decode_address_or_none(raw) == addr


class TestNoPoolsFound:
    @pytest.mark.asyncio
    async def test_all_zero_marks_checked_no_pools(self, session_factory):
        factory, schema = session_factory
        token = await _seed_token(factory, schema)
        # default_response = 32 zero bytes -> every getPair/getPool
        # call resolves to "no pool".
        provider = StubProvider()
        on_block = make_on_block(provider, factory)
        await on_block({"number": 500, "timestamp": 1_700_000_000})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            pools = (await session.execute(select(LiquidityPool))).scalars().all()
            assert pools == []
            t = (await session.execute(select(Token))).scalar_one()
            assert t.liquidity_checked_at is not None
        # Exactly the expected number of factory probes were made.
        assert len(provider.calls) == CALLS_PER_TOKEN_NO_HITS


class TestV2PoolFound:
    @pytest.mark.asyncio
    async def test_v2_pool_recorded_with_oriented_reserves(self, session_factory):
        factory, schema = session_factory
        token_addr = "0x" + "aa" * 20
        token = await _seed_token(factory, schema, address=token_addr)
        weth = PAIR_ASSETS[0]
        pool_addr = "0x" + "cc" * 20

        responses = {
            (UNISWAP_V2_FACTORY, _get_pair_call(token_addr, weth)):
                _addr_response(pool_addr),
            (pool_addr, _token0_call()): _addr_response(weth),  # token0 = WETH
            (pool_addr, _get_reserves_call()): _reserves_response(500, 1000),
        }
        provider = StubProvider(responses=responses)
        on_block = make_on_block(provider, factory)
        await on_block({"number": 600, "timestamp": 1_700_000_000})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            pools = (await session.execute(select(LiquidityPool))).scalars().all()
            v2_pools = [p for p in pools if p.dex == "uniswap_v2"]
            assert len(v2_pools) == 1
            p = v2_pools[0]
            assert p.pool_address == pool_addr
            assert p.pair_asset == weth
            assert p.fee_tier is None
            # token0 is WETH (reserve0=500), so our token (token1) gets
            # reserve1=1000 -- reserve_token must reflect that, not
            # naive reserve0.
            assert int(p.reserve_token) == 1000
            assert int(p.reserve_pair) == 500

    @pytest.mark.asyncio
    async def test_v2_pool_token_is_token0(self, session_factory):
        """When our token IS token0, reserves must NOT be swapped."""
        factory, schema = session_factory
        token_addr = "0x" + "aa" * 20
        await _seed_token(factory, schema, address=token_addr)
        weth = PAIR_ASSETS[0]
        pool_addr = "0x" + "cc" * 20

        responses = {
            (UNISWAP_V2_FACTORY, _get_pair_call(token_addr, weth)):
                _addr_response(pool_addr),
            (pool_addr, _token0_call()): _addr_response(token_addr),  # token0 = our token
            (pool_addr, _get_reserves_call()): _reserves_response(777, 333),
        }
        provider = StubProvider(responses=responses)
        on_block = make_on_block(provider, factory)
        await on_block({"number": 601, "timestamp": 1_700_000_000})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            p = (
                await session.execute(
                    select(LiquidityPool).where(LiquidityPool.dex == "uniswap_v2")
                )
            ).scalar_one()
            assert int(p.reserve_token) == 777
            assert int(p.reserve_pair) == 333


class TestV3PoolFound:
    @pytest.mark.asyncio
    async def test_v3_pool_recorded_without_reserves(self, session_factory):
        factory, schema = session_factory
        token_addr = "0x" + "aa" * 20
        await _seed_token(factory, schema, address=token_addr)
        usdc = PAIR_ASSETS[1]
        pool_addr = "0x" + "dd" * 20
        fee = 3000

        responses = {
            (UNISWAP_V3_FACTORY, _get_pool_call(token_addr, usdc, fee)):
                _addr_response(pool_addr),
        }
        provider = StubProvider(responses=responses)
        on_block = make_on_block(provider, factory)
        await on_block({"number": 700, "timestamp": 1_700_000_000})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            v3_pools = (
                await session.execute(
                    select(LiquidityPool).where(LiquidityPool.dex == "uniswap_v3")
                )
            ).scalars().all()
            assert len(v3_pools) == 1
            p = v3_pools[0]
            assert p.pool_address == pool_addr
            assert p.fee_tier == fee
            assert p.pair_asset == usdc
            assert p.reserve_token is None
            assert p.reserve_pair is None


class TestTransportErrors:
    @pytest.mark.asyncio
    async def test_transient_error_leaves_token_unchecked(self, session_factory):
        factory, schema = session_factory
        token_addr = "0x" + "aa" * 20
        await _seed_token(factory, schema, address=token_addr)
        provider = StubProvider(default_response="transient")
        on_block = make_on_block(provider, factory)
        await on_block({"number": 800, "timestamp": 1_700_000_000})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            assert (await session.execute(select(LiquidityPool))).scalars().all() == []
            t = (await session.execute(select(Token))).scalar_one()
            assert t.liquidity_checked_at is None  # will retry


class TestBatchingAndIdempotency:
    @pytest.mark.asyncio
    async def test_no_unchecked_tokens_is_noop(self, session_factory):
        factory, schema = session_factory
        provider = StubProvider()
        on_block = make_on_block(provider, factory)
        await on_block({"number": 1, "timestamp": 1_700_000_000})
        assert provider.calls == []

    @pytest.mark.asyncio
    async def test_batch_size_limits_tokens_per_tick(self, session_factory):
        factory, schema = session_factory
        for i in range(5):
            await _seed_token(factory, schema, address=f"0x{i:040x}")
        provider = StubProvider()  # all zero -> no pools, just marks checked
        on_block = make_on_block(provider, factory, batch_size=2)
        await on_block({"number": 900, "timestamp": 1_700_000_000})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            checked = (
                await session.execute(
                    select(Token).where(Token.liquidity_checked_at.is_not(None))
                )
            ).scalars().all()
            assert len(checked) == 2
            pending = (
                await session.execute(
                    select(Token).where(Token.liquidity_checked_at.is_(None))
                )
            ).scalars().all()
            assert len(pending) == 3

    @pytest.mark.asyncio
    async def test_idempotent_on_replay(self, session_factory):
        """A token already marked checked is not re-probed or
        re-inserted into liquidity_pools on a later tick."""
        factory, schema = session_factory
        token_addr = "0x" + "aa" * 20
        token = await _seed_token(factory, schema, address=token_addr)
        weth = PAIR_ASSETS[0]
        pool_addr = "0x" + "cc" * 20
        responses = {
            (UNISWAP_V2_FACTORY, _get_pair_call(token_addr, weth)):
                _addr_response(pool_addr),
            (pool_addr, _token0_call()): _addr_response(weth),
            (pool_addr, _get_reserves_call()): _reserves_response(1, 1),
        }
        provider = StubProvider(responses=responses)
        on_block = make_on_block(provider, factory)
        await on_block({"number": 1000, "timestamp": 1_700_000_000})
        calls_after_first = len(provider.calls)
        await on_block({"number": 1001, "timestamp": 1_700_000_001})

        # Second tick made zero new calls -- token was already checked.
        assert len(provider.calls) == calls_after_first

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            pools = (await session.execute(select(LiquidityPool))).scalars().all()
            assert len(pools) == 1  # no duplicate insert
