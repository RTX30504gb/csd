"""Unit tests for the Phase 6 liquidity monitoring detector.

Mirrors ``test_liquidity.py``'s per-test PG schema isolation and
stub-provider approach.

Prereqs:
    docker compose up -d

Run:
    pytest tests/test_monitor.py -v
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.discovery.monitor import SELECTOR_LIQUIDITY, make_on_block
from app.database.models import Base, LiquidityEvent, LiquidityPool

SELECTOR_GET_RESERVES = "0x0902f1ac"


class StubProvider:
    """Same shape as test_liquidity.py's StubProvider: exact (to, data)
    lookup with a default fallback, so each test only wires up the
    calls it actually cares about."""

    def __init__(
        self,
        responses: dict[tuple[str, str], Any] | None = None,
        default_response: Any = None,
    ) -> None:
        self.responses = dict(responses or {})
        self.default_response = (
            default_response if default_response is not None else _reserves_response(0, 0)
        )
        self.calls: list[tuple[str, str]] = []

    @property
    def chain_id(self) -> int:
        return 8453

    async def get_eth_call(self, to: str, data: str) -> bytes:
        self.calls.append((to.lower(), data))
        key = (to.lower(), data)
        resp = self.responses.get(key, self.default_response)
        if resp == "transient":
            raise OSError("simulated network failure")
        if isinstance(resp, BaseException):
            raise resp
        return resp


def _reserves_response(reserve0: int, reserve1: int) -> bytes:
    return (
        reserve0.to_bytes(32, "big")
        + reserve1.to_bytes(32, "big")
        + (0).to_bytes(32, "big")
    )


def _uint_response(n: int) -> bytes:
    return n.to_bytes(32, "big")


@pytest_asyncio.fixture
async def session_factory():
    from app.config import get_settings

    settings = get_settings()
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


async def _seed_pool(
    factory,
    schema,
    *,
    pool_address: str = "0x" + "cc" * 20,
    dex: str = "uniswap_v2",
    is_token0: bool | None = True,
    reserve_token: int | None = 1000,
    reserve_pair: int | None = 500,
    last_synced_at: datetime | None = None,
) -> LiquidityPool:
    from app.database.models import ContractDeployment, Token

    token_address = "0x" + "aa" * 20
    async with factory() as session:
        await session.execute(text(f'SET search_path TO "{schema}"'))
        # LiquidityPool.token_address is a FK into tokens; the token
        # must exist first (mirrors the real pipeline, where Phase 5
        # discovery only ever runs against already-confirmed tokens).
        existing = (
            await session.execute(
                select(Token).where(Token.contract_address == token_address)
            )
        ).scalars().first()
        if existing is None:
            session.add(
                ContractDeployment(
                    contract_address=token_address,
                    deployer="0x" + "22" * 20,
                    creation_tx="0x" + "33" * 32,
                    creation_block=1,
                    is_erc20=True,
                    erc20_checked_at=datetime.now(timezone.utc),
                )
            )
            session.add(
                Token(
                    contract_address=token_address,
                    deployer="0x" + "22" * 20,
                    name="Test",
                    symbol="TST",
                    decimals=18,
                    total_supply=10**24,
                    creation_block=1,
                    creation_timestamp=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        pool = LiquidityPool(
            token_address=token_address,
            pool_address=pool_address,
            dex=dex,
            pair_asset="0x" + "bb" * 20,
            fee_tier=None if dex == "uniswap_v2" else 3000,
            is_token0=is_token0,
            reserve_token=reserve_token,
            reserve_pair=reserve_pair,
            discovered_block=1,
            discovered_at=datetime.now(timezone.utc),
            last_synced_at=last_synced_at,
        )
        session.add(pool)
        await session.commit()
        await session.refresh(pool)
        return pool


class TestNoStalePools:
    @pytest.mark.asyncio
    async def test_recently_synced_pool_is_skipped(self, session_factory):
        factory, schema = session_factory
        await _seed_pool(factory, schema, last_synced_at=datetime.now(timezone.utc))
        provider = StubProvider()
        on_block = make_on_block(provider, factory)
        await on_block({"number": 100})
        assert provider.calls == []


class TestV2Withdrawal:
    @pytest.mark.asyncio
    async def test_large_drop_creates_withdrawal_event(self, session_factory):
        factory, schema = session_factory
        pool = await _seed_pool(
            factory, schema, is_token0=True, reserve_token=1000, reserve_pair=500,
            last_synced_at=None,
        )
        # token0 side (our token) drops from 1000 to 200 -> -80%
        responses = {
            (pool.pool_address, SELECTOR_GET_RESERVES): _reserves_response(200, 100),
        }
        provider = StubProvider(responses=responses)
        on_block = make_on_block(provider, factory, withdrawal_threshold=0.5)
        await on_block({"number": 200})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            updated = (await session.execute(select(LiquidityPool))).scalar_one()
            assert int(updated.reserve_token) == 200
            assert int(updated.reserve_pair) == 100
            assert updated.last_synced_at is not None

            events = (await session.execute(select(LiquidityEvent))).scalars().all()
            assert len(events) == 1
            ev = events[0]
            assert ev.event_type == "withdrawal"
            assert ev.metric == "reserve_token"
            assert int(ev.value_before) == 1000
            assert int(ev.value_after) == 200
            assert ev.percent_change == pytest.approx(-0.8)
            assert ev.block_number == 200

    @pytest.mark.asyncio
    async def test_token1_orientation_respected(self, session_factory):
        """is_token0=False means reserve1 is OUR token's side."""
        factory, schema = session_factory
        pool = await _seed_pool(
            factory, schema, is_token0=False, reserve_token=1000, reserve_pair=500,
        )
        # reserve0=100 (pair asset), reserve1=200 (our token) -> our
        # token dropped from 1000 to 200.
        responses = {
            (pool.pool_address, SELECTOR_GET_RESERVES): _reserves_response(100, 200),
        }
        provider = StubProvider(responses=responses)
        on_block = make_on_block(provider, factory, withdrawal_threshold=0.5)
        await on_block({"number": 201})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            updated = (await session.execute(select(LiquidityPool))).scalar_one()
            assert int(updated.reserve_token) == 200
            assert int(updated.reserve_pair) == 100
            events = (await session.execute(select(LiquidityEvent))).scalars().all()
            assert len(events) == 1


