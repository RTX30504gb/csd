"""Holder analysis (spec sec.12).

Algorithm
---------
1. On every block, select confirmed tokens with
   ``holder_analysis_analyzed_at IS NULL``, batched (same pattern as
   every other detector in this pipeline).
2. For each token, fetch every ``Transfer`` event from its creation
   block to the current block (chunked over ``max_log_range``,
   reusing the exact chunking approach ``wallet_graph.py`` uses for
   the same RPC constraint).
3. Replay the events as a ledger: ``balance[to] += value``,
   ``balance[from] -= value``. The zero address is never credited or
   debited (mint/burn have no real counterparty).
4. Keep only holders with a positive balance, rank them, and store:
     - every positive-balance holder in ``token_holders``
     - a summary row in ``holder_concentration`` with largest/top5/
       top10/top20 percentages (relative to ``tokens.total_supply``),
       creator holdings, and creator-associated holdings (summing
       balances of any address with a ``deployer_associated`` or
       ``deployer`` classification via Phase 13's classifier).
5. Per spec sec.12's explicit caution, the largest holder's own
   address classification is attached to the summary row so a
   consumer isn't left inferring "is 40% actually a problem" without
   knowing whether that 40% sits in a DEX pool.

This does NOT compute realtime/live balances -- it's a snapshot as
of the block the detector ran at, and (like the other detectors)
does not re-run on an already-analyzed token. A future refinement
would be to re-run on new Transfer activity rather than once ever;
that's a scheduling change, not a logic change, and is flagged here
rather than silently pretended to already exist.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.blockchain.provider import BlockchainProvider
from app.database.models import HolderConcentration, Token, TokenHolder
from app.services.address_classification import (
    CATEGORY_DEPLOYER,
    CATEGORY_DEPLOYER_ASSOCIATED,
    classify_address,
)

logger = logging.getLogger(__name__)

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO_ADDRESS = "0x" + "00" * 20

DEFAULT_BATCH_SIZE = 5  # holder analysis is the heaviest detector (full log replay); keep small
DEFAULT_MAX_LOG_RANGE = 5000


def _lower(addr: str) -> str:
    return addr.lower() if addr else addr


def _addr_from_topic(topic_hex: str) -> str | None:
    if not topic_hex or not topic_hex.startswith("0x") or len(topic_hex) != 66:
        return None
    return "0x" + topic_hex[-40:]


def make_on_block(
    provider: BlockchainProvider,
    session_factory: async_sessionmaker[AsyncSession],
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_log_range: int = DEFAULT_MAX_LOG_RANGE,
):
    """Return an ``on_block(block)`` callback that reconstructs holder balances."""

    async def on_block(block: dict) -> None:
        current_block = int(block["number"])
        async with session_factory() as session:
            tokens = (
                await session.execute(
                    select(Token)
                    .where(Token.holder_analysis_analyzed_at.is_(None))
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
                    balances = await _reconstruct_balances(
                        provider, token.contract_address, token.creation_block,
                        current_block, max_log_range,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "holder analysis failed for %s; will retry next tick",
                        token.contract_address,
                    )
                    continue

                await _store_results(session, token, balances, current_block, now)
                token.holder_analysis_analyzed_at = now
                analyzed += 1

            if analyzed:
                logger.info(
                    "block %s: holder analysis on %d token(s)",
                    current_block, analyzed,
                )
            await session.commit()

    return on_block


async def _reconstruct_balances(
    provider: BlockchainProvider,
    token_address: str,
    from_block: int,
    to_block: int,
    max_log_range: int,
) -> dict[str, int]:
    """Replay every Transfer event into a ``{holder: balance}`` ledger."""
    balances: dict[str, int] = {}
    chunk_start = int(from_block)
    to_block = int(to_block)
    if to_block < chunk_start:
        return balances

    while chunk_start <= to_block:
        chunk_end = min(chunk_start + max_log_range - 1, to_block)
        logs = await provider.get_logs(
            address=token_address,
            topics=[TRANSFER_TOPIC],
            from_block=chunk_start,
            to_block=chunk_end,
        )
        for log in logs:
            topics = log.get("topics") or []
            if len(topics) < 3:
                continue
            from_addr = _addr_from_topic(topics[1])
            to_addr = _addr_from_topic(topics[2])
            value = _decode_uint256(log.get("data"))
            if value is None:
                continue
            if from_addr and from_addr != ZERO_ADDRESS:
                balances[from_addr] = balances.get(from_addr, 0) - value
            if to_addr and to_addr != ZERO_ADDRESS:
                balances[to_addr] = balances.get(to_addr, 0) + value
        chunk_start = chunk_end + 1

    # Drop non-positive balances (fully exited holders, and any
    # negative artifact from processing an incomplete/malformed log
    # window -- a real ledger should never go negative for an
    # address that only ever received what it later sent, but we
    # don't want a data glitch to produce a nonsensical stored value).
    return {addr: bal for addr, bal in balances.items() if bal > 0}


def _decode_uint256(data: str | bytes | None) -> int | None:
    if data is None:
        return None
    if isinstance(data, str):
        raw = bytes.fromhex(data.removeprefix("0x"))
    else:
        raw = bytes(data)
    if len(raw) < 32:
        return None
    return int.from_bytes(raw[-32:], "big")


async def _store_results(
    session: AsyncSession,
    token: Token,
    balances: dict[str, int],
    current_block: int,
    now: datetime,
) -> None:
    # Replace-not-append: clear any prior snapshot for this token
    # before inserting the new one (idempotent re-analysis, and
    # avoids stale holders lingering after they've exited).
    await session.execute(delete(TokenHolder).where(TokenHolder.token_address == token.contract_address))
    await session.execute(
        delete(HolderConcentration).where(HolderConcentration.token_address == token.contract_address)
    )

    ranked = sorted(balances.items(), key=lambda kv: (-kv[1], kv[0]))
    total_supply = int(token.total_supply) if token.total_supply else 0

    for rank, (holder_addr, bal) in enumerate(ranked, start=1):
        session.add(
            TokenHolder(
                token_address=token.contract_address,
                holder_address=holder_addr,
                balance=bal,
                rank=rank,
                updated_at=now,
            )
        )

    def pct(amount: int) -> float:
        return (amount / total_supply * 100.0) if total_supply > 0 else 0.0

    largest_pct = pct(ranked[0][1]) if ranked else 0.0
    top5_pct = pct(sum(b for _, b in ranked[:5]))
    top10_pct = pct(sum(b for _, b in ranked[:10]))
    top20_pct = pct(sum(b for _, b in ranked[:20]))

    deployer = _lower(token.deployer)
    creator_holdings = balances.get(deployer, 0)

    # Classifying every holder is unbounded work for a widely-held
    # token (potentially thousands of dust holders). Deployer-
    # associated wallets that matter for concentration are
    # overwhelmingly among the larger holders in practice (a dust
    # holder isn't materially shifting the concentration picture
    # either way), so we cap the scan at the top 50 by balance.
    CLASSIFICATION_SCAN_CAP = 50
    creator_associated_total = 0
    for holder_addr, bal in ranked[:CLASSIFICATION_SCAN_CAP]:
        if holder_addr == deployer:
            continue
        result = await classify_address(holder_addr, session, provider=None)
        if result["category"] in (CATEGORY_DEPLOYER, CATEGORY_DEPLOYER_ASSOCIATED):
            creator_associated_total += bal

    largest_holder_addr = ranked[0][0] if ranked else None
    largest_holder_category = None
    if largest_holder_addr is not None:
        result = await classify_address(largest_holder_addr, session, provider=None)
        largest_holder_category = result["category"]

    session.add(
        HolderConcentration(
            token_address=token.contract_address,
            largest_holder_pct=largest_pct,
            top5_pct=top5_pct,
            top10_pct=top10_pct,
            top20_pct=top20_pct,
            creator_holdings_pct=pct(creator_holdings),
            creator_associated_holdings_pct=pct(creator_associated_total),
            largest_holder_address=largest_holder_addr,
            largest_holder_category=largest_holder_category,
            holder_count=len(ranked),
            analyzed_block=current_block,
            analyzed_at=now,
        )
    )
