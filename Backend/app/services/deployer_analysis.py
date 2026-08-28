"""Deployer analysis (spec sec.14).

Pure aggregation over data already collected by earlier phases --
no new RPC calls. For a given deployer address:

  number_of_previous_contracts   <- count(ContractDeployment) by deployer
  number_of_previous_token_launches <- count(Token) by deployer
  previous_suspicious_tokens     <- count of that deployer's tokens
                                     whose ContractRiskFlags show a
                                     dangerous-capability combination
                                     (see SUSPICIOUS_* below) AND
                                     ownership not renounced -- a
                                     documented heuristic, not a
                                     certainty
  previous_liquidity_withdrawals <- count of LiquidityEvent rows
                                     (event_type="withdrawal") on
                                     pools belonging to this
                                     deployer's tokens
  previous_token_collapses       <- count of that deployer's tokens
                                     with at least one withdrawal
                                     event whose drop exceeds
                                     COLLAPSE_THRESHOLD (default 90%)
  wallet_age_blocks              <- current_block - Wallet.first_seen_block,
                                     if the deployer has a Wallet row
                                     (from Phase 11's wallet graph);
                                     None if never observed there
  relationship_count             <- count of WalletRelationship edges
                                     touching this address

What this does NOT compute (documented, not silently skipped):
  - funding_sources: tracing where the deployer's very first ETH
    came from requires recursively walking native-ETH transactions
    into the address (potentially many levels), which needs
    ``eth_getTransactionByHash``/trace-style calls this pipeline
    doesn't make. ``wallet_graph.py`` explicitly deferred the same
    thing for the same reason (see its own module docstring); we
    inherit that limitation rather than fake an answer.

Per spec sec.14's own example ("Token 4 should inherit significant
deployer risk"), this module deliberately returns the raw counts
rather than a single risk score -- turning these into a score is
the risk-engine's job (a later spec section), not this one's.
"""
from __future__ import annotations

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    ContractDeployment,
    ContractRiskFlags,
    LiquidityEvent,
    LiquidityPool,
    Token,
    Wallet,
    WalletRelationship,
)

COLLAPSE_THRESHOLD = 0.90  # 90%+ single-event drop counts as a "collapse"


def _lower(addr: str) -> str:
    return addr.lower() if addr else addr


def _is_suspicious(flags: ContractRiskFlags) -> bool:
    """Heuristic only -- see module docstring. Flags a token as
    "suspicious" if it combines any dangerous capability with
    non-renounced ownership (owner_renounced is False, i.e. we
    positively confirmed a live owner -- NOT None/unknown, which
    would over-count contracts we simply couldn't determine
    ownership for)."""
    has_any_dangerous = (
        flags.has_mint
        or flags.has_blacklist
        or flags.has_pause
        or flags.has_tax_control
        or flags.has_max_tx_control
        or flags.has_max_wallet_control
    )
    return bool(has_any_dangerous and flags.owner_renounced is False)


async def analyze_deployer(
    deployer_address: str,
    session: AsyncSession,
    current_block: int | None = None,
) -> dict:
    deployer = _lower(deployer_address)

    num_contracts = (
        await session.execute(
            select(func.count()).select_from(ContractDeployment)
            .where(ContractDeployment.deployer == deployer)
        )
    ).scalar_one()

    token_addresses = (
        await session.execute(
            select(Token.contract_address).where(Token.deployer == deployer)
        )
    ).scalars().all()
    num_tokens = len(token_addresses)

    suspicious_count = 0
    if token_addresses:
        risk_rows = (
            await session.execute(
                select(ContractRiskFlags).where(
                    ContractRiskFlags.token_address.in_(token_addresses)
                )
            )
        ).scalars().all()
        suspicious_count = sum(1 for f in risk_rows if _is_suspicious(f))

    withdrawal_count = 0
    collapse_count = 0
    if token_addresses:
        pool_addresses = (
            await session.execute(
                select(LiquidityPool.pool_address, LiquidityPool.token_address)
                .where(LiquidityPool.token_address.in_(token_addresses))
            )
        ).all()
        pool_to_token = {p: t for p, t in pool_addresses}
        if pool_to_token:
            events = (
                await session.execute(
                    select(LiquidityEvent).where(
                        LiquidityEvent.pool_address.in_(list(pool_to_token.keys())),
                        LiquidityEvent.event_type == "withdrawal",
                    )
                )
            ).scalars().all()
            withdrawal_count = len(events)
            collapsed_tokens: set[str] = set()
            for ev in events:
                if ev.percent_change <= -COLLAPSE_THRESHOLD:
                    collapsed_tokens.add(pool_to_token[ev.pool_address])
            collapse_count = len(collapsed_tokens)

    wallet = (
        await session.execute(select(Wallet).where(Wallet.address == deployer))
    ).scalars().first()
    wallet_age_blocks = None
    if wallet is not None and current_block is not None:
        wallet_age_blocks = max(0, int(current_block) - int(wallet.first_seen_block))

    relationship_count = (
        await session.execute(
            select(func.count()).select_from(WalletRelationship)
            .where((WalletRelationship.a == deployer) | (WalletRelationship.b == deployer))
        )
    ).scalar_one()

    return {
        "deployer_address": deployer,
        "number_of_previous_contracts": int(num_contracts),
        "number_of_previous_token_launches": num_tokens,
        "previous_suspicious_tokens": suspicious_count,
        "previous_liquidity_withdrawals": withdrawal_count,
        "previous_token_collapses": collapse_count,
        "wallet_age_blocks": wallet_age_blocks,
        "relationship_count": int(relationship_count),
        "funding_sources": None,  # not computed -- see module docstring
    }
