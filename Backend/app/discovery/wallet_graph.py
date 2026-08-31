"""Wallet relationship graph builder (spec sec.15).

Goal
----
Detect clusters of wallets that may be controlled by the same actor
("shared funding sources, transfers between wallets, coordinated
purchases, common deployers ..."). This module does NOT run a graph-ML
algorithm -- it builds a small, explicit edge list and exposes it. A
later phase (or a human reviewer) walks the adjacency.

Scope and honesty notes
-----------------------
What we actually build from the data already in the DB, plus a single
new RPC capability (``eth_getLogs`` on the token contract over a
bounded block range):

  1. ``funds_token``        deployer -> token contract
                             (always present once a token is analyzed;
                             anchor edge for cluster expansion)
  2. ``co_deployed``        token A -> token B when both share a
                             deployer (i.e. deployer's busy)
  3. ``operates_pool``      token -> its liquidity pool
  4. ``transfer_recipient`` token -> address seen as ``to`` of an
                             ERC-20 Transfer event in the window

What this module deliberately does NOT do:

  - ``shared funding source`` analysis (spec sec.15 bullet 1).
    Following the funding chain requires ``eth_getTransactionByHash``
    recursively for every deployer (potentially N levels deep), which
    is unbounded work and out of scope for a tick-bounded callback.
    This belongs to a later "deep wallet intelligence" phase.
  - Graph ML, label propagation, cluster inference. The graph is the
    deliverable; clustering is downstream.
  - "Operates" classification for pool addresses. We don't trace
    LP-token holders from the pool, so ``operates_pool`` means
    "the pool's address appears alongside this token" rather than
    "this wallet controls the pool's LP".
  - A heuristic ``label in {EOA, contract, pool, router, ...}`` on
    Wallet. The counters (``tokens_deployed``, ``tokens_as_pool``,
    ``tokens_as_transfer``) make that inference possible from the
    data alone without committing to a brittle label here.

Algorithm
---------
1. On every block, select confirmed tokens with
   ``wallet_graph_analyzed_at IS NULL``, ordered by id, capped at
   ``batch_size`` (same batching pattern as the Phase 5/6/10 detectors
   so a single tick stays bounded against the public RPC's rate limit).
2. For each token:
     a. ``funds_token``         -- insert edge deployer -> token (weight=1)
     b. ``co_deployed``         -- find all *other* tokens by the same
                                  deployer; insert edge token -> peer
                                  (weight = # of peer's other tokens)
     c. ``operates_pool``       -- for each known liquidity_pool, insert
                                  edge token -> pool (weight=1)
     d. ``transfer_recipient``  -- call ``provider.get_logs`` for the
                                  Transfer event topic across
                                  ``[creation_block, current_block]``
                                  (chunked to stay under the RPC's
                                  range cap); accumulate ``to``
                                  addresses by frequency, insert the
                                  top ``top_n_recipients`` edges
                                  (weight = distinct transfer count)
3. Update ``wallets`` counters / first_seen / last_seen for every
   address that appeared as a node above. Uses an UPSERT so a wallet
   seen in two consecutive analyses doesn't double-count.
4. Set ``tokens.wallet_graph_analyzed_at`` and commit.

Idempotency / failure semantics
-------------------------------
  - ``wallet_relationships`` is unique on ``(a, b, kind)`` so a replay
    of an already-analyzed token is a no-op for edges it already
    inserted, and ``weight`` is updated via ``ON CONFLICT ... DO UPDATE``
    using ``GREATEST(excluded.weight, wallet_relationships.weight)``
    (a higher weight seen on a later run wins; the lower weight is not
    a regression).
  - Transport error on ``get_logs`` for a single token -> leave the
    token's ``wallet_graph_analyzed_at`` NULL so the next tick retries
    the whole token. The (a), (b), (c) edges above are NOT inserted
    in the same transaction, so a partial failure can't leave a
    token marked analyzed with no edges.

A note on what "Transfer event" means here: we filter on the
canonical ERC-20 ``Transfer(address,address,uint256)`` topic
``keccak256("Transfer(address,address,uint256)")``. The ``to`` field
of the event is decoded from topic[2] -- the standard event layout,
no ABI-decoding of ``data`` required to recover the recipient.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.blockchain.provider import BlockchainProvider
from app.database.models import (
    LiquidityPool,
    Token,
    Wallet,
    WalletRelationship,
)

logger = logging.getLogger(__name__)

# keccak256("Transfer(address,address,uint256)") -- canonical ERC-20 event topic.
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# Max # distinct ``to`` recipients we'll surface per token. Even a
# wildly active new token has fewer than this in the first few blocks
# after launch; if it has more, we keep the top-N by frequency.
DEFAULT_TOP_N_RECIPIENTS = 50

# Max block range we ask ``eth_getLogs`` for in one call. The Base
# public RPC tolerates more; paid providers commonly cap at 10k. We
# pick 5000 as a safe middle so we don't crash on a provider swap.
DEFAULT_MAX_LOG_RANGE = 5000

# Default batch size: how many tokens we analyze per block tick.
DEFAULT_BATCH_SIZE = 10

KIND_FUNDS_TOKEN = "funds_token"
KIND_CO_DEPLOYED = "co_deployed"
KIND_OPERATES_POOL = "operates_pool"
KIND_TRANSFER_RECIPIENT = "transfer_recipient"

# Same NULL-means-unprobed convention as other detectors.
ZERO_ADDRESS = "0x" + "00" * 20


def _lower(addr: str) -> str:
    if not addr:
        return addr
    return addr.lower() if addr.startswith("0x") else "0x" + addr.lower()


def make_on_block(
    provider: BlockchainProvider,
    session_factory: async_sessionmaker[AsyncSession],
    batch_size: int = DEFAULT_BATCH_SIZE,
    top_n_recipients: int = DEFAULT_TOP_N_RECIPIENTS,
    max_log_range: int = DEFAULT_MAX_LOG_RANGE,
):
    """Return an ``on_block(block)`` callback that builds wallet edges."""

    async def on_block(block: dict) -> None:
        await process_wallet_graph(
            block,
            provider,
            session_factory,
            batch_size,
            top_n_recipients,
            max_log_range,
        )

    return on_block


async def process_wallet_graph(
    block: dict,
    provider: BlockchainProvider,
    session_factory: async_sessionmaker[AsyncSession],
    batch_size: int = DEFAULT_BATCH_SIZE,
    top_n_recipients: int = DEFAULT_TOP_N_RECIPIENTS,
    max_log_range: int = DEFAULT_MAX_LOG_RANGE,
) -> None:
    current_block = int(block["number"])
    async with session_factory() as session:
        tokens = (
            await session.execute(
                select(Token)
                .where(Token.wallet_graph_analyzed_at.is_(None))
                .order_by(Token.id.asc())
                .limit(batch_size)
            )
        ).scalars().all()
        if not tokens:
            return

        now = datetime.now(timezone.utc)
        analyzed = 0
        for token in tokens:
            try:
                await _analyze_token(
                    session=session,
                    provider=provider,
                    token=token,
                    current_block=current_block,
                    top_n_recipients=top_n_recipients,
                    max_log_range=max_log_range,
                    now=now,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "wallet-graph analysis failed mid-token %s; "
                    "partial edges (if any) kept, token unmarked",
                    token.contract_address,
                )
                await session.rollback()
                continue

            token.wallet_graph_analyzed_at = now
            analyzed += 1

        if analyzed:
            logger.info(
                "block %s: wallet-graph analysis on %d token(s)",
                current_block, analyzed,
            )
        await session.commit()


async def _analyze_token(
    *,
    session: AsyncSession,
    provider: BlockchainProvider,
    token: Token,
    current_block: int,
    top_n_recipients: int,
    max_log_range: int,
    now: datetime,
) -> None:
    """Build the four kinds of edges for a single token.

    Steps (a)-(c) run purely off the DB; step (d) issues one or more
    ``eth_getLogs`` calls. Each step commits its own upserts so a
    later step's failure doesn't undo the earlier observations.
    """
    token_addr = token.contract_address
    deployer = token.deployer

    # ---- (a) funds_token: deployer -> token ---------------------------
    await _upsert_wallet(
        session,
        address=deployer,
        block=token.creation_block,
        now=now,
        bump_deployed=1,
    )
    await _upsert_wallet(
        session,
        address=token_addr,
        block=token.creation_block,
        now=now,
        bump_transfer=1,
    )
    await _upsert_edge(
        session,
        a=deployer,
        b=token_addr,
        kind=KIND_FUNDS_TOKEN,
        weight=1,
        block=token.creation_block,
        evidence={"deployer": deployer, "token": token_addr},
        now=now,
    )

    # ---- (b) co_deployed: token -> peer token(s) by same deployer ----
    if deployer and deployer != ZERO_ADDRESS:
        peers = (
            await session.execute(
                select(Token.contract_address, Token.creation_block)
                .where(
                    Token.deployer == deployer,
                    Token.contract_address != token_addr,
                )
                .order_by(Token.creation_block.asc())
            )
        ).all()
        # ``weight`` is the deployer's total token count at the moment
        # we ran the analysis. A deployer with 5 tokens gets a higher
        # weight on every ``co_deployed`` edge than a deployer with 2.
        weight = 1 + len(peers)  # +1 for the current token
        for peer_addr, peer_block in peers:
            await _upsert_wallet(
                session,
                address=peer_addr,
                block=peer_block,
                now=now,
                bump_transfer=0,  # don't bump transfer count for peer tokens
            )
            await _upsert_edge(
                session,
                a=token_addr,
                b=peer_addr,
                kind=KIND_CO_DEPLOYED,
                weight=weight,
                block=max(token.creation_block, int(peer_block)),
                evidence={
                    "shared_deployer": deployer,
                    "deployer_total_tokens": weight,
                },
                now=now,
            )

    # ---- (c) operates_pool: token -> each of its pools ---------------
    pools = (
        await session.execute(
            select(LiquidityPool.pool_address, LiquidityPool.discovered_block)
            .where(LiquidityPool.token_address == token_addr)
        )
    ).all()
    for pool_addr, pool_block in pools:
        await _upsert_wallet(
            session,
            address=pool_addr,
            block=int(pool_block),
            now=now,
            bump_pool=1,
        )
        await _upsert_edge(
            session,
            a=token_addr,
            b=pool_addr,
            kind=KIND_OPERATES_POOL,
            weight=1,
            block=int(pool_block),
            evidence={"token": token_addr, "pool": pool_addr},
            now=now,
        )

    # ---- (d) transfer_recipient: token -> top ``to`` addresses -------
    # Chunk the log range so a token that's been around for many
    # blocks doesn't ask the RPC for an oversized window in one call.
    from_block = int(token.creation_block)
    to_block = current_block
    if to_block < from_block:
        # Shouldn't happen -- the listener only hands us blocks with
        # number >= last_processed, and tokens are analyzed after
        # creation. Guard anyway.
        return

    recipient_counts: dict[str, int] = {}
    chunk_start = from_block
    while chunk_start <= to_block:
        chunk_end = min(chunk_start + max_log_range - 1, to_block)
        try:
            logs = await provider.get_logs(
                address=token_addr,
                topics=[TRANSFER_TOPIC],
                from_block=chunk_start,
                to_block=chunk_end,
            )
        except Exception:  # noqa: BLE001
            # Propagate up so the caller can leave the token unanalyzed
            # and retry next tick. Re-raise so the partial-state
            # rollback in ``on_block`` applies.
            raise
        for log in logs:
            topics = log.get("topics") or []
            # ERC-20 Transfer: topics[0] is the event sig,
            # topics[1] is indexed ``from``, topics[2] is ``to``.
            if len(topics) < 3:
                continue
            to_addr = _addr_from_topic(topics[2])
            if to_addr is None or to_addr == ZERO_ADDRESS:
                # Mint-to-zero or malformed -- skip.
                continue
            if to_addr == token_addr:
                # Defensive: a token shouldn't appear as its own
                # recipient in a normal Transfer.
                continue
            recipient_counts[to_addr] = recipient_counts.get(to_addr, 0) + 1
        chunk_start = chunk_end + 1

    # Sort by frequency, take top-N. Stable: ties broken alphabetically
    # by address so the same input always produces the same edge list.
    top = sorted(
        recipient_counts.items(),
        key=lambda kv: (-kv[1], kv[0]),
    )[:top_n_recipients]
    for to_addr, count in top:
        await _upsert_wallet(
            session,
            address=to_addr,
            block=from_block,
            now=now,
            bump_transfer=1,
        )
        await _upsert_edge(
            session,
            a=token_addr,
            b=to_addr,
            kind=KIND_TRANSFER_RECIPIENT,
            weight=int(count),
            block=from_block,
            evidence={
                "token": token_addr,
                "window": [from_block, to_block],
                "distinct_transfers_in_window": int(count),
            },
            now=now,
        )


# --- upserts --------------------------------------------------------------
async def _upsert_wallet(
    session: AsyncSession,
    *,
    address: str,
    block: int,
    now: datetime,
    bump_deployed: int = 0,
    bump_pool: int = 0,
    bump_transfer: int = 0,
) -> None:
    """Insert a Wallet row if missing, otherwise bump counters.

    Counters are added (``tokens_deployed = tokens_deployed + EXCLUDED``)
    so a wallet that appears across multiple analyses gets a
    cumulative count, not the value from the most recent call.

    ``first_seen_block`` is preserved as the earliest block we've
    ever seen this address at (``LEAST``); ``last_seen_block`` is
    the latest (``GREATEST``). We deliberately do NOT overwrite
    ``first_seen_at`` -- the first time we observed the address
    stays the same even on later bumps.
    """
    addr = _lower(address)
    if not addr or addr == ZERO_ADDRESS:
        return
    stmt = (
        pg_insert(Wallet)
        .values(
            address=addr,
            tokens_deployed=bump_deployed,
            tokens_as_pool=bump_pool,
            tokens_as_transfer=bump_transfer,
            first_seen_block=int(block),
            last_seen_block=int(block),
            first_seen_at=now,
            last_seen_at=now,
        )
        .on_conflict_do_update(
            index_elements=["address"],
            set_={
                "tokens_deployed": Wallet.tokens_deployed + bump_deployed,
                "tokens_as_pool": Wallet.tokens_as_pool + bump_pool,
                "tokens_as_transfer": Wallet.tokens_as_transfer + bump_transfer,
                # LEAST keeps the earliest observed block; GREATEST
                # the latest. Both ignore the incoming value when the
                # existing row already has a more extreme one.
                "first_seen_block": _least(Wallet.first_seen_block, int(block)),
                "last_seen_block": _greatest(Wallet.last_seen_block, int(block)),
                "last_seen_at": now,
            },
        )
    )
    await session.execute(stmt)


async def _upsert_edge(
    session: AsyncSession,
    *,
    a: str,
    b: str,
    kind: str,
    weight: int,
    block: int,
    evidence: dict,
    now: datetime,
) -> None:
    """Insert a WalletRelationship if missing, else bump weight.

    ``weight`` takes the max of the stored and incoming values -- a
    later, more-informed run shouldn't lose information to an
    earlier noisy one. ``evidence_json`` is only written on insert
    so a later run doesn't silently overwrite the original evidence
    with less context.
    """
    a_low = _lower(a)
    b_low = _lower(b)
    if not a_low or not b_low or a_low == b_low:
        return
    stmt = (
        pg_insert(WalletRelationship)
        .values(
            a=a_low,
            b=b_low,
            kind=kind,
            weight=int(weight),
            first_seen_block=int(block),
            last_seen_block=int(block),
            evidence_json=evidence,
            created_at=now,
        )
        .on_conflict_do_update(
            index_elements=["a", "b", "kind"],
            set_={
                "weight": _greatest(WalletRelationship.weight, int(weight)),
                "last_seen_block": _greatest(WalletRelationship.last_seen_block, int(block)),
            },
        )
    )
    await session.execute(stmt)


def _greatest(col, val: int):
    """SQLAlchemy ``GREATEST(col, :val)`` -- portable across PG / SQLite."""
    from sqlalchemy import func
    return func.greatest(col, int(val))


def _least(col, val: int):
    """SQLAlchemy ``LEAST(col, :val)`` -- portable across PG / SQLite."""
    from sqlalchemy import func
    return func.least(col, int(val))


def _addr_from_topic(topic_hex: str) -> str | None:
    """Decode the address out of an indexed-topic 32-byte word.

    Topics are 32-byte left-padded words; the address occupies the
    rightmost 20 bytes. ``0x000...addr`` -> ``addr``.
    """
    if not topic_hex or not topic_hex.startswith("0x"):
        return None
    raw = topic_hex[2:]
    if len(raw) != 64:
        return None
    addr = "0x" + raw[-40:]
    return addr
