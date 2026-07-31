"""Tamper-evident audit trail. Real gap this closes: the original build
wrote OPA's decision log to a plain text file via `tee` — anyone with
write access to that container could edit it with no way to detect it.

OPA's own `decision_logs` plugin supports pushing to an HTTP service
instead of (or as well as) console output (verified empirically — it
gzip-compresses a JSON array of decision entries and POSTs it). This
module is the receiving end: `/internal/audit-log` in main.py calls
`ingest()` with the raw request body, which decompresses it and inserts
each entry into Postgres's `audit_log` table with a hash chain — each
row's `entry_hash` covers its own content AND the previous row's hash, so
altering or deleting any past row breaks every hash after it, detectable
by `verify_chain()`. This is the same construction (not the same tech) as
what makes a blockchain's ledger tamper-evident: a linked hash chain, not
a trusted third party.
"""
import gzip
import hashlib
import json

from agent.db import execute, fetch_all, fetch_one


def _entry_hash(prev_hash: str, decision_id: str, opa_path: str, input_doc: dict, result: object, decided_at: str) -> str:
    canonical = json.dumps(
        {"prev_hash": prev_hash, "decision_id": decision_id, "path": opa_path, "input": input_doc, "result": result, "decided_at": decided_at},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _last_hash() -> str:
    row = fetch_one("SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1")
    return row["entry_hash"] if row else "0" * 64  # genesis hash


def ingest(raw_body: bytes) -> int:
    """Decompresses OPA's decision-log payload and inserts each entry,
    hash-chained to whatever's already there. Returns the number ingested."""
    try:
        entries = json.loads(gzip.decompress(raw_body))
    except OSError:
        entries = json.loads(raw_body)  # OPA only gzips above a size threshold

    count = 0
    for entry in entries:
        decision_id = entry.get("decision_id")
        if not decision_id:
            continue
        existing = fetch_one("SELECT 1 FROM audit_log WHERE decision_id = :id", {"id": decision_id})
        if existing:
            continue  # OPA retries on transient failures; don't double-chain a re-delivery
        prev_hash = _last_hash()
        decided_at = entry.get("timestamp", "")
        opa_path = entry.get("path", "")
        input_doc = entry.get("input", {})
        result = entry.get("result")
        entry_hash = _entry_hash(prev_hash, decision_id, opa_path, input_doc, result, decided_at)
        execute(
            """INSERT INTO audit_log (decision_id, opa_path, input, result, decided_at, prev_hash, entry_hash)
               VALUES (:decision_id, :opa_path, :input, :result, :decided_at, :prev_hash, :entry_hash)""",
            {
                "decision_id": decision_id,
                "opa_path": opa_path,
                "input": json.dumps(input_doc),
                "result": json.dumps(result),
                "decided_at": decided_at,
                "prev_hash": prev_hash,
                "entry_hash": entry_hash,
            },
        )
        count += 1
    return count


def verify_chain() -> tuple[bool, str]:
    """Recomputes every entry's hash from its stored content and confirms
    it matches both what's stored AND the next row's prev_hash — proof the
    log hasn't been edited or had rows removed since ingestion."""
    rows = fetch_all("SELECT * FROM audit_log ORDER BY id ASC")
    if not rows:
        return True, "audit_log is empty — nothing to verify yet"
    expected_prev = "0" * 64
    for row in rows:
        recomputed = _entry_hash(expected_prev, row["decision_id"], row["opa_path"], row["input"], row["result"], str(row["decided_at"]))
        if row["prev_hash"] != expected_prev:
            return False, f"entry {row['id']} ({row['decision_id']}): prev_hash doesn't chain to the prior entry — a row was likely deleted or reordered"
        if recomputed != row["entry_hash"]:
            return False, f"entry {row['id']} ({row['decision_id']}): stored hash doesn't match its content — the row was likely edited"
        expected_prev = row["entry_hash"]
    return True, f"{len(rows)} entries verified, hash chain intact"
