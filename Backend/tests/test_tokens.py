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
        # 16 bytes is neither a valid string (min 64) nor a bytes32
        # (needs exactly 32). Must raise so the contract is marked
        # not-ERC-20 instead of being silently dropped.
        with pytest.raises(DecodeError):
            _decode_string(b"\x00" * 16)

    def test_decode_string_wrong_offset_raises(self):
        data = (16).to_bytes(32, "big") + (0).to_bytes(32, "big")
        with pytest.raises(DecodeError):
            _decode_string(data)

    def test_decode_bytes32_padded(self):
        # USDT-style: "USDT" right-padded with NULs to 32 bytes.
        raw = b"USDT" + b"\x00" * 28
        assert _decode_string(raw) == "USDT"

    def test_decode_bytes32_full(self):
        # 32 bytes, no trailing NULs.
        raw = b"A" * 32
        assert _decode_string(raw) == "A" * 32

    def test_decode_bytes32_empty_returns_none(self):
        # All-zero bytes32 -> None (same semantics as empty string).
        assert _decode_string(b"\x00" * 32) is None

    def test_decode_bytes32_invalid_utf8_raises(self):
        # Random non-utf8 bytes in a 32-byte slot — must raise so
        # we don't silently store garbage in the DB.
        raw = b"\xff" * 32
        with pytest.raises(DecodeError):
            _decode_string(raw)

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
    async def test_erc20_with_bytes32_name_and_symbol(self, session_factory):
        """USDT-style tokens return bytes32 for name/symbol.

        The detector must accept both layouts and record the token.
        """
        factory, schema = session_factory
        addr = "0x" + "cc" * 20
        await _seed_deployment(factory, schema, address=addr)
        provider = StubProvider([
            b"Tether USD" + b"\x00" * 22,    # bytes32 name (10 + 22 = 32)
            b"USDT" + b"\x00" * 28,         # bytes32 symbol (4 + 28 = 32)
            _enc_uint8(6),
            _enc_uint256(10**12),
        ])
        on_block = make_on_block(provider, factory)
        await on_block({"number": 1, "timestamp": 1_700_000_000})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            t = (await session.execute(select(Token))).scalar_one()
            assert t.name == "Tether USD"
            assert t.symbol == "USDT"
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


# =========================================================================
# Phase 4 close-out: bytes32 fallback + GET /tokens/{address}
# =========================================================================
class TestBytes32Fallback:
    """USDT-style contracts return ``bytes32`` for name/symbol rather
    than ``string``. The detector must accept both layouts so we
    don't mis-classify well-known tokens as non-ERC-20."""

    @pytest.mark.asyncio
    async def test_end_to_end_bytes32(self, session_factory):
        factory, schema = session_factory
        addr = "0x" + "cc" * 20
        await _seed_deployment(factory, schema, address=addr)
        provider = StubProvider([
            b"Tether USD" + b"\x00" * 22,    # bytes32 name (10 + 22 = 32)
            b"USDT" + b"\x00" * 28,         # bytes32 symbol (4 + 28 = 32)
            _enc_uint8(6),
            _enc_uint256(10**12),
        ])
        on_block = make_on_block(provider, factory)
        await on_block({"number": 1, "timestamp": 1_700_000_000})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            t = (await session.execute(select(Token))).scalar_one()
            assert t.name == "Tether USD"
            assert t.symbol == "USDT"
            assert t.decimals == 6


class TestTokenDetailEndpoint:
    """The HTTP endpoint backing the dashboard's Token Page.

    ``GET /tokens/{address}`` must return the full record or 404.
    Address matching is case-insensitive so callers can paste
    EIP-55 checksummed addresses.

    Implementation note: we build a fresh ASGI app per test whose
    DB engine has its connection-level ``search_path`` pinned to
    the per-test schema. This isolates the HTTP tests from each
    other (parallel-safe) without touching the public schema.
    """

    @pytest_asyncio.fixture
    async def http_client(self, session_factory, monkeypatch):
        from httpx import ASGITransport, AsyncClient
        from sqlalchemy import event
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app import main as main_mod
        from app.database import database as db_mod

        factory, schema = session_factory
        settings = get_settings_safe()

        # Build a fresh engine that pins search_path on every
        # connection (BEGIN or otherwise).
        test_engine = create_async_engine(settings.database_url, echo=False)

        @event.listens_for(test_engine.sync_engine, "connect")
        def _set_search_path(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            try:
                cur.execute(f'SET search_path TO "{schema}"')
            finally:
                cur.close()

        # Create the tables once on the test engine / schema.
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        test_factory = async_sessionmaker(test_engine, expire_on_commit=False)
        monkeypatch.setattr(db_mod, "engine", test_engine, raising=True)
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", test_factory, raising=True)
        monkeypatch.setattr(main_mod, "engine", test_engine, raising=True)
        monkeypatch.setattr(main_mod, "AsyncSessionLocal", test_factory, raising=True)

        # Stub the provider so the lifespan handler (which would
        # otherwise spin up a real listener hitting the network)
        # stays a no-op.
        stub = StubProvider([])
        monkeypatch.setattr(main_mod, "HttpRpcProvider", lambda: stub, raising=True)
        # Short-circuit the lifespan so the listener never starts.
        monkeypatch.setattr(main_mod, "lifespan", _noop_lifespan, raising=True)

        async with AsyncClient(
            transport=ASGITransport(app=main_mod.app),
            base_url="http://test",
        ) as c:
            yield c, factory, schema

        await test_engine.dispose()

    @pytest.mark.asyncio
    async def test_get_token_by_address(self, http_client):
        client, factory, schema = http_client
        addr = "0x" + "ee" * 20
        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            session.add(ContractDeployment(
                contract_address=addr,
                deployer="0x" + "22" * 20,
                creation_tx="0x" + addr[2:].rjust(64, "0"),
                creation_block=99,
                is_erc20=True,
                erc20_checked_at=datetime.now(timezone.utc),
            ))
            session.add(Token(
                contract_address=addr,
                deployer="0x" + "22" * 20,
                name="MyToken",
                symbol="MTK",
                decimals=18,
                total_supply=1_000_000,
                creation_block=99,
                creation_timestamp=datetime.now(timezone.utc),
            ))
            await session.commit()

        # Lower-case lookup
        r = await client.get(f"/tokens/{addr.lower()}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["contract_address"] == addr.lower()
        assert body["name"] == "MyToken"
        assert body["symbol"] == "MTK"
        assert body["decimals"] == 18
        assert body["total_supply"] == "1000000"
        assert "deployment" in body
        assert body["deployment"]["is_erc20"] is True
        assert body["deployment"]["creation_tx"] == (
            "0x" + addr[2:].rjust(64, "0")
        )

        # EIP-55 / mixed-case lookup must also resolve (we
        # lower-case the path param before the DB query).
        r2 = await client.get(f"/tokens/{addr.upper()}")
        assert r2.status_code == 200
        assert r2.json()["contract_address"] == addr.lower()

    @pytest.mark.asyncio
    async def test_get_token_not_found_404(self, http_client):
        client, _, _ = http_client
        r = await client.get("/tokens/0x" + "ff" * 20)
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_get_token_invalid_address_400(self, http_client):
        client, _, _ = http_client
        r = await client.get("/tokens/not-an-address")
        assert r.status_code == 400


async def _noop_lifespan(app):
    """Lifespan replacement that skips the real listener boot.

    The HTTP tests use a stub provider; spinning up the real
    BlockListener would either hang on get_latest_block_number
    or pollute the per-test schema with listener checkpoint
    rows.
    """
    yield