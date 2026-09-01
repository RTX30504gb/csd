import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import numpy as np
from sklearn.metrics import precision_score, recall_score, f1_score, roc_auc_score, confusion_matrix

from app.database.models import RiskScore

logger = logging.getLogger(__name__)

class MLEvaluator:
    """Service for evaluating the ML model performance using ground truth outcomes."""

    async def evaluate_performance(self, session: AsyncSession) -> dict:
        """Calculate key classification metrics by comparing predictions with outcomes.

        Returns a dictionary of metrics: precision, recall, f1, auc, and confusion matrix.
        """
        # Fetch all scores that have a ground truth outcome
        res = await session.execute(
            select(RiskScore.score, RiskScore.outcome)
            .where(RiskScore.outcome.is_not(None))
        )
        data = res.all()

        if not data:
            logger.warning("No labeled data available for evaluation.")
            return {"error": "No labeled data found"}

        # True labels (rug=1, safe=0)
        y_true = np.array([1 if outcome == "rug" else 0 for _, outcome in data])

        # Binary predictions (threshold = 50)
        y_pred = np.array([1 if score >= 50 else 0 for score, _ in data])

        # Probabilities for AUC
        y_prob = np.array([score / 100.0 for score, _ in data])

        # Calculate metrics
        precision = precision_score(y_true, y_pred, zero_division=0)
        recall = recall_score(y_true, y_pred, zero_division=0)
        f1 = f1_score(y_true, y_pred, zero_division=0)
        auc = roc_auc_score(y_true, y_prob) if len(np.unique(y_true)) > 1 else 0.0
        cm = confusion_matrix(y_true, y_pred).tolist()

        return {
            "precision": float(precision),
            "recall": float(recall),
            "f1_score": float(f1),
            "auc_roc": float(auc),
            "confusion_matrix": cm,
            "sample_count": len(data),
        }

# Singleton instance
ml_evaluator = MLEvaluator()
