"""Unit tests for spec sec.14 deployer analysis.

Prereqs:
    docker compose up -d

Run:
    pytest tests/test_deployer_analysis.py -v
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

from app.services.deployer_analysis import analyze_deployer
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

DEPLOYER = "0x" + "22" * 20


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


async def _add_deployment_and_token(session, token_addr: str, block: int = 1) -> None:
    session.add(
        ContractDeployment(
            contract_address=token_addr,
            deployer=DEPLOYER,
            creation_tx="0x" + token_addr[2:].rjust(64, "0"),
            creation_block=block,
            is_erc20=True,
            erc20_checked_at=datetime.now(timezone.utc),
        )
    )
    session.add(
        Token(
            contract_address=token_addr,
            deployer=DEPLOYER,
            name="T", symbol="T", decimals=18, total_supply=10**24,
            creation_block=block, creation_timestamp=datetime.now(timezone.utc),
        )
    )
    await session.commit()


class TestBasicCounts:
    @pytest.mark.asyncio
    async def test_no_history_returns_zeros(self, session_factory):
        factory, schema = session_factory
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            result = await analyze_deployer(DEPLOYER, session)
            assert result["number_of_previous_contracts"] == 0
            assert result["number_of_previous_token_launches"] == 0
            assert result["previous_suspicious_tokens"] == 0
            assert result["previous_liquidity_withdrawals"] == 0
            assert result["previous_token_collapses"] == 0
            assert result["funding_sources"] is None

    @pytest.mark.asyncio
    async def test_counts_multiple_tokens(self, session_factory):
        factory, schema = session_factory
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            for i in range(4):
                await _add_deployment_and_token(session, f"0x{i:040x}")
            result = await analyze_deployer(DEPLOYER, session)
            assert result["number_of_previous_contracts"] == 4
            assert result["number_of_previous_token_launches"] == 4


class TestSuspiciousTokenHeuristic:
    @pytest.mark.asyncio
    async def test_dangerous_and_not_renounced_counts_as_suspicious(self, session_factory):
        factory, schema = session_factory
        token_addr = "0x" + "aa" * 20
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            await _add_deployment_and_token(session, token_addr)
            session.add(
                ContractRiskFlags(
                    token_address=token_addr,
                    has_mint=True, has_blacklist=False, has_pause=False,
                    has_tax_control=False, has_max_tx_control=False,
                    has_max_wallet_control=False, has_fee_exclusion_control=False,
                    has_trading_control=False, is_upgradeable_proxy=False,
                    has_owner_function=True, owner_address="0x" + "33" * 20,
                    owner_renounced=False, selectors_found="", bytecode_size=1,
                    analyzed_block=1, analyzed_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            result = await analyze_deployer(DEPLOYER, session)
            assert result["previous_suspicious_tokens"] == 1

    @pytest.mark.asyncio
    async def test_renounced_ownership_not_suspicious(self, session_factory):
        """Same dangerous flags, but ownership IS renounced -- should
        not count, since a renounced mint function can't be called by
        anyone anymore."""
        factory, schema = session_factory
        token_addr = "0x" + "aa" * 20
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            await _add_deployment_and_token(session, token_addr)
            session.add(
                ContractRiskFlags(
                    token_address=token_addr,
                    has_mint=True, has_blacklist=False, has_pause=False,
                    has_tax_control=False, has_max_tx_control=False,
                    has_max_wallet_control=False, has_fee_exclusion_control=False,
                    has_trading_control=False, is_upgradeable_proxy=False,
                    has_owner_function=True, owner_address=None,
                    owner_renounced=True, selectors_found="", bytecode_size=1,
                    analyzed_block=1, analyzed_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            result = await analyze_deployer(DEPLOYER, session)
            assert result["previous_suspicious_tokens"] == 0

    @pytest.mark.asyncio
    async def test_unknown_ownership_not_over_counted(self, session_factory):
        """owner_renounced=None (unknown, no owner() function at all)
        must NOT count as suspicious -- we don't know, so we don't
        accuse."""
        factory, schema = session_factory
        token_addr = "0x" + "aa" * 20
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            await _add_deployment_and_token(session, token_addr)
            session.add(
                ContractRiskFlags(
                    token_address=token_addr,
                    has_mint=True, has_blacklist=False, has_pause=False,
                    has_tax_control=False, has_max_tx_control=False,
                    has_max_wallet_control=False, has_fee_exclusion_control=False,
                    has_trading_control=False, is_upgradeable_proxy=False,
                    has_owner_function=False, owner_address=None,
                    owner_renounced=None, selectors_found="", bytecode_size=1,
                    analyzed_block=1, analyzed_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            result = await analyze_deployer(DEPLOYER, session)
            assert result["previous_suspicious_tokens"] == 0

    @pytest.mark.asyncio
    async def test_clean_token_not_suspicious(self, session_factory):
        factory, schema = session_factory
        token_addr = "0x" + "aa" * 20
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            await _add_deployment_and_token(session, token_addr)
            session.add(
                ContractRiskFlags(
                    token_address=token_addr,
                    has_mint=False, has_blacklist=False, has_pause=False,
                    has_tax_control=False, has_max_tx_control=False,
                    has_max_wallet_control=False, has_fee_exclusion_control=False,
                    has_trading_control=False, is_upgradeable_proxy=False,
                    has_owner_function=True, owner_address="0x" + "33" * 20,
                    owner_renounced=False, selectors_found="", bytecode_size=1,
                    analyzed_block=1, analyzed_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            result = await analyze_deployer(DEPLOYER, session)
            assert result["previous_suspicious_tokens"] == 0


class TestLiquidityHistory:
    @pytest.mark.asyncio
    async def test_withdrawal_and_collapse_counted(self, session_factory):
        factory, schema = session_factory
        token_addr = "0x" + "aa" * 20
        pool_addr = "0x" + "cc" * 20
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            await _add_deployment_and_token(session, token_addr)
            session.add(
                LiquidityPool(
                    token_address=token_addr, pool_address=pool_addr,
                    dex="uniswap_v2", pair_asset="0x" + "bb" * 20,
                    discovered_block=1, discovered_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            # A severe drop (95%) -- counts as both a withdrawal AND a collapse.
            session.add(
                LiquidityEvent(
                    pool_address=pool_addr, event_type="withdrawal",
                    metric="reserve_token", value_before=1000, value_after=50,
                    percent_change=-0.95, block_number=100,
                    detected_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            result = await analyze_deployer(DEPLOYER, session)
            assert result["previous_liquidity_withdrawals"] == 1
            assert result["previous_token_collapses"] == 1

    @pytest.mark.asyncio
    async def test_moderate_withdrawal_not_a_collapse(self, session_factory):
        factory, schema = session_factory
        token_addr = "0x" + "aa" * 20
        pool_addr = "0x" + "cc" * 20
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            await _add_deployment_and_token(session, token_addr)
            session.add(
                LiquidityPool(
                    token_address=token_addr, pool_address=pool_addr,
                    dex="uniswap_v2", pair_asset="0x" + "bb" * 20,
                    discovered_block=1, discovered_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            # 60% drop -- a withdrawal, but under the 90% collapse threshold.
            session.add(
                LiquidityEvent(
                    pool_address=pool_addr, event_type="withdrawal",
                    metric="reserve_token", value_before=1000, value_after=400,
                    percent_change=-0.6, block_number=100,
                    detected_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            result = await analyze_deployer(DEPLOYER, session)
            assert result["previous_liquidity_withdrawals"] == 1
            assert result["previous_token_collapses"] == 0

    @pytest.mark.asyncio
    async def test_addition_events_not_counted_as_withdrawals(self, session_factory):
        factory, schema = session_factory
        token_addr = "0x" + "aa" * 20
        pool_addr = "0x" + "cc" * 20
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            await _add_deployment_and_token(session, token_addr)
            session.add(
                LiquidityPool(
                    token_address=token_addr, pool_address=pool_addr,
                    dex="uniswap_v2", pair_asset="0x" + "bb" * 20,
                    discovered_block=1, discovered_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            session.add(
                LiquidityEvent(
                    pool_address=pool_addr, event_type="addition",
                    metric="reserve_token", value_before=1000, value_after=3000,
                    percent_change=2.0, block_number=100,
                    detected_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            result = await analyze_deployer(DEPLOYER, session)
            assert result["previous_liquidity_withdrawals"] == 0
            assert result["previous_token_collapses"] == 0


class TestWalletAgeAndRelationships:
    @pytest.mark.asyncio
    async def test_wallet_age_computed_from_first_seen_block(self, session_factory):
        factory, schema = session_factory
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            session.add(
                Wallet(
                    address=DEPLOYER, tokens_deployed=1, tokens_as_pool=0,
                    tokens_as_transfer=0, first_seen_block=1000, last_seen_block=1000,
                    first_seen_at=datetime.now(timezone.utc),
                    last_seen_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            result = await analyze_deployer(DEPLOYER, session, current_block=1500)
            assert result["wallet_age_blocks"] == 500

    @pytest.mark.asyncio
    async def test_no_wallet_row_gives_none_age(self, session_factory):
        factory, schema = session_factory
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            result = await analyze_deployer(DEPLOYER, session, current_block=1500)
            assert result["wallet_age_blocks"] is None

    @pytest.mark.asyncio
    async def test_no_current_block_gives_none_age_even_with_wallet(self, session_factory):
        factory, schema = session_factory
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            session.add(
                Wallet(
                    address=DEPLOYER, tokens_deployed=1, tokens_as_pool=0,
                    tokens_as_transfer=0, first_seen_block=1000, last_seen_block=1000,
                    first_seen_at=datetime.now(timezone.utc),
                    last_seen_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            result = await analyze_deployer(DEPLOYER, session, current_block=None)
            assert result["wallet_age_blocks"] is None

    @pytest.mark.asyncio
    async def test_relationship_count(self, session_factory):
        factory, schema = session_factory
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            session.add(
                WalletRelationship(
                    a=DEPLOYER, b="0x" + "99" * 20, kind="funds_token", weight=1,
                    first_seen_block=1, last_seen_block=1, evidence_json={},
                    created_at=datetime.now(timezone.utc),
                )
            )
            session.add(
                WalletRelationship(
                    a="0x" + "88" * 20, b=DEPLOYER, kind="co_deployed", weight=1,
                    first_seen_block=1, last_seen_block=1, evidence_json={},
                    created_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            result = await analyze_deployer(DEPLOYER, session)
            assert result["relationship_count"] == 2
