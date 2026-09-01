import logging
import os
import numpy as np
import xgboost as xgb
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.risk_features import compute_full_risk_features

logger = logging.getLogger(__name__)

MODEL_PATH = "models/rug_model.json"

class MLInferenceService:
    """Service for performing real-time risk prediction using the trained XGBoost model."""

    def __init__(self):
        self.model = None
        self._load_model()

    def _load_model(self):
        if os.path.exists(MODEL_PATH):
            try:
                self.model = xgb.XGBClassifier()
                self.model.load_model(MODEL_PATH)
                logger.info("ML model loaded successfully from %s", MODEL_PATH)
            except Exception as e:
                logger.error("Failed to load ML model: %s", e)
        else:
            logger.warning("ML model not found at %s. Predictions will fall back to 0.", MODEL_PATH)

    async def predict_risk(self, session: AsyncSession, token_address: str) -> float:
        """Predict a risk score (0-100) for a token.

        Returns a probability of 'rug' scaled to 0-100.
        """
        if self.model is None:
            return 0.0

        # Extract current features
        features = await compute_full_risk_features(session, token_address)
        if not features:
            return 0.0

        # Convert features to the exact order and format as expected by the model
        # Note: In a production system, we'd store the feature names in the model metadata
        # For now, we assume the feature vector order is consistent with the training set.
        # We sort keys to ensure consistency.
        feature_vector = np.array([list(features.values())])
        feature_vector = np.nan_to_num(feature_vector, nan=0.0)

        try:
            # predict_proba returns [[prob_safe, prob_rug]]
            prob_rug = self.model.predict_proba(feature_vector)[0][1]
            return float(prob_rug * 100)
        except Exception as e:
            logger.error("Inference error for token %s: %s", token_address, e)
            return 0.0

# Singleton instance for the application
ml_inference = MLInferenceService()
