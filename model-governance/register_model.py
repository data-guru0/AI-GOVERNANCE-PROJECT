"""Registers governance-agent-model in MLflow's Model Registry from
model_card.yaml: one version per entry, tagged with the model card
(intended use, eval notes, limitations, risk_tier).

No version is ever registered straight to "Production" here, regardless
of what model_card.yaml says — Production has to be *earned* by
promote_model.py's automated eval gate (real mlflow.evaluate() against a
real validation threshold), not decreed in a YAML file by whoever runs
this script. A "Production"-requested entry is capped to "Staging" at
registration time; promote_model.py is what can actually move it further.

This script (and promote_model.py) are not meant to be run by hand — see
agent/startup_automation.py, which runs both automatically on agent
startup and on a recurring schedule. Kept as standalone scripts too since
that's still useful for a first bring-up or manual re-run.
"""
import os
import yaml
import mlflow
import mlflow.pyfunc
from mlflow.tracking import MlflowClient

from agent.llm_client import chat

HERE = os.path.dirname(__file__)
MODEL_NAME = "governance-agent-model"


class LLMProxyModel(mlflow.pyfunc.PythonModel):
    """A pyfunc wrapper whose predict() makes a REAL call to the provider
    baked into its own config artifact — so mlflow.evaluate() exercises the
    actual OpenAI/Groq API, not a stub."""

    def load_context(self, context):
        with open(context.artifacts["config"]) as f:
            self.config = yaml.safe_load(f)

    def predict(self, context, model_input, params=None):
        prompts = model_input["prompt"] if hasattr(model_input, "columns") else model_input
        return [chat(self.config["provider"], self.config["model_id"], p) for p in prompts]


def build_card_description(card: dict, version_entry: dict) -> str:
    return (
        f"### Intended use\n{card['intended_use'].strip()}\n\n"
        f"### Eval notes\n{card['eval_notes'].strip()}\n\n"
        f"### Limitations\n{card['limitations'].strip()}\n\n"
        f"### Risk tier: {version_entry['risk_tier']}\n{version_entry['risk_tier_reason']}\n"
    )


def _already_registered(client: MlflowClient, version_alias: str) -> bool:
    """Idempotency check — safe to call on every agent startup without
    creating a fresh duplicate version each time."""
    try:
        versions = client.search_model_versions(f"name='{MODEL_NAME}'")
    except Exception:
        return False
    return any(v.tags.get("version_alias") == version_alias for v in versions)


def main():
    mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", "http://mlflow:5000"))
    client = MlflowClient()

    with open(os.path.join(HERE, "model_card.yaml")) as f:
        card = yaml.safe_load(f)

    for entry in card["versions"]:
        if _already_registered(client, entry["version_alias"]):
            print(f"[register_model] {entry['version_alias']} already registered, skipping")
            continue

        registered_stage = "Staging" if entry["stage"] == "Production" else entry["stage"]

        config_path = os.path.join(HERE, f"_config_{entry['version_alias']}.yaml")
        with open(config_path, "w") as f:
            yaml.safe_dump({"provider": entry["provider"], "model_id": entry["model_id"]}, f)

        with mlflow.start_run(run_name=entry["version_alias"]):
            mlflow.pyfunc.log_model(
                artifact_path="model",
                python_model=LLMProxyModel(),
                artifacts={"config": config_path},
                registered_model_name=MODEL_NAME,
            )
        os.remove(config_path)

        # log_model+registered_model_name creates a new version; find it.
        version = client.get_latest_versions(MODEL_NAME, stages=["None"])[0].version

        client.transition_model_version_stage(MODEL_NAME, version, stage=registered_stage)
        client.update_model_version(MODEL_NAME, version, description=build_card_description(card, entry))
        for tag_key in ("provider", "model_id", "risk_tier", "risk_tier_reason", "version_alias"):
            client.set_model_version_tag(MODEL_NAME, version, tag_key, str(entry.get(tag_key, entry.get("version_alias"))))

        note = " (capped from requested Production — must pass promote_model.py's gate)" if registered_stage != entry["stage"] else ""
        print(f"[register_model] {entry['version_alias']} -> version {version}, stage={registered_stage}{note}, risk_tier={entry['risk_tier']}")


if __name__ == "__main__":
    main()
