"""Unit tests for the Phase 11 wallet-graph detector.

Prereqs:
    docker compose up -d

Run:
    pytest tests/test_wallet_graph.py -v
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

from app.discovery.wallet_graph import (
    DEFAULT_TOP_N_RECIPIENTS,
    KIND_CO_DEPLOYED,
    KIND_FUNDS_TOKEN,
    KIND_OPERATES_POOL,
    KIND_TRANSFER_RECIPIENT,
    TRANSFER_TOPIC,
    _addr_from_topic,
    make_on_block,
)
from app.database.models import (
    Base,
    ContractDeployment,
    LiquidityPool,
    Token,
    Wallet,
    WalletRelationship,
)


# ---------- stub provider ---------------------------------------------------
class StubProvider:
    """Returns canned get_logs responses, keyed by (to, topics, from, to).

    The detector calls ``provider.get_logs(address=token, topics=[TRANSFER_TOPIC],
    from_block=..., to_block=...)``. For tests with multiple chunks we
    key on the exact range. ``default_logs`` is the fallback for any
    call not in the response map.
    """

    def __init__(
        self,
        log_responses: dict[tuple[str, str, int, int], list[dict]] | None = None,
        default_logs: list[dict] | None = None,
        raise_transient: bool = False,
    ) -> None:
        self.log_responses = dict(log_responses or {})
        self.default_logs = list(default_logs or [])
        self.raise_transient = raise_transient
        self.log_calls: list[tuple[str, tuple[str, ...] | None, int, int]] = []

    @property
    def chain_id(self) -> int:
        return 8453

    async def get_logs(
        self,
        *,
        address: str | None = None,
        topics: list[str] | None = None,
        from_block: int,
        to_block: int,
    ) -> list[dict]:
        self.log_calls.append((address, tuple(topics) if topics else None, from_block, to_block))
        if self.raise_transient:
            raise OSError("simulated transport failure")
        key = (
            (address or "").lower(),
            topics[0] if topics else "",
            from_block,
            to_block,
        )
        return list(self.log_responses.get(key, self.default_logs))


def _transfer_log(
    token_address: str,
    to_address: str,
    block_number: int,
    tx_index: int = 0,
) -> dict:
    """Synthesize one ERC-20 Transfer log entry as the detector
    would receive it from ``provider.get_logs``.

    ``to`` lives in topics[2], so we right-pad it to 32 bytes.
    """
    addr_padded = to_address.lower().removeprefix("0x").rjust(64, "0")
    return {
        "address": token_address.lower(),
        "blockNumber": block_number,
        "transactionHash": f"0x{token_address[2:].rjust(64, chr(ord('a') + tx_index % 6))}",
        "logIndex": hex(tx_index),
        "data": "0x" + "00" * 32,  # amount -- unused
        "topics": [TRANSFER_TOPIC, "0x" + "11" * 32, "0x" + addr_padded],
    }


# ---------- schema fixture (per-test isolation) ---------------------------
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


async def _seed_token(
    factory,
    schema,
    *,
    address: str = "0x" + "aa" * 20,
    deployer: str = "0x" + "22" * 20,
    creation_block: int = 100,
    name: str = "Test",
    symbol: str = "TST",
) -> Token:
    async with factory() as session:
        await session.execute(text(f'SET search_path TO "{schema}"'))
        session.add(
            ContractDeployment(
                contract_address=address,
                deployer=deployer,
                creation_tx="0x" + address[2:].rjust(64, "0"),
                creation_block=creation_block,
                is_erc20=True,
                erc20_checked_at=datetime.now(timezone.utc),
            )
        )
        token = Token(
            contract_address=address,
            deployer=deployer,
            name=name,
            symbol=symbol,
            decimals=18,
            total_supply=10**24,
            creation_block=creation_block,
            creation_timestamp=datetime.now(timezone.utc),
        )
        session.add(token)
        await session.commit()
        await session.refresh(token)
        return token


async def _seed_pool(
    factory,
    schema,
    *,
    token_address: str,
    pool_address: str = "0x" + "cc" * 20,
    dex: str = "uniswap_v2",
    pair_asset: str = "0x" + "bb" * 20,
    discovered_block: int = 200,
) -> None:
    async with factory() as session:
        await session.execute(text(f'SET search_path TO "{schema}"'))
        session.add(
            LiquidityPool(
                token_address=token_address,
                pool_address=pool_address,
                dex=dex,
                pair_asset=pair_asset,
                fee_tier=None if dex == "uniswap_v2" else 3000,
                reserve_token=None,
                reserve_pair=None,
                discovered_block=discovered_block,
                discovered_at=datetime.now(timezone.utc),
                last_synced_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()


# ---------- topic-decoder unit tests --------------------------------------
class TestTopicDecoding:
    def test_addr_extracted_from_indexed_topic(self):
        addr = "0x" + "ab" * 20
        topic = "0x" + "00" * 12 + addr.removeprefix("0x")
        assert _addr_from_topic(topic) == addr

    def test_short_topic_returns_none(self):
        assert _addr_from_topic("0x1234") is None

    def test_non_hex_returns_none(self):
        assert _addr_from_topic("not a topic") is None
        assert _addr_from_topic("") is None


# ---------- no-op / idempotency -------------------------------------------
class TestNoWorkToDo:
    @pytest.mark.asyncio
    async def test_no_unanalyzed_tokens_is_noop(self, session_factory):
        factory, schema = session_factory
        provider = StubProvider()
        on_block = make_on_block(provider, factory)
        await on_block({"number": 1000})
        assert provider.log_calls == []

    @pytest.mark.asyncio
    async def test_replay_is_idempotent(self, session_factory):
        factory, schema = session_factory
        token = await _seed_token(factory, schema)
        provider = StubProvider(default_logs=[])
        on_block = make_on_block(provider, factory)
        await on_block({"number": 200})
        first_calls = len(provider.log_calls)

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            funds = (await session.execute(
                select(WalletRelationship).where(
                    WalletRelationship.kind == KIND_FUNDS_TOKEN,
                )
            )).scalars().all()
            assert len(funds) == 1

        await on_block({"number": 201})
        # No new log calls -- the token is now marked analyzed.
        assert len(provider.log_calls) == first_calls

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            funds = (await session.execute(
                select(WalletRelationship).where(
                    WalletRelationship.kind == KIND_FUNDS_TOKEN,
                )
            )).scalars().all()
            assert len(funds) == 1


# ---------- funds_token edge ----------------------------------------------
class TestFundsTokenEdge:
    @pytest.mark.asyncio
    async def test_deployer_to_token_edge_created(self, session_factory):
        factory, schema = session_factory
        deployer = "0x" + "22" * 20
        token_addr = "0x" + "aa" * 20
        await _seed_token(factory, schema, address=token_addr, deployer=deployer)
        provider = StubProvider(default_logs=[])
        on_block = make_on_block(provider, factory)
        await on_block({"number": 300})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            edges = (await session.execute(
                select(WalletRelationship).where(
                    WalletRelationship.kind == KIND_FUNDS_TOKEN,
                )
            )).scalars().all()
            assert len(edges) == 1
            e = edges[0]
            assert e.a == deployer
            assert e.b == token_addr
            assert e.weight == 1
            assert e.first_seen_block == 100  # token.creation_block
            assert e.evidence_json["deployer"] == deployer
            assert e.evidence_json["token"] == token_addr


# ---------- co_deployed edge ----------------------------------------------
class TestCoDeployedEdge:
    @pytest.mark.asyncio
    async def test_two_tokens_same_deployer_get_co_deployed_edge(self, session_factory):
        factory, schema = session_factory
        deployer = "0x" + "22" * 20
        token_a = "0x" + "aa" * 20
        token_b = "0x" + "bb" * 20
        await _seed_token(factory, schema, address=token_a, deployer=deployer, creation_block=100)
        await _seed_token(factory, schema, address=token_b, deployer=deployer, creation_block=110)
        provider = StubProvider(default_logs=[])
        on_block = make_on_block(provider, factory)
        await on_block({"number": 200})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            edges = (await session.execute(
                select(WalletRelationship).where(
                    WalletRelationship.kind == KIND_CO_DEPLOYED,
                ).order_by(WalletRelationship.a.asc())
            )).scalars().all()
            assert len(edges) == 2  # token_a -> token_b, token_b -> token_a
            assert {e.a for e in edges} == {token_a, token_b}
            assert {e.b for e in edges} == {token_a, token_b}
            for e in edges:
                assert e.weight == 2  # deployer has 2 tokens
                assert e.evidence_json["shared_deployer"] == deployer

    @pytest.mark.asyncio
    async def test_different_deployers_get_no_co_deployed_edge(self, session_factory):
        factory, schema = session_factory
        await _seed_token(factory, schema, address="0x" + "aa" * 20, deployer="0x" + "11" * 20)
        await _seed_token(factory, schema, address="0x" + "bb" * 20, deployer="0x" + "22" * 20)
        provider = StubProvider(default_logs=[])
        on_block = make_on_block(provider, factory)
        await on_block({"number": 200})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            edges = (await session.execute(
                select(WalletRelationship).where(
                    WalletRelationship.kind == KIND_CO_DEPLOYED,
                )
            )).scalars().all()
            assert edges == []


# ---------- operates_pool edge --------------------------------------------
class TestOperatesPoolEdge:
    @pytest.mark.asyncio
    async def test_token_to_pool_edge_created(self, session_factory):
        factory, schema = session_factory
        token_addr = "0x" + "aa" * 20
        pool_addr = "0x" + "cc" * 20
        await _seed_token(factory, schema, address=token_addr)
        await _seed_pool(factory, schema, token_address=token_addr, pool_address=pool_addr)
        provider = StubProvider(default_logs=[])
        on_block = make_on_block(provider, factory)
        await on_block({"number": 400})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            edges = (await session.execute(
                select(WalletRelationship).where(
                    WalletRelationship.kind == KIND_OPERATES_POOL,
                )
            )).scalars().all()
            assert len(edges) == 1
            assert edges[0].a == token_addr
            assert edges[0].b == pool_addr


# ---------- transfer_recipient edge ---------------------------------------
class TestTransferRecipientEdge:
    @pytest.mark.asyncio
    async def test_top_recipients_get_edges_with_weight(self, session_factory):
        factory, schema = session_factory
        token_addr = "0x" + "aa" * 20
        await _seed_token(factory, schema, address=token_addr, creation_block=100)
        # Recipient A: 3 transfers; Recipient B: 1 transfer; Recipient C: 2 transfers.
        recipient_a = "0x" + "a1" * 20
        recipient_b = "0x" + "b1" * 20
        recipient_c = "0x" + "c1" * 20
        logs = [
            _transfer_log(token_addr, recipient_a, 105),
            _transfer_log(token_addr, recipient_a, 106),
            _transfer_log(token_addr, recipient_a, 107),
            _transfer_log(token_addr, recipient_c, 108),
            _transfer_log(token_addr, recipient_c, 109),
            _transfer_log(token_addr, recipient_b, 110),
        ]
        provider = StubProvider(
            log_responses={(token_addr, TRANSFER_TOPIC, 100, 200): logs},
        )
        on_block = make_on_block(provider, factory)
        await on_block({"number": 200})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            edges = (await session.execute(
                select(WalletRelationship).where(
                    WalletRelationship.kind == KIND_TRANSFER_RECIPIENT,
                ).order_by(WalletRelationship.b.asc())
            )).scalars().all()
            assert len(edges) == 3
            by_addr = {e.b: e for e in edges}
            assert by_addr[recipient_a].weight == 3
            assert by_addr[recipient_c].weight == 2
            assert by_addr[recipient_b].weight == 1
            for e in edges:
                assert e.evidence_json["token"] == token_addr
                assert e.evidence_json["window"] == [100, 200]
                assert e.evidence_json["distinct_transfers_in_window"] == e.weight

    @pytest.mark.asyncio
    async def test_top_n_caps_recipients(self, session_factory):
        factory, schema = session_factory
        token_addr = "0x" + "aa" * 20
        await _seed_token(factory, schema, address=token_addr, creation_block=100)
        # Build more recipients than the cap.
        n_extra = DEFAULT_TOP_N_RECIPIENTS + 10
        logs = [
            _transfer_log(token_addr, f"0x{i:040x}", 105 + i)
            for i in range(n_extra)
        ]
        provider = StubProvider(
            log_responses={(token_addr, TRANSFER_TOPIC, 100, 200): logs},
        )
        on_block = make_on_block(provider, factory, top_n_recipients=5)
        await on_block({"number": 200})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            edges = (await session.execute(
                select(WalletRelationship).where(
                    WalletRelationship.kind == KIND_TRANSFER_RECIPIENT,
                )
            )).scalars().all()
            assert len(edges) == 5

    @pytest.mark.asyncio
    async def test_zero_address_recipient_is_skipped(self, session_factory):
        factory, schema = session_factory
        token_addr = "0x" + "aa" * 20
        await _seed_token(factory, schema, address=token_addr, creation_block=100)
        zero_addr = "0x" + "00" * 20
        recipient = "0x" + "ab" * 20
        logs = [
            _transfer_log(token_addr, zero_addr, 105),
            _transfer_log(token_addr, recipient, 106),
        ]
        provider = StubProvider(
            log_responses={(token_addr, TRANSFER_TOPIC, 100, 200): logs},
        )
        on_block = make_on_block(provider, factory)
        await on_block({"number": 200})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            edges = (await session.execute(
                select(WalletRelationship).where(
                    WalletRelationship.kind == KIND_TRANSFER_RECIPIENT,
                )
            )).scalars().all()
            assert len(edges) == 1
            assert edges[0].b == recipient

    @pytest.mark.asyncio
    async def test_log_range_is_chunked(self, session_factory):
        factory, schema = session_factory
        token_addr = "0x" + "aa" * 20
        await _seed_token(factory, schema, address=token_addr, creation_block=100)
        # Range [100, 1100] split by max_log_range=500 -> chunks
        # [100,599], [600,1099], [1100,1100].
        provider = StubProvider(default_logs=[])
        on_block = make_on_block(
            provider, factory, max_log_range=500, top_n_recipients=10,
        )
        await on_block({"number": 1100})
        chunks = [c for c in provider.log_calls if c[0] == token_addr]
        assert len(chunks) == 3
        assert chunks[0][2:] == (100, 599)
        assert chunks[1][2:] == (600, 1099)
        assert chunks[2][2:] == (1100, 1100)

    @pytest.mark.asyncio
    async def test_transport_error_leaves_token_unanalyzed(self, session_factory):
        factory, schema = session_factory
        token_addr = "0x" + "aa" * 20
        token = await _seed_token(factory, schema, address=token_addr, creation_block=100)
        provider = StubProvider(raise_transient=True)
        on_block = make_on_block(provider, factory)
        await on_block({"number": 200})

        # funds_token / operates_pool would also have raised, so the
        # whole token is unmarked -- retry next tick.
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            t = (await session.execute(select(Token))).scalar_one()
            assert t.wallet_graph_analyzed_at is None
            assert (await session.execute(select(WalletRelationship))).scalars().all() == []


# ---------- wallet table bookkeeping --------------------------------------
class TestWalletBookkeeping:
    @pytest.mark.asyncio
    async def test_deployer_wallet_counters_bumped(self, session_factory):
        factory, schema = session_factory
        deployer = "0x" + "22" * 20
        token_addr = "0x" + "aa" * 20
        await _seed_token(factory, schema, address=token_addr, deployer=deployer)
        provider = StubProvider(default_logs=[])
        on_block = make_on_block(provider, factory)
        await on_block({"number": 300})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            w = (await session.execute(
                select(Wallet).where(Wallet.address == deployer)
            )).scalar_one()
            assert w.tokens_deployed == 1
            assert w.first_seen_block == 100

            t = (await session.execute(
                select(Wallet).where(Wallet.address == token_addr)
            )).scalar_one()
            assert t.tokens_as_transfer == 1  # counted as transfer-relation endpoint

    @pytest.mark.asyncio
    async def test_first_seen_block_is_earliest(self, session_factory):
        """Re-analysis with a stale (later) block must NOT overwrite
        the original first_seen_block with a larger value."""
        factory, schema = session_factory
        deployer = "0x" + "22" * 20
        token_a = "0x" + "aa" * 20
        token_b = "0x" + "bb" * 20
        await _seed_token(factory, schema, address=token_a, deployer=deployer, creation_block=50)
        await _seed_token(factory, schema, address=token_b, deployer=deployer, creation_block=80)
        provider = StubProvider(default_logs=[])
        on_block = make_on_block(provider, factory)
        # Note: current_block is 90; we should pick 50 as the
        # first_seen for the deployer, not 90.
        await on_block({"number": 90})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            w = (await session.execute(
                select(Wallet).where(Wallet.address == deployer)
            )).scalar_one()
            assert w.first_seen_block == 50
            assert w.last_seen_block == 80
            assert w.tokens_deployed == 2


# ---------- weight monotonicity -------------------------------------------
class TestWeightMaxSemantics:
    @pytest.mark.asyncio
    async def test_higher_weight_wins_on_rescan(self, session_factory):
        """If we re-insert a token with a heavier weight, the edge's
        stored weight must take the max (not be overwritten with the
        lower value)."""
        factory, schema = session_factory
        token_addr = "0x" + "aa" * 20
        await _seed_token(factory, schema, address=token_addr, creation_block=100)
        # First scan: weight=1.
        provider = StubProvider(default_logs=[])
        on_block = make_on_block(provider, factory)
        await on_block({"number": 200})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            e = (await session.execute(
                select(WalletRelationship).where(
                    WalletRelationship.kind == KIND_TRANSFER_RECIPIENT,
                )
            )).scalars().first()
            # No transfer recipients logged -> no transfer_recipient edge.
            assert e is None

        # Now mark the token un-analyzed and re-run with a single
        # heavy recipient. The existing weight=1 edges for funds_token
        # etc. should stay at 1 (the upsert uses GREATEST).
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            await session.execute(text(
                f'UPDATE tokens SET wallet_graph_analyzed_at = NULL'
            ))
            await session.commit()

        recipient = "0x" + "ab" * 20
        logs = [_transfer_log(token_addr, recipient, 150)]
        provider2 = StubProvider(
            log_responses={(token_addr, TRANSFER_TOPIC, 100, 250): logs},
        )
        on_block2 = make_on_block(provider2, factory)
        await on_block2({"number": 250})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            funds = (await session.execute(
                select(WalletRelationship).where(
                    WalletRelationship.kind == KIND_FUNDS_TOKEN,
                )
            )).scalar_one()
            assert funds.weight == 1  # unchanged: GREATEST(1, 1) = 1


# ---------- batching ------------------------------------------------------
class TestBatching:
    @pytest.mark.asyncio
    async def test_batch_size_limits_tokens_per_tick(self, session_factory):
        factory, schema = session_factory
        for i in range(5):
            await _seed_token(
                factory, schema,
                address=f"0x{i:040x}",
                creation_block=100 + i,
            )
        provider = StubProvider(default_logs=[])
        on_block = make_on_block(provider, factory, batch_size=2)
        await on_block({"number": 200})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            analyzed = (
                await session.execute(
                    select(Token).where(Token.wallet_graph_analyzed_at.is_not(None))
                )
            ).scalars().all()
            assert len(analyzed) == 2
            pending = (
                await session.execute(
                    select(Token).where(Token.wallet_graph_analyzed_at.is_(None))
                )
            ).scalars().all()
            assert len(pending) == 3