class TestV2Addition:
    @pytest.mark.asyncio
    async def test_large_increase_creates_addition_event(self, session_factory):
        factory, schema = session_factory
        pool = await _seed_pool(
            factory, schema, is_token0=True, reserve_token=1000, reserve_pair=500,
        )
        # more than doubled: 1000 -> 3000
        responses = {
            (pool.pool_address, SELECTOR_GET_RESERVES): _reserves_response(3000, 1500),
        }
        provider = StubProvider(responses=responses)
        on_block = make_on_block(provider, factory, addition_threshold=1.0)
        await on_block({"number": 300})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            events = (await session.execute(select(LiquidityEvent))).scalars().all()
            assert len(events) == 1
            assert events[0].event_type == "addition"


class TestNoEventOnSmallChange:
    @pytest.mark.asyncio
    async def test_small_change_updates_reserves_no_event(self, session_factory):
        factory, schema = session_factory
        pool = await _seed_pool(
            factory, schema, is_token0=True, reserve_token=1000, reserve_pair=500,
        )
        # -10%, under the 50% default threshold
        responses = {
            (pool.pool_address, SELECTOR_GET_RESERVES): _reserves_response(900, 450),
        }
        provider = StubProvider(responses=responses)
        on_block = make_on_block(provider, factory)
        await on_block({"number": 400})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            events = (await session.execute(select(LiquidityEvent))).scalars().all()
            assert events == []
            updated = (await session.execute(select(LiquidityPool))).scalar_one()
            assert int(updated.reserve_token) == 900


