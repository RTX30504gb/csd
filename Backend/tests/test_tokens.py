"""Unit tests for the ERC-20 detector.

Mirrors ``test_deployments.py``'s per-test PG schema isolation so
the tests can be run in any order without poisoning the public
``tokens`` / ``contract_deployments`` tables.

Prereqs:
    docker compose up -d

Run:
    pytest tests/test_tokens.py -v
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.discovery.tokens import (
    DecodeError,
    _decode_string,
    _decode_uint8,
    _decode_uint256,
    _truncate,
    make_on_block,
)
from app.database.models import (
    Base,
    ContractDeployment,
    Token,
)


# ---------- stub provider ---------------------------------------------------
class StubProvider:
    """A provider that returns canned bytes for each eth_call selector.

    The detector's ``_probe_one`` calls four methods in a fixed
    order: name, symbol, decimals, totalSupply. The stub returns
    the corresponding entry from ``self.responses`` (a list of
    4-tuples). For "revert" tests, an entry can be the string
    ``"revert"`` to raise ``ContractLogicError``; for transport
    errors, ``"transient"`` raises ``OSError``.
    """

    def __init__(self, responses: list[Any], chain_id: int = 8453) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []
        self.get_block_calls: list[int] = []
        self._chain_id = chain_id

    @property
    def chain_id(self) -> int:
        return self._chain_id

    async def get_eth_call(self, to: str, data: str) -> bytes:
        self.calls.append((to, data))
        if not self.responses:
            raise RuntimeError("stub exhausted")
        resp = self.responses.pop(0)
        if resp == "revert":
            from web3.exceptions import ContractLogicError
            raise ContractLogicError("execution reverted: stub", data=data)
        if resp == "transient":
            raise OSError("simulated network failure")
        if isinstance(resp, BaseException):
            raise resp
        return resp

    async def get_block(self, block_number: int, full_transactions: bool = False) -> dict:
        self.get_block_calls.append(block_number)
        return {
            "number": block_number,
            "hash": f"0x{block_number:064x}",
            "timestamp": 1_700_000_000 + block_number,
            "transactions": [],
        }


# ---------- ABI encoder helpers (for test fixtures) ------------------------
def _enc_string(s: str) -> bytes:
    raw = s.encode("utf-8")
    n = len(raw)
    return (
        (32).to_bytes(32, "big")           # offset = 0x20
        + n.to_bytes(32, "big")
        + raw
        + b"\x00" * ((-n) % 32)            # pad to 32
    )


def _enc_uint8(v: int) -> bytes:
    return b"\x00" * 31 + bytes([v & 0xFF])


def _enc_uint256(v: int) -> bytes:
    return v.to_bytes(32, "big")


# ---------- schema fixture (per-test isolation) ---------------------------
@pytest_asyncio.fixture
async def session_factory():
    """Fresh PG schema per test."""
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


# ---------- helpers --------------------------------------------------------
async def _seed_deployment(
    factory, schema, address: str = "0x" + "11" * 20, deployer: str = "0x" + "22" * 20,
    creation_block: int = 100, deployer_override: str | None = None,
) -> ContractDeployment:
    # Derive a unique creation_tx from the address so multiple
    # deployments in one test don't violate the unique constraint.
    creation_tx = "0x" + address[2:].rjust(64, "0")
    async with factory() as session:
        await session.execute(text(f'SET search_path TO "{schema}"'))
        row = ContractDeployment(
            contract_address=address,
            deployer=deployer_override or deployer,
            creation_tx=creation_tx,
            creation_block=creation_block,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row


# =========================================================================
# Pure-function decoders
# =========================================================================
class TestDecoders:
    def test_decode_string_hello(self):
        assert _decode_string(_enc_string("Hello")) == "Hello"

    def test_decode_string_empty_returns_none(self):
        # length = 0 -> None (legitimately empty name/symbol)
        assert _decode_string(_enc_string("")) is None

    def test_decode_string_too_short_raises(self):
        with pytest.raises(DecodeError):
            _decode_string(b"\x00" * 32)

    def test_decode_string_wrong_offset_raises(self):
        data = (16).to_bytes(32, "big") + (0).to_bytes(32, "big")
        with pytest.raises(DecodeError):
            _decode_string(data)

    def test_decode_uint8(self):
        assert _decode_uint8(_enc_uint8(18)) == 18
        assert _decode_uint8(_enc_uint8(0)) == 0
        assert _decode_uint8(_enc_uint8(255)) == 255

    def test_decode_uint256(self):
        assert _decode_uint256(_enc_uint256(1_000_000_000_000_000_000_000_000)) == 10**24
        assert _decode_uint256(_enc_uint256(0)) == 0

    def test_truncate(self):
        assert _truncate(None) is None
        assert _truncate("a" * 100) == "a" * 100
        assert _truncate("a" * 1000) == "a" * 256


# =========================================================================
# Detector: happy paths
# =========================================================================
class TestDetectorSuccess:
    @pytest.mark.asyncio
    async def test_standard_erc20_recorded(self, session_factory):
        factory, schema = session_factory
        addr = "0x" + "aa" * 20
        await _seed_deployment(factory, schema, address=addr, creation_block=12345)
        provider = StubProvider([
            _enc_string("MyToken"),
            _enc_string("MTK"),
            _enc_uint8(18),
            _enc_uint256(1_000_000 * 10**18),
        ])
        on_block = make_on_block(provider, factory)
        await on_block({"number": 12346, "timestamp": 1_700_000_000})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            tokens = (await session.execute(select(Token))).scalars().all()
            assert len(tokens) == 1
            t = tokens[0]
            assert t.contract_address == addr
            assert t.name == "MyToken"
            assert t.symbol == "MTK"
            assert t.decimals == 18
            assert t.total_supply == 1_000_000 * 10**18
            assert t.creation_block == 12345

            dep = (await session.execute(select(ContractDeployment))).scalar_one()
            assert dep.is_erc20 is True
            assert dep.erc20_checked_at is not None

    @pytest.mark.asyncio
    async def test_erc20_with_empty_name_is_recorded(self, session_factory):
        factory, schema = session_factory
        addr = "0x" + "bb" * 20
        await _seed_deployment(factory, schema, address=addr)
        provider = StubProvider([
            _enc_string(""),       # empty name
            _enc_string("XXX"),
            _enc_uint8(6),
            _enc_uint256(10**12),
        ])
        on_block = make_on_block(provider, factory)
        await on_block({"number": 1, "timestamp": 1_700_000_000})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            t = (await session.execute(select(Token))).scalar_one()
            assert t.name is None        # empty -> None
            assert t.symbol == "XXX"
            assert t.decimals == 6

    @pytest.mark.asyncio
    async def test_idempotent_on_replay(self, session_factory):
        """Running the detector twice on the same deployment does
        not create duplicate token rows."""
        factory, schema = session_factory
        await _seed_deployment(factory, schema)
        responses = [
            _enc_string("T"), _enc_string("S"), _enc_uint8(18), _enc_uint256(1000),
        ]
        provider = StubProvider(responses + responses)
        on_block = make_on_block(provider, factory)
        await on_block({"number": 5, "timestamp": 1_700_000_000})
        await on_block({"number": 6, "timestamp": 1_700_000_001})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            tokens = (await session.execute(select(Token))).scalars().all()
            assert len(tokens) == 1


# =========================================================================
# Detector: failure paths
# =========================================================================
class TestDetectorFailures:
    @pytest.mark.asyncio
    async def test_revert_on_name_marks_checked_not_erc20(self, session_factory):
        factory, schema = session_factory
        await _seed_deployment(factory, schema)
        provider = StubProvider(["revert"])
        on_block = make_on_block(provider, factory)
        await on_block({"number": 1, "timestamp": 1_700_000_000})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            tokens = (await session.execute(select(Token))).scalars().all()
            assert tokens == []
            dep = (await session.execute(select(ContractDeployment))).scalar_one()
            assert dep.is_erc20 is False
            assert dep.erc20_checked_at is not None  # marked as probed

    @pytest.mark.asyncio
    async def test_revert_on_decimals_also_marks_not_erc20(self, session_factory):
        factory, schema = session_factory
        await _seed_deployment(factory, schema)
        provider = StubProvider([
            _enc_string("X"), _enc_string("X"),
            "revert",                 # decimals reverts
            _enc_uint256(1),
        ])
        on_block = make_on_block(provider, factory)
        await on_block({"number": 1, "timestamp": 1_700_000_000})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            assert (await session.execute(select(Token))).scalars().all() == []
            dep = (await session.execute(select(ContractDeployment))).scalar_one()
            assert dep.is_erc20 is False
            assert dep.erc20_checked_at is not None

    @pytest.mark.asyncio
    async def test_garbage_response_marks_not_erc20(self, session_factory):
        factory, schema = session_factory
        await _seed_deployment(factory, schema)
        provider = StubProvider([
            _enc_string("T"),
            _enc_string("S"),
            _enc_uint8(18),
            b"",                 # empty totalSupply -> DecodeError
        ])
        on_block = make_on_block(provider, factory)
        await on_block({"number": 1, "timestamp": 1_700_000_000})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            assert (await session.execute(select(Token))).scalars().all() == []
            dep = (await session.execute(select(ContractDeployment))).scalar_one()
            assert dep.is_erc20 is False
            assert dep.erc20_checked_at is not None

    @pytest.mark.asyncio
    async def test_transport_error_leaves_unchecked(self, session_factory):
        """A network/RPC error must not mark the contract as probed --
        it should be retried on a later tick."""
        factory, schema = session_factory
        await _seed_deployment(factory, schema)
        provider = StubProvider(["transient"])
        on_block = make_on_block(provider, factory)
        await on_block({"number": 1, "timestamp": 1_700_000_000})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            assert (await session.execute(select(Token))).scalars().all() == []
            dep = (await session.execute(select(ContractDeployment))).scalar_one()
            assert dep.is_erc20 is False
            assert dep.erc20_checked_at is None  # still NULL -> will retry

    @pytest.mark.asyncio
    async def test_no_deployments_is_noop(self, session_factory):
        factory, schema = session_factory
        provider = StubProvider([])        # no deployments in DB
        on_block = make_on_block(provider, factory)
        await on_block({"number": 1, "timestamp": 1_700_000_000})
        # No probes, no errors, no rows.

    @pytest.mark.asyncio
    async def test_batch_size_limits_probes(self, session_factory):
        """If there are 5 deployments and batch_size=2, only 2 are
        probed in this tick. The rest stay NULL and will be picked
        up later."""
        factory, schema = session_factory
        for i in range(5):
            await _seed_deployment(
                factory, schema,
                address=f"0x{i:040x}",
            )
        provider = StubProvider([
            _enc_string("A"), _enc_string("A"), _enc_uint8(18), _enc_uint256(1),
        ] * 2)        # only 2 *sets* = 2 deployments
        on_block = make_on_block(provider, factory, batch_size=2)
        await on_block({"number": 1, "timestamp": 1_700_000_000})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            tokens = (await session.execute(select(Token))).scalars().all()
            assert len(tokens) == 2
            confirmed = (await session.execute(
                select(ContractDeployment).where(ContractDeployment.is_erc20.is_(True))
            )).scalars().all()
            assert len(confirmed) == 2
            pending = (await session.execute(
                select(ContractDeployment).where(ContractDeployment.erc20_checked_at.is_(None))
            )).scalars().all()
            assert len(pending) == 3        # 3 still to probe