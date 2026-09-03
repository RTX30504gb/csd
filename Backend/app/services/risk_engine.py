import logging
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import RiskScore
from app.services.ml_inference import ml_inference
from app.services.mechanical_verification import mechanical_verification

logger = logging.getLogger(__name__)

class RiskEngine:
    """Orchestrates risk scoring and persists results to the database."""

    async def calculate_and_store_score(self, session: AsyncSession, token_address: str) -> RiskScore:
        """Compute a risk score using ML and store it in the database."""
        logger.info("ANALYSIS STARTED for token %s", token_address)

        # 1. Perform ML Inference
        try:
            ml_score = await ml_inference.predict_risk(session, token_address)
            logger.info("ML ANALYSIS COMPLETE for token %s: score %f", token_address, ml_score)
        except Exception as e:
            logger.exception("ML analysis failed for token %s: %s", token_address, e)
            ml_score = 0.0

        # 2. Mechanical Verification (Phase 20)
        try:
            mech_data = await mechanical_verification.verify_mechanical_risk(session, token_address)
            mech_score = mech_data["mechanical_risk_score"]
            logger.info("MECHANICAL ANALYSIS COMPLETE for token %s: score %f", token_address, mech_score)
        except Exception as e:
            logger.exception("Mechanical verification failed for token %s: %s", token_address, e)
            mech_score = 0.0
            mech_data = {"flags": {}}

        # Combine scores: Max of ML and Mechanical
        final_score = max(ml_score, mech_score)

        # 3. Determine risk level
        level = self._determine_level(final_score)

        # 4. Generate reasons
        reasons = self._generate_reasons(final_score, level, mech_data.get("flags", {}))

        # 5. Store result
        risk_score = RiskScore(
            token_address=token_address,
            score=int(final_score),
            level=level,
            category_scores={"ml_score": ml_score, "mechanical_score": mech_score},
            reasons=reasons,
            computed_at=datetime.now(timezone.utc),
        )

        session.add(risk_score)
        await session.commit()
        logger.info("RISK SCORE SAVED for token %s: %d (%s)", token_address, risk_score.score, level)
        logger.info("ANALYSIS COMPLETE for token %s", token_address)

        return risk_score

    def _determine_level(self, score: float) -> str:
        if score < 30: return "Low"
        if score < 55: return "Suspicious"
        if score < 80: return "High"
        return "Critical"

    def _generate_reasons(self, score: float, level: str, mech_flags: dict) -> list[str]:
        reasons = []
        if level == "Low":
            reasons.append("No significant risk signals detected.")
        elif level == "Suspicious":
            reasons.append("Model detected mild anomalies in holder concentration or deployer history.")
        elif level == "High":
            reasons.append("Strong correlation with previous rug-pull patterns found in bytecode or liquidity.")
        elif level == "Critical":
            reasons.append("Critical risk: High probability of rug-pull detected by ML model.")

        # Add mechanical verification reasons
        if mech_flags.get("hard_rug_detected"):
            reasons.append("CRITICAL: 100% liquidity removal detected.")
        if mech_flags.get("wash_trading_suspected"):
            reasons.append("High frequency churn detected: potential wash-trading bots.")

        return reasons

# Singleton instance
risk_engine = RiskEngine()
