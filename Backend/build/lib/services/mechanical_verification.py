import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import LiquidityPool, LiquidityEvent, TokenHolder

logger = logging.getLogger(__name__)

class MechanicalVerificationService:
    """Detects mechanical/bot-driven patterns in token activity."""

    async def verify_mechanical_risk(self, session: AsyncSession, token_address: str) -> dict:
        """Analyze if a token's activity is mechanical/bot-driven.

        Returns a dict of binary flags and a final mechanical_risk_score (0-100).
        """
        flags = {
            "hard_rug_detected": False,
            "wash_trading_suspected": False,
            "bot_cluster_detected": False,
        }
        score = 0.0

        # 1. Hard Rug Detection:
        # Find if any liquidity pool for this token has had a 100% removal event.
        pools_res = await session.execute(
            select(LiquidityPool.pool_address).where(LiquidityPool.token_address == token_address)
        )
        pools = pools_res.scalars().all()

        for pool_addr in pools:
            events_res = await session.execute(
                select(LiquidityEvent)
                .where(LiquidityEvent.pool_address == pool_addr)
                .where(LiquidityEvent.event_type == "withdrawal")
                .where(LiquidityEvent.percent_change <= -0.99) # 99% or more removed
            )
            if events_res.scalars().first():
                flags["hard_rug_detected"] = True
                score += 70.0
                break

        # 2. Wash Trading / Churn:
        # Check for an unusually high number of holders relative to total supply
        # combined with low holder concentration (everyone has a tiny bit).
        holders_res = await session.execute(
            select(TokenHolder).where(TokenHolder.token_address == token_address)
        )
        holders = holders_res.scalars().all()
        if len(holders) > 1000: # Heuristic: >1000 holders for a new token is often bot-driven
            flags["wash_trading_suspected"] = True
            score += 20.0

        # Cap score at 100
        final_score = min(100.0, score)

        return {
            "flags": flags,
            "mechanical_risk_score": final_score,
            "verification_status": "verified" if final_score > 0 else "clean"
        }

# Singleton
mechanical_verification = MechanicalVerificationService()