class TestFirstSyncNoEvent:
    @pytest.mark.asyncio
    async def test_null_previous_value_never_fires_event(self, session_factory):
        """A pool with no prior reserve_token (shouldn't normally happen
        for V2 given Phase 5 sets it at discovery, but must not crash
        or false-positive if it does)."""
        factory, schema = session_factory
        pool = await _seed_pool(
            factory, schema, is_token0=True, reserve_token=None, reserve_pair=None,
        )
        responses = {
            (pool.pool_address, SELECTOR_GET_RESERVES): _reserves_response(50, 25),
        }
        provider = StubProvider(responses=responses)
        on_block = make_on_block(provider, factory)
        await on_block({"number": 500})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            events = (await session.execute(select(LiquidityEvent))).scalars().all()
            assert events == []
            updated = (await session.execute(select(LiquidityPool))).scalar_one()
            assert int(updated.reserve_token) == 50
            assert updated.last_synced_at is not None


class TestV3Monitoring:
    @pytest.mark.asyncio
    async def test_v3_liquidity_drop_creates_event(self, session_factory):
        factory, schema = session_factory
        pool = await _seed_pool(
            factory, schema, dex="uniswap_v3", is_token0=None,
            reserve_token=10_000, reserve_pair=None,
        )
        responses = {
            (pool.pool_address, SELECTOR_LIQUIDITY): _uint_response(1_000),
        }
        provider = StubProvider(responses=responses)
        on_block = make_on_block(provider, factory, withdrawal_threshold=0.5)
        await on_block({"number": 600})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            events = (await session.execute(select(LiquidityEvent))).scalars().all()
            assert len(events) == 1
            assert events[0].metric == "v3_liquidity"
            assert events[0].event_type == "withdrawal"
            updated = (await session.execute(select(LiquidityPool))).scalar_one()
            assert int(updated.reserve_token) == 1_000
            # V3 has no reserve_pair concept; must stay untouched (NULL).
            assert updated.reserve_pair is None


class TestTransportErrors:
    @pytest.mark.asyncio
    async def test_transient_error_leaves_pool_unsynced(self, session_factory):
        factory, schema = session_factory
        pool = await _seed_pool(factory, schema)
        provider = StubProvider(default_response="transient")
        on_block = make_on_block(provider, factory)
        await on_block({"number": 700})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            updated = (await session.execute(select(LiquidityPool))).scalar_one()
            assert updated.last_synced_at is None  # will retry
            assert (await session.execute(select(LiquidityEvent))).scalars().all() == []


class TestBatchingAndStaleness:
    @pytest.mark.asyncio
    async def test_stale_cutoff_respected(self, session_factory):
        factory, schema = session_factory
        # Synced 30s ago; default min_resync_interval is 60s -> skip.
        recent = datetime.now(timezone.utc) - timedelta(seconds=30)
        await _seed_pool(factory, schema, last_synced_at=recent)
        provider = StubProvider()
        on_block = make_on_block(provider, factory)  # default 60s interval
        await on_block({"number": 800})
        assert provider.calls == []

    @pytest.mark.asyncio
    async def test_old_enough_pool_is_resynced(self, session_factory):
        factory, schema = session_factory
        old = datetime.now(timezone.utc) - timedelta(seconds=120)
        pool = await _seed_pool(factory, schema, last_synced_at=old)
        responses = {
            (pool.pool_address, SELECTOR_GET_RESERVES): _reserves_response(999, 111),
        }
        provider = StubProvider(responses=responses)
        on_block = make_on_block(provider, factory, min_resync_interval_seconds=60)
        await on_block({"number": 900})
        assert len(provider.calls) == 1

    @pytest.mark.asyncio
    async def test_batch_size_limits_pools_per_tick(self, session_factory):
        factory, schema = session_factory
        for i in range(5):
            await _seed_pool(factory, schema, pool_address=f"0x{i:040x}")
        provider = StubProvider()  # default zero reserves for all
        on_block = make_on_block(provider, factory, batch_size=2)
        await on_block({"number": 1000})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            synced = (
                await session.execute(
                    select(LiquidityPool).where(LiquidityPool.last_synced_at.is_not(None))
                )
            ).scalars().all()
            assert len(synced) == 2

    @pytest.mark.asyncio
    async def test_no_stale_pools_is_noop(self, session_factory):
        factory, schema = session_factory
        provider = StubProvider()
        on_block = make_on_block(provider, factory)
        await on_block({"number": 1})
        assert provider.calls == []
