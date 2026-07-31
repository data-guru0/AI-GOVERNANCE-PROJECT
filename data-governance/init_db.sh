#!/bin/bash
# Runs automatically on first Postgres startup (official image convention:
# anything in /docker-entrypoint-initdb.d/ runs once, against an empty data
# volume). Creates a small but real relational schema plus two
# least-privilege application roles, distinct from the postgres superuser —
# the DB-level "blast radius ceiling" underneath the app-level OPA/AGT
# checks, not a replacement for them.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE TABLE customers (
        customer_id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT NOT NULL,
        signup_date DATE NOT NULL DEFAULT CURRENT_DATE
    );

    CREATE TABLE products (
        product_id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        price_cents INTEGER NOT NULL
    );

    CREATE TABLE orders (
        order_id SERIAL PRIMARY KEY,
        customer_id INTEGER NOT NULL REFERENCES customers(customer_id) ON DELETE CASCADE,
        product_id INTEGER NOT NULL REFERENCES products(product_id),
        status TEXT NOT NULL DEFAULT 'processing',
        created_at TIMESTAMP NOT NULL DEFAULT now()
    );

    INSERT INTO customers (name, email) VALUES
        ('Ava Thompson', 'ava.thompson@example.com'),
        ('Liam Chen', 'liam.chen@example.com'),
        ('Noah Patel', 'noah.patel@example.com'),
        ('Emma Garcia', 'emma.garcia@example.com'),
        ('Olivia Kim', 'olivia.kim@example.com');

    INSERT INTO products (name, category, price_cents) VALUES
        ('Wireless Mouse', 'accessories', 2499),
        ('USB-C Hub', 'accessories', 3999),
        ('Mechanical Keyboard', 'accessories', 8999),
        ('Webcam', 'accessories', 4999),
        ('Monitor Stand', 'accessories', 3499);

    INSERT INTO orders (customer_id, product_id, status) VALUES
        (1, 1, 'shipped'),
        (2, 2, 'processing'),
        (3, 3, 'delivered'),
        (4, 4, 'shipped'),
        (5, 5, 'cancelled');

    -- Tamper-evident audit trail (agent/audit_log.py) — OPA's decision_logs
    -- plugin POSTs every policy decision to POST /internal/audit-log, which
    -- hash-chains each row here. decided_at is TEXT, not TIMESTAMPTZ, on
    -- purpose: Postgres reformats timestamps on round-trip (different
    -- precision/offset display), which silently broke hash verification
    -- during testing — TEXT preserves the exact original string the hash
    -- was computed over.
    CREATE TABLE audit_log (
        id BIGSERIAL PRIMARY KEY,
        decision_id TEXT NOT NULL UNIQUE,
        opa_path TEXT NOT NULL,
        input JSONB NOT NULL,
        result JSONB NOT NULL,
        decided_at TEXT NOT NULL,
        ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        prev_hash TEXT NOT NULL,
        entry_hash TEXT NOT NULL
    );

    -- Used by the agent service. Can read all 3 tables, can UPDATE order
    -- status (billing_agent.issue_refund), can DELETE customers
    -- (admin_agent.purge_customer_data). Whether a given request is ever
    -- allowed to reach these grants at all is OPA's/AGT's job, checked
    -- BEFORE any SQL runs — this role is the ceiling, not the gate.
    CREATE ROLE agent_app LOGIN PASSWORD '${AGENT_APP_DB_PASSWORD}';
    GRANT SELECT ON customers, products, orders TO agent_app;
    GRANT UPDATE (status) ON orders TO agent_app;
    GRANT DELETE ON customers TO agent_app;
    GRANT SELECT, INSERT ON audit_log TO agent_app;
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO agent_app;

    -- Used by the dashboard's "Live Database" tab. Read-only, on purpose —
    -- a viewer has no business being able to write.
    CREATE ROLE dashboard_viewer LOGIN PASSWORD '${DASHBOARD_DB_PASSWORD}';
    GRANT SELECT ON customers, products, orders, audit_log TO dashboard_viewer;
EOSQL
