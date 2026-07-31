# One image for the FastAPI agent server AND every one-off governance
# script (model-governance/, data-governance/, operations/, compliance/,
# demos/) — they all run via `docker compose run --rm agent python <path>`,
# so we don't need a separate Dockerfile per script.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONPATH=/app

COPY agent/requirements.txt agent/requirements.txt
RUN pip install --no-cache-dir -r agent/requirements.txt

# The rest of the project (agent/, model-governance/, data-governance/,
# operations/, compliance/, demos/, data-governance/sample_dataset.csv) is
# bind-mounted at runtime by docker-compose — see docker-compose.yml — so
# code edits during the on-camera walkthrough don't require a rebuild.

CMD ["uvicorn", "agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
