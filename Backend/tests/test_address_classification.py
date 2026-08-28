"""Unit tests for spec sec.13 address classification.

Prereqs:
    docker compose up -d

Run:
    pytest tests/test_address_classification.py -v
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.services.address_classification import (
    CATEGORY_BRIDGE,
    CATEGORY_BURN,
    CATEGORY_CONTRACT,
    CATEGORY_DEPLOYER,
    CATEGORY_DEPLOYER_ASSOCIATED,
    CATEGORY_DEX_POOL,
    CATEGORY_DEX_ROUTER,
    CATEGORY_EOA,
    CATEGORY_UNKNOWN,
    KNOWN_BRIDGES,
    KNOWN_DEX_ROUTERS,
    ZERO_ADDRESS,
    classify_address,
)
from app.database.models import (
    Base,
    ContractDeployment,
    LiquidityPool,
    Token,
    WalletRelationship,
)


class StubProvider:
    def __init__(self, code_by_address: dict[str, bytes] | None = None):
        self.code_by_address = {k.lower(): v for k, v in (code_by_address or {}).items()}

    async def get_code(self, address: str) -> bytes:
        return self.code_by_address.get(address.lower(), b"")


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


class TestHardcodedCategories:
    @pytest.mark.asyncio
    async def test_zero_address_is_burn(self, session_factory):
        factory, schema = session_factory
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            result = await classify_address(ZERO_ADDRESS, session)
            assert result["category"] == CATEGORY_BURN

    @pytest.mark.asyncio
    async def test_dead_address_is_burn(self, session_factory):
        factory, schema = session_factory
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            result = await classify_address(
                "0x000000000000000000000000000000000000dEaD", session
            )
            assert result["category"] == CATEGORY_BURN

    @pytest.mark.asyncio
    async def test_known_router_classified(self, session_factory):
        factory, schema = session_factory
        router = next(iter(KNOWN_DEX_ROUTERS))
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            result = await classify_address(router, session)
            assert result["category"] == CATEGORY_DEX_ROUTER

    @pytest.mark.asyncio
    async def test_known_bridge_classified(self, session_factory):
        factory, schema = session_factory
        bridge = next(iter(KNOWN_BRIDGES))
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            result = await classify_address(bridge, session)
            assert result["category"] == CATEGORY_BRIDGE


class TestDatabaseBackedCategories:
    @pytest.mark.asyncio
    async def test_known_pool_classified(self, session_factory):
        factory, schema = session_factory
        pool_addr = "0x" + "cc" * 20
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            session.add(
                ContractDeployment(
                    contract_address="0x" + "aa" * 20,
                    deployer="0x" + "22" * 20,
                    creation_tx="0x" + "33" * 32,
                    creation_block=1,
                    is_erc20=True,
                    erc20_checked_at=datetime.now(timezone.utc),
                )
            )
            session.add(
                Token(
                    contract_address="0x" + "aa" * 20,
                    deployer="0x" + "22" * 20,
                    name="T", symbol="T", decimals=18, total_supply=10**24,
                    creation_block=1, creation_timestamp=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            session.add(
                LiquidityPool(
                    token_address="0x" + "aa" * 20,
                    pool_address=pool_addr,
                    dex="uniswap_v2", pair_asset="0x" + "bb" * 20,
                    discovered_block=1, discovered_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            result = await classify_address(pool_addr, session)
            assert result["category"] == CATEGORY_DEX_POOL

    @pytest.mark.asyncio
    async def test_known_deployer_classified(self, session_factory):
        factory, schema = session_factory
        deployer_addr = "0x" + "22" * 20
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            session.add(
                ContractDeployment(
                    contract_address="0x" + "aa" * 20,
                    deployer=deployer_addr,
                    creation_tx="0x" + "33" * 32,
                    creation_block=1,
                    is_erc20=False,
                )
            )
            await session.commit()
            result = await classify_address(deployer_addr, session)
            assert result["category"] == CATEGORY_DEPLOYER

    @pytest.mark.asyncio
    async def test_deployer_associated_via_edge(self, session_factory):
        factory, schema = session_factory
        associated_addr = "0x" + "ee" * 20
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            session.add(
                WalletRelationship(
                    a="0x" + "22" * 20,
                    b=associated_addr,
                    kind="transfer_recipient",
                    weight=1,
                    first_seen_block=1,
                    last_seen_block=1,
                    evidence_json={},
                    created_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            result = await classify_address(associated_addr, session)
            assert result["category"] == CATEGORY_DEPLOYER_ASSOCIATED

    @pytest.mark.asyncio
    async def test_unrelated_edge_kind_not_deployer_associated(self, session_factory):
        """Sanity: an edge kind NOT in the deployer-association list
        (there currently are none outside the four, but this guards
        against a future edge kind being silently included)."""
        factory, schema = session_factory
        addr = "0x" + "ff" * 20
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            result = await classify_address(addr, session)
            assert result["category"] == CATEGORY_UNKNOWN  # no provider given, no edges


class TestProviderBackedCategories:
    @pytest.mark.asyncio
    async def test_contract_with_code_classified(self, session_factory):
        factory, schema = session_factory
        addr = "0x" + "11" * 20
        provider = StubProvider(code_by_address={addr: b"\x60\x60\x60\x40"})
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            result = await classify_address(addr, session, provider=provider)
            assert result["category"] == CATEGORY_CONTRACT

    @pytest.mark.asyncio
    async def test_eoa_with_no_code_classified(self, session_factory):
        factory, schema = session_factory
        addr = "0x" + "12" * 20
        provider = StubProvider(code_by_address={addr: b""})
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            result = await classify_address(addr, session, provider=provider)
            assert result["category"] == CATEGORY_EOA

    @pytest.mark.asyncio
    async def test_no_provider_and_no_db_match_is_unknown(self, session_factory):
        factory, schema = session_factory
        addr = "0x" + "13" * 20
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            result = await classify_address(addr, session, provider=None)
            assert result["category"] == CATEGORY_UNKNOWN


class TestPriorityOrder:
    @pytest.mark.asyncio
    async def test_burn_takes_priority_over_everything(self, session_factory):
        """Even if somehow the zero address had DB rows, burn wins."""
        factory, schema = session_factory
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            session.add(
                ContractDeployment(
                    contract_address="0x" + "aa" * 20,
                    deployer=ZERO_ADDRESS,
                    creation_tx="0x" + "33" * 32,
                    creation_block=1,
                    is_erc20=False,
                )
            )
            await session.commit()
            result = await classify_address(ZERO_ADDRESS, session)
            assert result["category"] == CATEGORY_BURN
