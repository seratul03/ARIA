import pickle
import hashlib
import json
import logging
import sqlite3
import numpy as np
from pathlib import Path
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.naive_bayes import GaussianNB
from sklearn.model_selection import train_test_split, StratifiedKFold
from sklearn.metrics import accuracy_score, roc_auc_score, classification_report
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline

from aria.predictors.dataset import build_candidate_dataset, build_failure_dataset, build_risk_dataset

logger = logging.getLogger(__name__)

MODEL_DIR = Path("aria/predictors/models/")
MIN_TEST_AUC_FOR_PROMOTION = 0.65
MIN_TEST_ACCURACY_FOR_PROMOTION = 0.60
TEST_SET_FRACTION = 0.20
RANDOM_SEED = 42

PREDICTOR_CONFIGS = {
    "success": {
        "estimator": GradientBoostingClassifier(
            n_estimators=100, max_depth=3, learning_rate=0.1,
            subsample=0.8, random_state=RANDOM_SEED
        ),
        "min_samples": 50,
        "dataset_fn": build_candidate_dataset,
    },
    "failure": {
        "estimator": LogisticRegression(
            C=1.0, max_iter=1000, random_state=RANDOM_SEED
        ),
        "min_samples": 30,
        "dataset_fn": build_failure_dataset,
    },
    "risk": {
        "estimator": GradientBoostingClassifier(
            n_estimators=50, max_depth=2, learning_rate=0.1,
            random_state=RANDOM_SEED
        ),
        "min_samples": 20,
        "dataset_fn": build_risk_dataset,
    },
}

def evaluate_with_cv(pipeline, X, y, n_splits=5) -> dict:
    """
    StratifiedKFold(n_splits=5, shuffle=True, random_state=RANDOM_SEED).
    Returns mean AUC, std AUC across folds.
    """
    counts = np.bincount(y)
    if len(counts) < 2 or min(counts) < n_splits:
        return {"mean_auc": 0.0, "std_auc": 0.0}
        
    skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=RANDOM_SEED)
    aucs = []
    
    X_arr = np.array(X)
    y_arr = np.array(y)
    
    for train_index, test_index in skf.split(X_arr, y_arr):
        X_train, X_test = X_arr[train_index], X_arr[test_index]
        y_train, y_test = y_arr[train_index], y_arr[test_index]
        
        from sklearn.base import clone
        cloned = clone(pipeline)
        
        cloned.fit(X_train, y_train)
        y_pred_proba = cloned.predict_proba(X_test)[:, 1]
        
        try:
            auc = roc_auc_score(y_test, y_pred_proba)
            aucs.append(auc)
        except ValueError:
            pass
            
    if not aucs:
        return {"mean_auc": 0.0, "std_auc": 0.0}
        
    return {"mean_auc": float(np.mean(aucs)), "std_auc": float(np.std(aucs))}

