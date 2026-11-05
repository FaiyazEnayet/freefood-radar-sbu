"""Free-food text classifier.

Pipeline: TF-IDF (word 1-2 grams) -> LogisticRegression -> Platt
scaling (CalibratedClassifierCV, sigmoid) so the output probabilities
are actually calibrated: a 0.8 should mean food four times out of five.

Trained on data/labeled.csv (hand-labeled SBU-style event blurbs).

The probabilities are one candidate signal. Digest scoring combines
them with the deterministic text evidence in radar.features
(analyze_evidence); the classifier output alone is never treated as
proof that food is complimentary.
"""

from __future__ import annotations

import json
import os

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)
from sklearn.model_selection import StratifiedKFold, cross_val_predict, train_test_split

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "..", "data")
MODEL_DIR = os.path.join(HERE, "..", "..", "models")


def load_labeled(path: str | None = None) -> pd.DataFrame:
    path = path or os.path.join(DATA, "labeled.csv")
    df = pd.read_csv(path)
    df = df.dropna(subset=["text", "label"])
    df["label"] = df["label"].astype(int)
    return df


def build_pipeline() -> CalibratedClassifierCV:
    vectorizer = TfidfVectorizer(
        ngram_range=(1, 2),
        min_df=2,
        max_df=0.9,
        sublinear_tf=True,
        stop_words="english",
    )
    base = LogisticRegression(max_iter=2000, C=2.0, class_weight="balanced")
    # Wrap vectorizer+model manually: calibrate on top of the full pipeline.
    from sklearn.pipeline import Pipeline

    pipe = Pipeline([("tfidf", vectorizer), ("lr", base)])
    calibrated = CalibratedClassifierCV(pipe, method="sigmoid", cv=3)
    return calibrated


def train(path: str | None = None, test_size: float = 0.25, seed: int = 7) -> dict:
    """Train on labeled data; return metrics + the fitted model."""
    df = load_labeled(path)
    X = df["text"].tolist()
    y = df["label"].tolist()
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=seed, stratify=y
    )
    model = build_pipeline()
    model.fit(X_train, y_train)
    proba = model.predict_proba(X_test)[:, 1]
    pred = (proba >= 0.5).astype(int)

    # Cross-validated probabilities on the full set for the calibration curve.
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=seed)
    cv_proba = cross_val_predict(build_pipeline(), X, y, cv=cv, method="predict_proba")[:, 1]

    metrics = {
        "n_total": len(df),
        "n_train": len(X_train),
        "n_test": len(X_test),
        "accuracy": float(accuracy_score(y_test, pred)),
        "precision": float(precision_score(y_test, pred, zero_division=0)),
        "recall": float(recall_score(y_test, pred, zero_division=0)),
        "f1": float(f1_score(y_test, pred, zero_division=0)),
        "confusion": confusion_matrix(y_test, pred).tolist(),
        "seed": seed,
    }
    os.makedirs(MODEL_DIR, exist_ok=True)
    joblib.dump(model, os.path.join(MODEL_DIR, "classifier.joblib"))
    with open(os.path.join(MODEL_DIR, "metrics.json"), "w") as f:
        json.dump(metrics, f, indent=2)
    # Save calibration inputs for the dashboard.
    np.savez(
        os.path.join(MODEL_DIR, "calibration.npz"),
        y_true=np.array(y),
        y_prob=cv_proba,
    )
    return {"metrics": metrics, "model": model}


def load_model(path: str | None = None):
    path = path or os.path.join(MODEL_DIR, "classifier.joblib")
    return joblib.load(path)


def predict_proba(model, texts: list[str]) -> np.ndarray:
    return model.predict_proba(texts)[:, 1]
