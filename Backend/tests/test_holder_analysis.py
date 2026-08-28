"""Unit tests for spec sec.12 holder analysis.

Prereqs:
    docker compose up -d

Run:
    pytest tests/test_holder_analysis.py -v
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.discovery.holder_analysis import (
    TRANSFER_TOPIC,
    ZERO_ADDRESS,
    _addr_from_topic,
    _decode_uint256,
    make_on_block,
)
from app.database.models import Base, ContractDeployment, HolderConcentration, Token, TokenHolder

DEPLOYER = "0x" + "22" * 20
TOKEN_ADDR = "0x" + "aa" * 20


def _topic_addr(addr: str) -> str:
    """Encode an address into a 32-byte indexed-topic hex string."""
    return "0x" + "00" * 12 + addr.removeprefix("0x")


def _transfer_log(from_addr: str, to_addr: str, value: int) -> dict:
    return {
        "address": TOKEN_ADDR,
        "topics": [TRANSFER_TOPIC, _topic_addr(from_addr), _topic_addr(to_addr)],
        "data": "0x" + value.to_bytes(32, "big").hex(),
        "blockNumber": 1,
        "transactionHash": "0x" + "01" * 32,
        "logIndex": "0x0",
    }


class StubProvider:
    def __init__(self, logs: list[dict] | None = None, raise_error: bool = False):
        self.logs = logs or []
        self.raise_error = raise_error
        self.calls: list[tuple[int, int]] = []

    async def get_logs(self, *, address, topics, from_block, to_block):
        self.calls.append((from_block, to_block))
        if self.raise_error:
            raise OSError("simulated transport failure")
        return [
            log for log in self.logs
            if from_block <= log["blockNumber"] <= to_block
        ]


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


async def _seed_token(factory, schema, total_supply: int = 1000) -> Token:
    async with factory() as session:
        await session.execute(text(f'SET search_path TO "{schema}"'))
        session.add(
            ContractDeployment(
                contract_address=TOKEN_ADDR, deployer=DEPLOYER,
                creation_tx="0x" + "33" * 32, creation_block=1,
                is_erc20=True, erc20_checked_at=datetime.now(timezone.utc),
            )
        )
        token = Token(
            contract_address=TOKEN_ADDR, deployer=DEPLOYER,
            name="T", symbol="T", decimals=18, total_supply=total_supply,
            creation_block=1, creation_timestamp=datetime.now(timezone.utc),
        )
        session.add(token)
        await session.commit()
        await session.refresh(token)
        return token


class TestTopicAndDataDecoding:
    def test_addr_from_topic(self):
        addr = "0x" + "ab" * 20
        topic = _topic_addr(addr)
        assert _addr_from_topic(topic) == addr

    def test_invalid_topic_returns_none(self):
        assert _addr_from_topic("") is None
        assert _addr_from_topic("0x1234") is None

    def test_decode_uint256_hex_string(self):
        assert _decode_uint256("0x" + (500).to_bytes(32, "big").hex()) == 500

    def test_decode_uint256_none_input(self):
        assert _decode_uint256(None) is None


class TestBalanceReconstruction:
    @pytest.mark.asyncio
    async def test_simple_mint_creates_holder(self, session_factory):
        factory, schema = session_factory
        await _seed_token(factory, schema, total_supply=1000)
        logs = [_transfer_log(ZERO_ADDRESS, "0x" + "01" * 20, 1000)]
        provider = StubProvider(logs=logs)
        on_block = make_on_block(provider, factory)
        await on_block({"number": 10})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            holders = (await session.execute(select(TokenHolder))).scalars().all()
            assert len(holders) == 1
            assert holders[0].holder_address == "0x" + "01" * 20
            assert int(holders[0].balance) == 1000

            conc = (await session.execute(select(HolderConcentration))).scalar_one()
            assert conc.largest_holder_pct == pytest.approx(100.0)
            assert conc.holder_count == 1

    @pytest.mark.asyncio
    async def test_transfer_moves_balance_correctly(self, session_factory):
        factory, schema = session_factory
        await _seed_token(factory, schema, total_supply=1000)
        holder_a = "0x" + "01" * 20
        holder_b = "0x" + "02" * 20
        logs = [
            _transfer_log(ZERO_ADDRESS, holder_a, 1000),
            _transfer_log(holder_a, holder_b, 400),
        ]
        provider = StubProvider(logs=logs)
        on_block = make_on_block(provider, factory)
        await on_block({"number": 10})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            holders = {
                h.holder_address: int(h.balance)
                for h in (await session.execute(select(TokenHolder))).scalars().all()
            }
            assert holders[holder_a] == 600
            assert holders[holder_b] == 400

    @pytest.mark.asyncio
    async def test_full_exit_removes_holder(self, session_factory):
        """An address that received tokens and later sent all of them
        back out should NOT appear as a current holder."""
        factory, schema = session_factory
        await _seed_token(factory, schema, total_supply=1000)
        holder_a = "0x" + "01" * 20
        holder_b = "0x" + "02" * 20
        logs = [
            _transfer_log(ZERO_ADDRESS, holder_a, 1000),
            _transfer_log(holder_a, holder_b, 1000),  # A sends everything away
        ]
        provider = StubProvider(logs=logs)
        on_block = make_on_block(provider, factory)
        await on_block({"number": 10})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            holders = {
                h.holder_address: int(h.balance)
                for h in (await session.execute(select(TokenHolder))).scalars().all()
            }
            assert holder_a not in holders
            assert holders[holder_b] == 1000

    @pytest.mark.asyncio
    async def test_burn_reduces_balance_without_creating_zero_holder(self, session_factory):
        factory, schema = session_factory
        await _seed_token(factory, schema, total_supply=1000)
        holder_a = "0x" + "01" * 20
        logs = [
            _transfer_log(ZERO_ADDRESS, holder_a, 1000),
            _transfer_log(holder_a, ZERO_ADDRESS, 300),  # burn
        ]
        provider = StubProvider(logs=logs)
        on_block = make_on_block(provider, factory)
        await on_block({"number": 10})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            holders = (await session.execute(select(TokenHolder))).scalars().all()
            addresses = {h.holder_address for h in holders}
            assert ZERO_ADDRESS not in addresses
            assert int(holders[0].balance) == 700


class TestConcentrationStats:
    @pytest.mark.asyncio
    async def test_top5_top10_top20_percentages(self, session_factory):
        factory, schema = session_factory
        await _seed_token(factory, schema, total_supply=1000)
        # 10 holders, 100 each, sums to total_supply exactly.
        logs = [
            _transfer_log(ZERO_ADDRESS, f"0x{i:040x}", 100) for i in range(1, 11)
        ]
        provider = StubProvider(logs=logs)
        on_block = make_on_block(provider, factory)
        await on_block({"number": 10})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            conc = (await session.execute(select(HolderConcentration))).scalar_one()
            assert conc.largest_holder_pct == pytest.approx(10.0)
            assert conc.top5_pct == pytest.approx(50.0)
            assert conc.top10_pct == pytest.approx(100.0)
            assert conc.top20_pct == pytest.approx(100.0)  # only 10 holders exist
            assert conc.holder_count == 10

    @pytest.mark.asyncio
    async def test_creator_holdings_tracked(self, session_factory):
        factory, schema = session_factory
        await _seed_token(factory, schema, total_supply=1000)
        logs = [_transfer_log(ZERO_ADDRESS, DEPLOYER, 500)]
        provider = StubProvider(logs=logs)
        on_block = make_on_block(provider, factory)
        await on_block({"number": 10})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            conc = (await session.execute(select(HolderConcentration))).scalar_one()
            assert conc.creator_holdings_pct == pytest.approx(50.0)

    @pytest.mark.asyncio
    async def test_largest_holder_category_attached(self, session_factory):
        """The largest holder's address classification should be
        attached -- here the deployer itself, so it should classify
        as 'deployer' per spec sec.13."""
        factory, schema = session_factory
        await _seed_token(factory, schema, total_supply=1000)
        logs = [_transfer_log(ZERO_ADDRESS, DEPLOYER, 1000)]
        provider = StubProvider(logs=logs)
        on_block = make_on_block(provider, factory)
        await on_block({"number": 10})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            conc = (await session.execute(select(HolderConcentration))).scalar_one()
            assert conc.largest_holder_address == DEPLOYER
            assert conc.largest_holder_category == "deployer"


class TestNoHoldersEdgeCase:
    @pytest.mark.asyncio
    async def test_zero_transfers_yields_empty_concentration(self, session_factory):
        factory, schema = session_factory
        await _seed_token(factory, schema, total_supply=1000)
        provider = StubProvider(logs=[])
        on_block = make_on_block(provider, factory)
        await on_block({"number": 10})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            conc = (await session.execute(select(HolderConcentration))).scalar_one()
            assert conc.holder_count == 0
            assert conc.largest_holder_pct == 0.0
            assert conc.largest_holder_address is None


class TestTransportErrorsAndIdempotency:
    @pytest.mark.asyncio
    async def test_transport_error_leaves_token_unanalyzed(self, session_factory):
        factory, schema = session_factory
        await _seed_token(factory, schema)
        provider = StubProvider(raise_error=True)
        on_block = make_on_block(provider, factory)
        await on_block({"number": 10})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            t = (await session.execute(select(Token))).scalar_one()
            assert t.holder_analysis_analyzed_at is None
            assert (await session.execute(select(TokenHolder))).scalars().all() == []

    @pytest.mark.asyncio
    async def test_no_unanalyzed_tokens_is_noop(self, session_factory):
        factory, schema = session_factory
        provider = StubProvider()
        on_block = make_on_block(provider, factory)
        await on_block({"number": 1})
        assert provider.calls == []

    @pytest.mark.asyncio
    async def test_replay_does_not_duplicate_holders(self, session_factory):
        factory, schema = session_factory
        await _seed_token(factory, schema, total_supply=1000)
        logs = [_transfer_log(ZERO_ADDRESS, "0x" + "01" * 20, 1000)]
        provider = StubProvider(logs=logs)
        on_block = make_on_block(provider, factory)
        await on_block({"number": 10})
        # holder_analysis_analyzed_at is now set -- a second tick
        # shouldn't pick this token up again at all.
        await on_block({"number": 11})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            holders = (await session.execute(select(TokenHolder))).scalars().all()
            assert len(holders) == 1
        assert len(provider.calls) == 1  # only the first tick issued a call


class TestLogChunking:
    @pytest.mark.asyncio
    async def test_wide_block_range_is_chunked(self, session_factory):
        factory, schema = session_factory
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            session.add(
                ContractDeployment(
                    contract_address=TOKEN_ADDR, deployer=DEPLOYER,
                    creation_tx="0x" + "33" * 32, creation_block=1,
                    is_erc20=True, erc20_checked_at=datetime.now(timezone.utc),
                )
            )
            session.add(
                Token(
                    contract_address=TOKEN_ADDR, deployer=DEPLOYER,
                    name="T", symbol="T", decimals=18, total_supply=1000,
                    creation_block=1, creation_timestamp=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        provider = StubProvider(logs=[])
        on_block = make_on_block(provider, factory, max_log_range=100)
        # from_block=1, to_block=250 with max_log_range=100 -> 3 chunks
        await on_block({"number": 250})
        assert len(provider.calls) == 3
        assert provider.calls[0] == (1, 100)
        assert provider.calls[1] == (101, 200)
        assert provider.calls[2] == (201, 250)
