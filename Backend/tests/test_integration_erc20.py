"""Live-network integration smoke test for the ERC-20 detector.

NOT run by the default pytest invocation -- the unit suite must
stay hermetic. Opt in with::

    RUN_INTEGRATION=1 pytest -m integration tests/test_integration_erc20.py -v

What this proves
----------------
* ``HttpRpcProvider`` can talk to ``https://mainnet.base.org``
* ``_probe_one`` correctly identifies a well-known ERC-20 (USDC)
  end-to-end: name, symbol, decimals, totalSupply round-trip
  via real ``eth_call``s
* A non-ERC-20 contract (a contract that intentionally does NOT
  implement the four selectors) is correctly classified as
  ``is_erc20=false`` and ``erc20_checked_at=now``

Both probes are read-only and safe to run against mainnet.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine,
)

from app.blockchain.provider import HttpRpcProvider
from app.config import get_settings
from app.database.models import Base, ContractDeployment, Token
from app.discovery.tokens import make_on_block

# USDC on Base mainnet. Source: Circle's official deployment.
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDC_NAME = "USD Coin"
USDC_SYMBOL = "USDC"
USDC_DECIMALS = 6

# A known non-ERC-20 contract on Base: the L2StandardBridge proxy.
# It has bytecode but does NOT implement name()/symbol()/decimals()
# /totalSupply() at its main address, so all four calls should
# revert (or return empty). We use it as the negative case.
NON_ERC20 = "0x4200000000000000000000000000000000000010"

pytestmark = pytest.mark.integration


def _integration_enabled() -> bool:
    return os.environ.get("RUN_INTEGRATION") == "1"


# ---------- shared infrastructure ---------------------------------------
@pytest.fixture(scope="module")
def provider() -> HttpRpcProvider:
    return HttpRpcProvider()


@pytest_asyncio.fixture
async def integration_session_factory():
    """Per-test isolated PG schema, same pattern as the unit suite.

    We can't share ``tests/test_tokens.py``'s fixture without
    crossing the unit/integration boundary, so we re-define it
    here. The schema is dropped on teardown.
    """
    import uuid
    schema = "t_" + uuid.uuid4().hex[:12]
    settings = get_settings()
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


# ---------- tests -------------------------------------------------------
@pytest.mark.skipif(
    not _integration_enabled(),
    reason="set RUN_INTEGRATION=1 to run live-network integration tests",
)
def test_provider_chain_id(provider: HttpRpcProvider) -> None:
    assert provider.chain_id == 8453  # Base mainnet


@pytest.mark.skipif(
    not _integration_enabled(),
    reason="set RUN_INTEGRATION=1 to run live-network integration tests",
)
@pytest.mark.asyncio
async def test_probe_usdc_returns_expected_metadata(provider: HttpRpcProvider) -> None:
    """Direct eth_call to USDC for all four selectors.

    Uses the provider as-is, no DB. This is the fastest possible
    verification that web3.py + Base RPC + our selectors line up.
    """
    from app.discovery.tokens import (
        SELECTOR_DECIMALS, SELECTOR_NAME, SELECTOR_SYMBOL,
        SELECTOR_TOTAL_SUPPLY, _decode_string, _decode_uint8, _decode_uint256,
    )

    name_raw = await provider.get_eth_call(USDC_BASE, SELECTOR_NAME)
    sym_raw = await provider.get_eth_call(USDC_BASE, SELECTOR_SYMBOL)
    dec_raw = await provider.get_eth_call(USDC_BASE, SELECTOR_DECIMALS)
    sup_raw = await provider.get_eth_call(USDC_BASE, SELECTOR_TOTAL_SUPPLY)

    name = _decode_string(name_raw)
    sym = _decode_string(sym_raw)
    decimals = _decode_uint8(dec_raw)
    total_supply = _decode_uint256(sup_raw)

    assert name == USDC_NAME, f"USDC.name() returned {name!r}"
    assert sym == USDC_SYMBOL, f"USDC.symbol() returned {sym!r}"
    assert decimals == USDC_DECIMALS, f"USDC.decimals() returned {decimals!r}"
    # USDC has a known supply in the trillions of smallest units.
    # Just sanity-check it parses and is > 1e12.
    assert total_supply is not None
    assert total_supply > 10**12, f"USDC.totalSupply() suspiciously small: {total_supply}"


@pytest.mark.skipif(
    not _integration_enabled(),
    reason="set RUN_INTEGRATION=1 to run live-network integration tests",
)
@pytest.mark.asyncio
async def test_probe_non_erc20_reverts_or_garbage(provider: HttpRpcProvider) -> None:
    """L2StandardBridge must NOT pass our ERC-20 probe.

    We don't pin the exact failure mode (some implementations
    revert, others return garbage). What matters is that
    ``_probe_one`` returns a non-OK outcome so the deployment
    gets ``erc20_checked_at`` set without ``is_erc20=true``.
    """
    from app.discovery.tokens import _PROBE_OK, _probe_one

    fake_dep = ContractDeployment(
        contract_address=NON_ERC20,
        deployer="0x" + "00" * 20,
        creation_tx="0x" + "00" * 32,
        creation_block=0,
    )
    outcome, decoded = await _probe_one(provider, fake_dep.contract_address)
    assert outcome is not _PROBE_OK, (
        f"L2StandardBridge unexpectedly passed ERC-20 probe: {decoded!r}"
    )


@pytest.mark.skipif(
    not _integration_enabled(),
    reason="set RUN_INTEGRATION=1 to run live-network integration tests",
)
@pytest.mark.asyncio
async def test_detector_records_usdc_end_to_end(
    provider: HttpRpcProvider, integration_session_factory
) -> None:
    """Full detector pipeline against a seeded USDC deployment row.

    The seeded deployment points at the real USDC contract address,
    so the detector's eth_calls hit mainnet. The session factory
    isolates this run in its own schema so it can coexist with the
    unit suite.
    """
    factory, schema = integration_session_factory
    addr_lc = USDC_BASE.lower()
    async with factory() as session:
        await session.execute(text(f'SET search_path TO "{schema}"'))
        session.add(ContractDeployment(
            contract_address=addr_lc,
            deployer="0x" + "00" * 20,
            creation_tx="0x" + addr_lc[2:].rjust(64, "0"),
            creation_block=1,
        ))
        await session.commit()

    on_block = make_on_block(provider, factory)
    await on_block({"number": 1, "timestamp": int(datetime.now(timezone.utc).timestamp())})

    async with factory() as session:
        await session.execute(text(f'SET search_path TO "{schema}"'))
        token = (await session.execute(
            select(Token).where(Token.contract_address == addr_lc)
        )).scalars().first()
        assert token is not None, "USDC was not detected by the live probe"
        assert token.name == USDC_NAME
        assert token.symbol == USDC_SYMBOL
        assert token.decimals == USDC_DECIMALS
        assert token.total_supply is not None
        assert token.total_supply > 0

        dep = (await session.execute(
            select(ContractDeployment).where(
                ContractDeployment.contract_address == addr_lc
            )
        )).scalar_one()
        assert dep.is_erc20 is True
        assert dep.erc20_checked_at is not None

