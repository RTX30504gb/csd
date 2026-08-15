"""Unit tests for the contract-deployment detector.

These tests run against the real Postgres container (``rug-postgres``)
because the detector uses ``sqlalchemy.dialects.postgresql.insert``'s
``ON CONFLICT DO NOTHING`` which is dialect-specific and cannot be
faithfully exercised under SQLite.

Isolation strategy: per-test PG schema. Each test creates a fresh
schema, runs against it, and drops it on teardown. This keeps tests
independent without poisoning the public ``contract_deployments``
table that the smoke test (and eventually the live listener) uses.

Prereqs:
    docker compose up -d       # from the repo root

Run:
    cd backend
    pytest tests/test_deployments.py -v
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings
from app.database.models import Base, ContractDeployment
from app.discovery.deployments import make_on_block


# ---------- stub provider ---------------------------------------------------
class StubProvider:
    """Returns canned blocks and receipts. No network.

    By default the stub simulates a block with 3 txs:
        tx0: contract deployment  -> contract A
        tx1: normal ETH transfer  -> ignored
        tx2: contract deployment  -> contract B (deployer same as A)
    """

    chain_id = 8453

    def __init__(self) -> None:
        self.receipt_calls: list[str] = []
        self.full_block_calls: list[int] = []

    async def get_latest_block_number(self) -> int:
        return 100

    async def get_block(self, block_number: int, full_transactions: bool = False) -> dict:
        if full_transactions:
            self.full_block_calls.append(block_number)
            return {
                "number": block_number,
                "hash": f"0x{block_number:064x}",
                "timestamp": 1_700_000_000,
                "transactions": [
                    {
                        "hash": "0x" + "aa" * 32,
                        "from": "0x" + "11" * 20,
                        "to": None,  # deployment
                        "value": 0,
                        "nonce": 0,
                        "input": "0x6080",
                    },
                    {
                        "hash": "0x" + "bb" * 32,
                        "from": "0x" + "22" * 20,
                        "to": "0x" + "33" * 20,  # normal transfer
                        "value": 1,
                        "nonce": 0,
                        "input": "0x",
                    },
                    {
                        "hash": "0x" + "cc" * 32,
                        "from": "0x" + "11" * 20,  # same deployer as tx0
                        "to": None,  # deployment
                        "value": 0,
                        "nonce": 1,
                        "input": "0x6080",
                    },
                ],
            }
        return {
            "number": block_number,
            "hash": f"0x{block_number:064x}",
            "timestamp": 1_700_000_000,
            "transactions": [
                "0x" + "aa" * 32,
                "0x" + "bb" * 32,
                "0x" + "cc" * 32,
            ],
        }

    async def get_transaction_receipt(self, tx_hash: str) -> dict:
        self.receipt_calls.append(tx_hash)
        if tx_hash == "0x" + "aa" * 32:
            return _receipt(tx_hash, "0x" + "11" * 20, "0x" + "AA" * 20, 100, 1)
        if tx_hash == "0x" + "bb" * 32:
            return _receipt(tx_hash, "0x" + "22" * 20, None, 21000, 1)
        if tx_hash == "0x" + "cc" * 32:
            return _receipt(tx_hash, "0x" + "11" * 20, "0x" + "BB" * 20, 200, 1)
        raise AssertionError(f"unexpected receipt request: {tx_hash}")


def _receipt(tx_hash: str, frm: str, contract: str | None, gas: int, status: int) -> dict:
    return {
        "transactionHash": tx_hash,
        "blockNumber": 100,
        "from": frm,
        "to": None,
        "contractAddress": contract,
        "gasUsed": gas,
        "status": status,
        "logs": [],
    }


# ---------- fixtures --------------------------------------------------------
def _admin_url() -> str:
    """Database URL pointing at the default 'rug' database for DDL."""
    return get_settings().database_url


def _schema_name() -> str:
    return "t_" + uuid.uuid4().hex[:12]


@pytest_asyncio.fixture
async def session_factory() -> Any:
    """Spin up a fresh PG schema, yield a session factory, drop the schema."""
    schema = _schema_name()
    admin_url = _admin_url()
    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")

    async with admin_engine.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))

    # Per-schema engine: set search_path so all CREATE/SELECT on
    # ContractDeployment land in our isolated schema, not public.
    schema_engine = create_async_engine(
        admin_url,
        connect_args={"server_settings": {"search_path": schema}},
    )

    async with schema_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(schema_engine, class_=AsyncSession, expire_on_commit=False)

    try:
        yield factory
    finally:
        await schema_engine.dispose()
        async with admin_engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin_engine.dispose()


# ---------- tests -----------------------------------------------------------
@pytest.mark.asyncio
async def test_deployments_records_only_creations(session_factory) -> None:
    provider = StubProvider()
    on_block = make_on_block(provider=provider, session_factory=session_factory)
    block = await provider.get_block(100, full_transactions=True)
    await on_block(block)

    # receipt was requested for the 2 candidates, NOT the normal transfer
    assert sorted(provider.receipt_calls) == sorted(
        ["0x" + "aa" * 32, "0x" + "cc" * 32]
    ), provider.receipt_calls

    async with session_factory() as session:
        rows = (
            await session.execute(ContractDeployment.__table__.select())
        ).fetchall()
    assert len(rows) == 2
    addrs = {r.contract_address for r in rows}
    # addresses stored lower-case
    assert "0x" + "aa" * 20 in addrs
    assert "0x" + "bb" * 20 in addrs
    # both rows attributed to the same deployer
    assert all(r.deployer == "0x" + "11" * 20 for r in rows)
    # same block
    assert {r.creation_block for r in rows} == {100}


@pytest.mark.asyncio
async def test_deployments_idempotent_on_replay(session_factory) -> None:
    provider = StubProvider()
    on_block = make_on_block(provider=provider, session_factory=session_factory)
    block = await provider.get_block(100, full_transactions=True)
    await on_block(block)
    await on_block(block)  # same block, simulate listener replay

    async with session_factory() as session:
        count = (
            await session.execute(
                select(func.count()).select_from(ContractDeployment)
            )
        ).scalar_one()
    assert count == 2, "replaying the same block must not duplicate rows"


@pytest.mark.asyncio
async def test_deployments_skips_failed_tx(session_factory) -> None:
    """A deployment whose receipt has status=0 must not be stored."""
    provider = StubProvider()

    # override: make tx "aa" a reverted deployment (no contractAddress)
    async def bad_receipt(tx_hash: str) -> dict:
        if tx_hash == "0x" + "aa" * 32:
            return _receipt(tx_hash, "0x" + "11" * 20, None, 100, 0)
        return await StubProvider.get_transaction_receipt(provider, tx_hash)

    provider.get_transaction_receipt = bad_receipt  # type: ignore[method-assign]

    on_block = make_on_block(provider=provider, session_factory=session_factory)
    block = await provider.get_block(100, full_transactions=True)
    await on_block(block)

    async with session_factory() as session:
        rows = (
            await session.execute(ContractDeployment.__table__.select())
        ).fetchall()
    assert len(rows) == 1
    assert rows[0].contract_address == "0x" + "bb" * 20
    async with session_factory() as session:
        count = (
            await session.execute(
                select(func.count()).select_from(ContractDeployment)
            )
        ).scalar_one()
    assert count == 1, "failed deployment must not be stored"
    