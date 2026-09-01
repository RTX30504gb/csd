import logging
import json
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import xgboost as xgb
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, roc_auc_score

from app.database.models import RiskScore, Token
from app.services.risk_features import compute_full_risk_features

logger = logging.getLogger(__name__)

MODEL_PATH = "models/rug_model.json"

async def train_model(session: AsyncSession):
    """Train the XGBoost rug-pull detector model.

    1. Fetch tokens with known outcomes (rug/safe).
    2. Extract features for each.
    3. Train an XGBClassifier.
    4. Save the model.
    """
    logger.info("Starting ML model training...")

    # 1. Fetch labels
    res = await session.execute(
        select(RiskScore.token_address, RiskScore.outcome)
        .where(RiskScore.outcome.is_not(None))
    )
    data = res.all()
    if not data:
        logger.warning("No labeled data found in risk_scores table. Training skipped.")
        return False

    logger.info("Found %d labeled samples.", len(data))

    # 2. Extract features
    samples = []
    labels = []

    for addr, outcome in data:
        features = await compute_full_risk_features(session, addr)
        if not features:
            continue

        samples.append(features)
        labels.append(1 if outcome == "rug" else 0)

    if not samples:
        logger.error("Could not extract features for any labeled samples.")
        return False

    # Convert to DataFrame for XGBoost
    df = pd.DataFrame(samples)
    X = df.values
    y = np.array(labels)

    # Handle missing values (XGBoost handles NaNs, but we'll be explicit)
    X = np.nan_to_num(X, nan=0.0)

    # 3. Train/Test Split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    # 4. Train XGBoost
    # Use a simple binary classifier
    model = xgb.XGBClassifier(
        n_estimators=100,
        max_depth=5,
        learning_rate=0.1,
        objective="binary:logistic",
        random_state=42,
        use_label_encoder=False,
        eval_metric="logloss"
    )

    model.fit(X_train, y_train)

    # 5. Evaluate
    preds = model.predict(X_test)
    probs = model.predict_proba(X_test)[:, 1]

    report = classification_report(y_test, preds)
    auc = roc_auc_score(y_test, probs)

    logger.info("Model Training Complete.\n%s\nAUC: %.4f", report, auc)

    # 6. Save model
    import os
    os.makedirs("models", exist_ok=True)
    model.save_model(MODEL_PATH)
    logger.info("Model saved to %s", MODEL_PATH)

    return True
