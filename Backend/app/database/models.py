"""SQLAlchemy ORM models.

Phase 2 scope: only the ProcessedBlock checkpoint table, per spec sec.5
("Compare it with the last processed block stored in the database ...
Resume from the saved block after a restart.").
"""
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
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
    total_supply: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
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