def train_predictor(predictor_type: str, db_path: str) -> dict:
    if predictor_type not in PREDICTOR_CONFIGS:
        return {"status": "error", "error": f"Unknown predictor type {predictor_type}"}
        
    config = PREDICTOR_CONFIGS[predictor_type]
    dataset = config["dataset_fn"](db_path)
    
    if dataset["sample_count"] < config["min_samples"]:
        return {"status": "insufficient_data", "samples": dataset["sample_count"]}
        
    X = dataset["X"]
    y = dataset["y"]
    
    if len(set(y)) < 2:
        return {"status": "insufficient_data", "samples": dataset["sample_count"], "error": "Only one class present."}
        
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=TEST_SET_FRACTION, random_state=RANDOM_SEED, stratify=y
    )
    
    pipeline = Pipeline([
        ('scaler', StandardScaler()),
        ('estimator', config["estimator"])
    ])
    
    pipeline.fit(X_train, y_train)
    
    y_pred = pipeline.predict(X_test)
    y_pred_proba = pipeline.predict_proba(X_test)[:, 1]
    
    acc = accuracy_score(y_test, y_pred)
    try:
        auc = roc_auc_score(y_test, y_pred_proba)
    except ValueError:
        auc = 0.0
        
    report = classification_report(y_test, y_pred, output_dict=True)
    
    cv_results = {}
    if len(y) < 100:
        cv_results = evaluate_with_cv(pipeline, X, y)
        logger.info(f"CV Fallback for {predictor_type}: AUC {cv_results.get('mean_auc', 0.0):.3f} +- {cv_results.get('std_auc', 0.0):.3f}")
        
    if auc >= MIN_TEST_AUC_FOR_PROMOTION and acc >= MIN_TEST_ACCURACY_FOR_PROMOTION:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        try:
            row = cursor.execute("SELECT MAX(version) as max_v FROM predictor_registry WHERE predictor_type = ?", (predictor_type,)).fetchone()
            version = (dict(row).get("max_v") or 0) + 1
        except sqlite3.OperationalError:
            version = 1
            
        file_path = MODEL_DIR / f"{predictor_type}_v{version}.pkl"
        
        with open(file_path, "wb") as f:
            pickle.dump(pipeline, f)
            
        try:
            cursor.execute(
                "INSERT INTO predictor_registry (predictor_type, version, status, model_path, feature_schema_hash, train_samples, test_samples, test_auc, test_accuracy) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (predictor_type, version, "candidate", str(file_path), "v1", len(X_train), len(X_test), auc, acc)
            )
            conn.commit()
        except sqlite3.OperationalError as e:
            logger.error(f"Error saving to registry: {e}")
        conn.close()
        
        analyze_feature_importance(predictor_type, version, db_path, dataset["feature_names"], pipeline)
        
        return {
            "status": "trained",
            "version": version,
            "auc": auc,
            "accuracy": acc,
            "report": report
        }
    else:
        return {
            "status": "below_threshold",
            "auc": auc,
            "accuracy": acc,
            "cv_auc": cv_results.get("mean_auc", 0.0)
        }

def analyze_feature_importance(predictor_type: str, version: int, db_path: str, feature_names: list, pipeline=None) -> dict:
    if pipeline is None:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        try:
            row = cursor.execute("SELECT model_path FROM predictor_registry WHERE predictor_type = ? AND version = ?", (predictor_type, version)).fetchone()
            if not row:
                return {}
            file_path = row[0]
            with open(file_path, "rb") as f:
                pipeline = pickle.load(f)
        except sqlite3.OperationalError:
            return {}
        finally:
            conn.close()
            
    estimator = pipeline.named_steps['estimator']
    
    if hasattr(estimator, 'feature_importances_'):
        importances = estimator.feature_importances_
    elif hasattr(estimator, 'coef_'):
        importances = np.abs(estimator.coef_[0])
        if np.sum(importances) > 0:
            importances = importances / np.sum(importances)
    else:
        return {}
        
    feature_importances = list(zip(feature_names, importances))
    feature_importances.sort(key=lambda x: x[1], reverse=True)
    
    top_10 = feature_importances[:10]
    
    phase_3_features = ["rule_compliance_score", "applicable_active_rules_count", "applicable_rules_count", "top_rule_confidence_for_category", "top_rule_confidence"]
    flags = [f for f, imp in top_10 if f in phase_3_features]
    
    notes = {
        "top_10_features": [{"feature": f, "importance": float(imp)} for f, imp in top_10],
        "phase_3_feedback_signals": flags
    }
    
    print(f"\n[Feature Analysis for {predictor_type} v{version}]")
    print(json.dumps(notes, indent=2))
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE predictor_registry SET notes = ? WHERE predictor_type = ? AND version = ?", (json.dumps(notes), predictor_type, version))
        conn.commit()
    except sqlite3.OperationalError:
        pass
    conn.close()
    
    return notes

def retrain_all(db_path: str) -> dict:
    return {ptype: train_predictor(ptype, db_path) for ptype in PREDICTOR_CONFIGS}
