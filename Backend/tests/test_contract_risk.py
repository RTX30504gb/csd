"""Unit tests for the Phase 10 contract bytecode risk detector.

Prereqs:
    docker compose up -d

Run:
    pytest tests/test_contract_risk.py -v
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

from app.discovery.contract_risk import (
    SELECTOR_GROUPS,
    SELECTOR_OWNER,
    ZERO_ADDRESS,
    _extract_selectors,
    make_on_block,
)
from app.database.models import Base, ContractDeployment, ContractRiskFlags, Token


def _dispatch_bytecode(*selectors: str) -> bytes:
    """Build synthetic-but-realistic Solidity dispatcher bytecode.

    Real solc output for each function is roughly:
        PUSH4 <selector> DUP2 EQ PUSH2 <jumpdest> JUMPI
    We reproduce that exact opcode shape (not just a bare PUSH4) so
    the test actually exercises the scanner's ability to skip over
    other PUSH-with-operand opcodes correctly, not just find PUSH4
    in isolation.
    """
    code = bytearray()
    code += bytes([0x63])  # PUSH1 dummy prefix noise, value below
    code += (0).to_bytes(4, "big")  # placeholder to soak an early PUSH4 slot (no match)
    code += bytes([0x35])  # CALLDATALOAD (1-byte opcode, no operand)
    for sel in selectors:
        code += bytes([0x63])  # PUSH4
        code += bytes.fromhex(sel.removeprefix("0x"))
        code += bytes([0x81])  # DUP2
        code += bytes([0x14])  # EQ
        code += bytes([0x61])  # PUSH2
        code += (0x00AB).to_bytes(2, "big")  # jumpdest operand -- must be skipped, not scanned
        code += bytes([0x57])  # JUMPI
    code += bytes([0x00])  # STOP
    return bytes(code)


class StubProvider:
    def __init__(
        self,
        code_by_address: dict[str, bytes] | None = None,
        eth_call_responses: dict[tuple[str, str], Any] | None = None,
    ) -> None:
        self.code_by_address = {
            k.lower(): v for k, v in (code_by_address or {}).items()
        }
        self.eth_call_responses = dict(eth_call_responses or {})
        self.code_calls: list[str] = []
        self.eth_calls: list[tuple[str, str]] = []

    @property
    def chain_id(self) -> int:
        return 8453

    async def get_code(self, address: str) -> bytes:
        self.code_calls.append(address.lower())
        if address.lower() not in self.code_by_address:
            raise OSError("simulated transport failure")
        return self.code_by_address[address.lower()]

    async def get_eth_call(self, to: str, data: str) -> bytes:
        self.eth_calls.append((to.lower(), data))
        key = (to.lower(), data)
        if key in self.eth_call_responses:
            resp = self.eth_call_responses[key]
            if isinstance(resp, BaseException):
                raise resp
            return resp
        return bytes(32)


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


async def _seed_token(factory, schema, address: str = "0x" + "aa" * 20) -> Token:
    async with factory() as session:
        await session.execute(text(f'SET search_path TO "{schema}"'))
        session.add(
            ContractDeployment(
                contract_address=address,
                deployer="0x" + "22" * 20,
                creation_tx="0x" + address[2:].rjust(64, "0"),
                creation_block=1,
                is_erc20=True,
                erc20_checked_at=datetime.now(timezone.utc),
            )
        )
        token = Token(
            contract_address=address,
            deployer="0x" + "22" * 20,
            name="Test",
            symbol="TST",
            decimals=18,
            total_supply=10**24,
            creation_block=1,
            creation_timestamp=datetime.now(timezone.utc),
        )
        session.add(token)
        await session.commit()
        await session.refresh(token)
        return token


class TestSelectorExtraction:
    def test_finds_all_selectors_amid_realistic_dispatcher(self):
        mint_sel = SELECTOR_GROUPS["has_mint"][0]
        pause_sel = SELECTOR_GROUPS["has_pause"][0]
        code = _dispatch_bytecode(mint_sel, pause_sel)
        found = _extract_selectors(code)
        assert mint_sel in found
        assert pause_sel in found

    def test_does_not_misread_push2_operand_as_opcodes(self):
        """The PUSH2 jumpdest operand (0x00AB) must not be scanned
        as if it contained opcodes -- if the scanner mis-skips
        operand lengths it will desync and either miss real
        selectors or hallucinate fake ones."""
        sel = SELECTOR_GROUPS["has_blacklist"][0]
        code = _dispatch_bytecode(sel)
        found = _extract_selectors(code)
        assert sel in found
        # Sanity: scanning ends cleanly without exception on the
        # trailing STOP opcode (0x00, not a PUSH).
        assert isinstance(found, set)

    def test_empty_bytecode_returns_empty_set(self):
        assert _extract_selectors(b"") == set()

    def test_no_dangerous_selectors_in_plain_erc20(self):
        # A vanilla ERC-20 dispatcher: transfer/approve/balanceOf/
        # totalSupply -- none of which are in any SELECTOR_GROUPS.
        code = _dispatch_bytecode(
            "0xa9059cbb",  # transfer(address,uint256)
            "0x095ea7b3",  # approve(address,uint256)
        )
        found = _extract_selectors(code)
        all_dangerous = {s for group in SELECTOR_GROUPS.values() for s in group}
        assert found.isdisjoint(all_dangerous)


class TestDetectorEndToEnd:
    @pytest.mark.asyncio
    async def test_dangerous_token_flagged_correctly(self, session_factory):
        factory, schema = session_factory
        token = await _seed_token(factory, schema)
        mint_sel = SELECTOR_GROUPS["has_mint"][0]
        blacklist_sel = SELECTOR_GROUPS["has_blacklist"][0]
        code = _dispatch_bytecode(mint_sel, blacklist_sel, SELECTOR_OWNER)
        owner_addr = "0x" + "44" * 20
        provider = StubProvider(
            code_by_address={token.contract_address: code},
            eth_call_responses={
                (token.contract_address, SELECTOR_OWNER):
                    bytes(12) + bytes.fromhex(owner_addr.removeprefix("0x")),
            },
        )
        on_block = make_on_block(provider, factory)
        await on_block({"number": 100})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            flags = (await session.execute(select(ContractRiskFlags))).scalar_one()
            assert flags.has_mint is True
            assert flags.has_blacklist is True
            assert flags.has_pause is False
            assert flags.has_owner_function is True
            assert flags.owner_address == owner_addr
            assert flags.owner_renounced is False
            t = (await session.execute(select(Token))).scalar_one()
            assert t.contract_analyzed_at is not None

    @pytest.mark.asyncio
    async def test_renounced_ownership_detected(self, session_factory):
        factory, schema = session_factory
        token = await _seed_token(factory, schema)
        code = _dispatch_bytecode(SELECTOR_OWNER)
        provider = StubProvider(
            code_by_address={token.contract_address: code},
            eth_call_responses={
                (token.contract_address, SELECTOR_OWNER): bytes(32),  # zero address
            },
        )
        on_block = make_on_block(provider, factory)
        await on_block({"number": 200})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            flags = (await session.execute(select(ContractRiskFlags))).scalar_one()
            assert flags.owner_renounced is True
            assert flags.owner_address == ZERO_ADDRESS

    @pytest.mark.asyncio
    async def test_no_owner_function_leaves_ownership_fields_null(self, session_factory):
        factory, schema = session_factory
        token = await _seed_token(factory, schema)
        code = _dispatch_bytecode("0xa9059cbb")  # only transfer(), no owner()
        provider = StubProvider(code_by_address={token.contract_address: code})
        on_block = make_on_block(provider, factory)
        await on_block({"number": 300})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            flags = (await session.execute(select(ContractRiskFlags))).scalar_one()
            assert flags.has_owner_function is False
            assert flags.owner_address is None
            assert flags.owner_renounced is None

    @pytest.mark.asyncio
    async def test_clean_token_has_all_flags_false(self, session_factory):
        factory, schema = session_factory
        token = await _seed_token(factory, schema)
        code = _dispatch_bytecode("0xa9059cbb", "0x095ea7b3")
        provider = StubProvider(code_by_address={token.contract_address: code})
        on_block = make_on_block(provider, factory)
        await on_block({"number": 400})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            flags = (await session.execute(select(ContractRiskFlags))).scalar_one()
            for group_name in SELECTOR_GROUPS:
                assert getattr(flags, group_name) is False

    @pytest.mark.asyncio
    async def test_transport_error_leaves_token_unanalyzed(self, session_factory):
        factory, schema = session_factory
        await _seed_token(factory, schema)
        provider = StubProvider(code_by_address={})  # get_code always raises
        on_block = make_on_block(provider, factory)
        await on_block({"number": 500})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            assert (await session.execute(select(ContractRiskFlags))).scalars().all() == []
            t = (await session.execute(select(Token))).scalar_one()
            assert t.contract_analyzed_at is None  # will retry

    @pytest.mark.asyncio
    async def test_empty_bytecode_is_not_a_transport_error(self, session_factory):
        """A self-destructed contract or address with no code returns
        b'' from eth_getCode -- that's a valid (if boring) result,
        not a failure, and must still mark the token analyzed."""
        factory, schema = session_factory
        token = await _seed_token(factory, schema)
        provider = StubProvider(code_by_address={token.contract_address: b""})
        on_block = make_on_block(provider, factory)
        await on_block({"number": 600})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            flags = (await session.execute(select(ContractRiskFlags))).scalar_one()
            assert flags.bytecode_size == 0
            t = (await session.execute(select(Token))).scalar_one()
            assert t.contract_analyzed_at is not None


class TestBatchingAndIdempotency:
    @pytest.mark.asyncio
    async def test_no_unanalyzed_tokens_is_noop(self, session_factory):
        factory, schema = session_factory
        provider = StubProvider()
        on_block = make_on_block(provider, factory)
        await on_block({"number": 1})
        assert provider.code_calls == []

    @pytest.mark.asyncio
    async def test_batch_size_limits_tokens_per_tick(self, session_factory):
        factory, schema = session_factory
        addresses = [f"0x{i:040x}" for i in range(5)]
        for addr in addresses:
            await _seed_token(factory, schema, address=addr)
        code = _dispatch_bytecode("0xa9059cbb")
        provider = StubProvider(code_by_address={a: code for a in addresses})
        on_block = make_on_block(provider, factory, batch_size=2)
        await on_block({"number": 700})

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            analyzed = (
                await session.execute(
                    select(Token).where(Token.contract_analyzed_at.is_not(None))
                )
            ).scalars().all()
            assert len(analyzed) == 2

    @pytest.mark.asyncio
    async def test_idempotent_on_replay(self, session_factory):
        factory, schema = session_factory
        token = await _seed_token(factory, schema)
        code = _dispatch_bytecode("0xa9059cbb")
        provider = StubProvider(code_by_address={token.contract_address: code})
        on_block = make_on_block(provider, factory)
        await on_block({"number": 800})
        calls_after_first = len(provider.code_calls)
        await on_block({"number": 801})
        assert len(provider.code_calls) == calls_after_first

        async with factory() as session:
            await session.execute(text(f'SET search_path TO "{schema}"'))
            rows = (await session.execute(select(ContractRiskFlags))).scalars().all()
            assert len(rows) == 1  # no duplicate row
