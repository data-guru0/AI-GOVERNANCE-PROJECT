"""Real mlflow.evaluate() run against the Production model version — the
'Model version and performance are tracked before deployment' evidence for
the NIST AI RMF (Measure) compliance item. Every prediction is a real API
call (via LLMProxyModel.predict -> agent/llm_client.chat). Run via:
    docker compose run --rm agent python model-governance/evaluate_model.py
"""
import json
import os
import mlflow
import pandas as pd

HERE = os.path.dirname(__file__)
MODEL_NAME = "governance-agent-model"


def main():
    mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", "http://mlflow:5000"))

    rows = []
    with open(os.path.join(HERE, "eval_dataset.jsonl")) as f:
        for line in f:
            rows.append(json.loads(line))
    eval_df = pd.DataFrame(rows)

    with mlflow.start_run(run_name="production_eval_gate"):
        result = mlflow.evaluate(
            model=f"models:/{MODEL_NAME}/Production",
            data=eval_df,
            targets="reference",
            model_type=None,
            extra_metrics=[
                mlflow.metrics.exact_match(),
                mlflow.metrics.flesch_kincaid_grade_level(),
                mlflow.metrics.ari_grade_level(),
            ],
        )

    print("[evaluate_model] metrics:")
    for k, v in result.metrics.items():
        print(f"  {k}: {v}")
    print(f"[evaluate_model] full results table logged to MLflow run — view at the MLflow UI (:5000)")


if __name__ == "__main__":
    main()
