"""SQLAlchemy ORM models.

Phase 2 scope: only the ProcessedBlock checkpoint table, per spec sec.5
("Compare it with the last processed block stored in the database ...
Resume from the saved block after a restart.").
"""
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ProcessedBlock(Base):
    """A singleton-style checkpoint table.

    We keep a single row (id=1) holding the last block number the
    listener successfully processed. Updated transactionally after
    each block so a crash mid-gap only reprocesses from the last
    persisted block.
    """

    __tablename__ = "processed_block"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    block_hash: Mapped[str | None] = mapped_column(nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class ContractDeployment(Base):
    """A contract creation observed on-chain (spec sec.6).

    We register every ``to == None`` transaction whose receipt has a
    non-null ``contractAddress``. We do NOT yet know whether the
    contract is an ERC-20 — that check is Phase 4 and lives in a
    separate column (``is_erc20``) populated by the ERC-20 detector.

    Uniqueness on ``contract_address`` is the source of truth for
    "have we seen this deployment before?", and gives the
    ERC-20 detector an O(1) lookup on restart.

    Phase 4 adds ``erc20_checked_at``: the timestamp of the last
    ERC-20 probe attempt. We set it whether the probe succeeded or
    failed, so a contract that reverts on ``name()`` is not
    re-queried every block forever. NULL = never probed.
    """

    __tablename__ = "contract_deployments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_address: Mapped[str] = mapped_column(
        String(42), unique=True, index=True, nullable=False
    )
    deployer: Mapped[str] = mapped_column(
        String(42), index=True, nullable=False
    )
    creation_tx: Mapped[str] = mapped_column(
        String(66), unique=True, nullable=False
    )
    creation_block: Mapped[int] = mapped_column(
        BigInteger, index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    is_erc20: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )  # Phase 4 will flip this
    erc20_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Token(Base):
    """A confirmed ERC-20 token (spec sec.7).

    Inserted by the Phase 4 detector after a successful
    ``eth_call(name), eth_call(symbol), eth_call(decimals),
    eth_call(totalSupply)`` round-trip. FK to
    ``contract_deployments`` so the deployer / creation_block /
    creation_tx of the underlying contract is always one join away.

    name / symbol use Text because some projects set very long
    strings; the detector layer caps them at 256 chars to keep row
    size sane.
    """

    __tablename__ = "tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    contract_address: Mapped[str] = mapped_column(
        String(42),
        ForeignKey("contract_deployments.contract_address", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    deployer: Mapped[str] = mapped_column(
        String(42), index=True, nullable=False
    )
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    symbol: Mapped[str | None] = mapped_column(Text, nullable=True)
    decimals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # ``Numeric(78)`` is uint256-sized: max value is 1.16e77, which
    # is the full EVM uint256 range. We deliberately do NOT use
    # BigInteger (int8, max 9.2e18) because many real ERC-20s
    # (meme coins, rebasing tokens) issue >1e18 in smallest units.
    # SQLAlchemy Decimal on the wire, ``int`` in Python.
    total_supply: Mapped[int | None] = mapped_column(
        Numeric(78, 0), nullable=True
    )  # raw integer in smallest unit
    creation_block: Mapped[int] = mapped_column(
        BigInteger, index=True, nullable=False
    )
    creation_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    # Phase 5: timestamp of the last liquidity-pool discovery sweep
    # for this token. Same NULL-means-unprobed convention as
    # ``ContractDeployment.erc20_checked_at`` in Phase 4: set on
    # both "found a pool" and "checked, found nothing" so we don't
    # re-query every block forever; left NULL on transport errors
    # so the sweep retries on a later tick.
    liquidity_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class LiquidityPool(Base):
    """A discovered liquidity pool for a token (spec sec.8).

    Populated by the Phase 5 detector, which probes the well-known
    Base DEX factories (Uniswap V2, Uniswap V3) for a pool pairing
    each confirmed ERC-20 against a short list of major paired
    assets (WETH, USDC). One token can have several rows here --
    e.g. a V2 pool against WETH *and* a V3 0.3% pool against USDC.

    Reserves are only populated for V2-style pools, where
    ``getReserves()`` gives a direct, unambiguous snapshot. V3 pools
    use concentrated liquidity (a single reserve number is
    meaningless without the active tick range), so ``reserve_token``
    / ``reserve_pair`` stay NULL for V3 rows -- depth analysis for
    V3 is deferred to Phase 9 (Liquidity Monitoring) / Phase 10.
    """

    __tablename__ = "liquidity_pools"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_address: Mapped[str] = mapped_column(
        String(42),
        ForeignKey("tokens.contract_address", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    pool_address: Mapped[str] = mapped_column(
        String(42), unique=True, index=True, nullable=False
    )
    dex: Mapped[str] = mapped_column(String(32), nullable=False)  # "uniswap_v2" | "uniswap_v3"
    pair_asset: Mapped[str] = mapped_column(String(42), nullable=False)
    # Uniswap V3 fee tier in hundredths of a bip (500/3000/10000).
    # NULL for V2 pools, which have a single fixed 0.3% fee baked
    # into the pair contract rather than a per-pool parameter.
    fee_tier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Raw on-chain reserves at discovery time, smallest-unit integers
    # (same Numeric(78,0) rationale as Token.total_supply -- must
    # hold the full uint112/uint256 range). V2 only; see class
    # docstring.
    reserve_token: Mapped[int | None] = mapped_column(Numeric(78, 0), nullable=True)
    reserve_pair: Mapped[int | None] = mapped_column(Numeric(78, 0), nullable=True)
    discovered_block: Mapped[int] = mapped_column(BigInteger, nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
