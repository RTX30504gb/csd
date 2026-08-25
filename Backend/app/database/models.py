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
    # Phase 10: timestamp of the last contract-bytecode risk scan.
    # Same NULL-means-unprobed convention as the other detectors.
    contract_analyzed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class LiquidityPool(Base):
    """A discovered liquidity pool for a token (spec sec.8).

    Populated by the Phase 5 detector, which probes the well-known
    Base DEX factories (Uniswap V2, Uniswap V3) for a pool pairing
    each confirmed ERC-20 against a short list of major paired
    assets (WETH, USDC). One token can have several rows here --
    e.g. a V2 pool against WETH *and* a V3 0.3% pool against USDC.

    Reserves are populated immediately for V2-style pools, where
    ``getReserves()`` gives a direct, unambiguous snapshot. V3 pools
    use concentrated liquidity (a single reserve number is
    meaningless without the active tick range), so at discovery time
    ``reserve_token`` / ``reserve_pair`` stay NULL for V3 rows; the
    Phase 6 monitor later populates ``reserve_token`` for V3 with the
    pool's raw ``liquidity()`` value instead (see field docstring
    below) as a withdrawal-detection proxy, not a true reserve.
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
    # True if our token is token0 in the pair (V2 only; NULL for
    # V3, which has no analogous reserve orientation). Uniswap V2
    # pairs are immutable once deployed, so this is captured once
    # at discovery and reused by the Phase 6 monitor on every
    # resync instead of re-querying token0() every tick.
    is_token0: Mapped[bool | None] = mapped_column(nullable=True)
    # Raw on-chain magnitude, smallest-unit integers (same
    # Numeric(78,0) rationale as Token.total_supply -- must hold the
    # full uint112/uint128/uint256 range).
    #   V2: reserve_token / reserve_pair are the pair's two reserves,
    #       oriented via is_token0. Both populated from discovery
    #       onward and refreshed by the Phase 6 monitor.
    #   V3: reserve_token holds the pool's current active
    #       ``liquidity()`` value (NOT a token amount -- concentrated
    #       liquidity has no single reserve figure, but the raw
    #       liquidity value is still a valid magnitude to track for
    #       withdrawal detection). reserve_pair is unused (NULL) for
    #       V3 since there is no second leg to this metric.
    #       NULL for both until the Phase 6 monitor's first sync.
    reserve_token: Mapped[int | None] = mapped_column(Numeric(78, 0), nullable=True)
    reserve_pair: Mapped[int | None] = mapped_column(Numeric(78, 0), nullable=True)
    discovered_block: Mapped[int] = mapped_column(BigInteger, nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    # Phase 6: last time the monitor re-checked this pool's
    # reserves/liquidity. NULL means "never synced since discovery"
    # -- for V2 that's not quite true (discovery itself fetches an
    # initial reserve snapshot) so the Phase 6 detector treats
    # discovery as sync #0 and sets this at insert time; see
    # ``liquidity.py``.
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class LiquidityEvent(Base):
    """A significant liquidity change detected for a pool (spec sec.9).

    Emitted by the Phase 6 monitor when a pool's tracked liquidity
    metric (V2: reserve_token; V3: ``liquidity()``) moves by more
    than the configured threshold between two syncs.

    This table intentionally stores only the *what changed*
    observation -- percent moved, before/after, which block. Per
    spec sec.9 ("do not automatically classify every withdrawal as
    a rug"), the *investigation* (who removed it, is that wallet the
    deployer, did price collapse, was it expected) is explicitly out
    of scope here: it needs event-log/wallet analysis the current
    ``BlockchainProvider`` interface doesn't expose (no ``get_logs``
    yet), and belongs in a later scoring phase that consumes these
    events rather than in the detector that produces them.
    """

    __tablename__ = "liquidity_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pool_address: Mapped[str] = mapped_column(
        String(42),
        ForeignKey("liquidity_pools.pool_address", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)  # "withdrawal" | "addition"
    metric: Mapped[str] = mapped_column(String(16), nullable=False)  # "reserve_token" | "v3_liquidity"
    value_before: Mapped[int] = mapped_column(Numeric(78, 0), nullable=False)
    value_after: Mapped[int] = mapped_column(Numeric(78, 0), nullable=False)
    percent_change: Mapped[float] = mapped_column(nullable=False)  # negative = decrease
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class ContractRiskFlags(Base):
    """Bytecode-level capability flags for a token contract (spec sec.10/11).

    Populated by the Phase 10 detector, which fetches the contract's
    runtime bytecode via ``eth_getCode`` and checks for the presence
    of known dangerous function selectors (mint, blacklist, pause,
    tax/fee setters, wallet/tx limit setters, proxy upgrade hooks) --
    the same first-pass heuristic used by tools like GoPlus Security
    and TokenSniffer before falling back to verified-source review.

    IMPORTANT -- what this table does NOT tell you:
      - Presence of a selector means the function exists in the
        dispatch table, not that it is reachable, unrestricted, or
        dangerous in practice (e.g. a ``mint`` gated behind a burned
        multisig with a hard-coded supply cap is low-risk despite
        ``has_mint=True``).
      - It cannot determine whether a numeric parameter has a
        hard-coded cap (spec's "5% max tax" vs "unlimited tax")
        without full bytecode data-flow analysis or verified source,
        neither of which this detector does. That level of nuance is
        deferred to whatever consumes these flags (Phase 11 feature
        engineering / manual review), which should treat "flag is
        True" as "worth a closer look", not "confirmed dangerous".
      - It cannot see through a proxy: if the token address is an
        ERC-1967/UUPS proxy, this scans the *proxy's* bytecode. A
        proxy's own code always contains ``upgradeTo``-style
        selectors delegatecall hands off to, which is itself a
        meaningful signal (upgradeable = mutable logic) but the
        *implementation* contract's own risk flags are not resolved
        here.
    """

    __tablename__ = "contract_risk_flags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token_address: Mapped[str] = mapped_column(
        String(42),
        ForeignKey("tokens.contract_address", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    has_mint: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_blacklist: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_pause: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_tax_control: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_max_tx_control: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_max_wallet_control: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_fee_exclusion_control: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_trading_control: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_upgradeable_proxy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Ownership. NULL for both means no owner()/Ownable pattern was
    # detected at all (not "renounced" -- genuinely not applicable).
    has_owner_function: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    owner_address: Mapped[str | None] = mapped_column(String(42), nullable=True)
    owner_renounced: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Raw selectors found, comma-joined hex, for auditability -- lets
    # a human check the detector's work without re-fetching bytecode.
    selectors_found: Mapped[str] = mapped_column(Text, nullable=False, default="")
    bytecode_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    analyzed_block: Mapped[int] = mapped_column(BigInteger, nullable=False)
    analyzed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
