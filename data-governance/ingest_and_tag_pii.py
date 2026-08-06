"""Catalogs the REAL Postgres schema (reflected live via SQLAlchemy, not
hand-typed) into OpenMetadata as a native Postgres-typed service, tags
`customers.email` PII.Sensitive (OpenMetadata's built-in PII
classification), and records real lineage (customers/products -> orders,
matching the actual foreign keys). This is the evidence for Scenario C
(PII masking) and the ISO 42001 "sensitive data classified" / "decisions
logged" compliance items.

Talks to OpenMetadata over plain REST (agent/om_client.py) — see that
file's docstring for why the official SDK isn't used. Authenticates
automatically via om_client's real POST /users/login (no manual token
generation needed). Request bodies below match OpenMetadata 1.5.11's real
generated schema field names (verified by inspecting the installed
openmetadata-ingestion package's pydantic models directly), even though
that package itself isn't a runtime dependency here.

Run via: docker compose run --rm agent python data-governance/ingest_and_tag_pii.py
"""
import os

from sqlalchemy import inspect

from agent import om_client
from agent.db import get_engine

SERVICE_NAME = "governance-demo-source"
DB_NAME = "governance_demo"
SCHEMA_NAME = "public"
TABLES = ["customers", "products", "orders"]
PII_COLUMNS = {"customers": ["email"]}
# (from_table, to_table) — mirrors the real foreign keys in init_db.sh.
LINEAGE_EDGES = [("customers", "orders"), ("products", "orders")]

_SQLALCHEMY_TO_OM_TYPE = {
    "SERIAL": "INT",
    "INTEGER": "INT",
    "TEXT": "STRING",
    "VARCHAR": "STRING",
    "DATE": "DATE",
    "TIMESTAMP": "TIMESTAMP",
}


def reflect_columns(table_name: str) -> list[dict]:
    inspector = inspect(get_engine())
    columns = []
    for col in inspector.get_columns(table_name):
        om_type = "STRING"
        for prefix, mapped in _SQLALCHEMY_TO_OM_TYPE.items():
            if prefix in str(col["type"]).upper():
                om_type = mapped
                break
        columns.append({"name": col["name"], "dataType": om_type})
    return columns


def main():
    assert om_client.health_check(), "OpenMetadata not reachable or auto-login failed — is the server up?"

    service = om_client.create_or_update(
        "/services/databaseServices",
        {
            "name": SERVICE_NAME,
            "serviceType": "Postgres",
            "connection": {
                "config": {
                    "type": "Postgres",
                    "scheme": "postgresql+psycopg2",
                    "username": "agent_app",
                    "authType": {"password": os.environ.get("APP_DB_PASSWORD", "")},
                    "hostPort": "app-db:5432",
                    "database": DB_NAME,
                }
            },
        },
    )
    database = om_client.create_or_update("/databases", {"name": DB_NAME, "service": service["fullyQualifiedName"]})
    schema = om_client.create_or_update(
        "/databaseSchemas", {"name": SCHEMA_NAME, "database": database["fullyQualifiedName"]}
    )

    tables = {}
    for table_name in TABLES:
        columns = reflect_columns(table_name)
        table = om_client.create_or_update(
            "/tables",
            {
                "name": table_name,
                "databaseSchema": schema["fullyQualifiedName"],
                "columns": columns,
                "description": f"Reflected live from Postgres table `{table_name}` (see data-governance/init_db.sh).",
            },
        )
        tables[table_name] = table
        print(f"[ingest] cataloged table {table['fullyQualifiedName']} ({len(columns)} columns)")

    
    for table_name, pii_cols in PII_COLUMNS.items():
        table = tables[table_name]
        patch_ops = []
        for idx, col in enumerate(table["columns"]):
            if col["name"] in pii_cols:
                patch_ops.append(
                    {
                        "op": "add",
                        "path": f"/columns/{idx}/tags",
                        "value": [
                            {
                                "tagFQN": "PII.Sensitive",
                                "source": "Classification",
                                "labelType": "Manual",
                                "state": "Confirmed",
                            }
                        ],
                    }
                )
        if patch_ops:
            om_client.patch("/tables", table["id"], patch_ops)
            print(f"[ingest] tagged {pii_cols} in '{table_name}' as PII.Sensitive")

    # Real lineage, matching the actual foreign keys.
    for from_table, to_table in LINEAGE_EDGES:
        om_client.add_lineage(tables[from_table]["id"], tables[to_table]["id"])
        print(f"[ingest] recorded lineage {from_table} -> {to_table}")


if __name__ == "__main__":
    main()
