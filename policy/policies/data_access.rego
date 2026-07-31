package governance.data_access

# OPA Call 1 (Data Governance layer): asked which columns must be masked
# before a retrieval result reaches the model. The agent gets input.columns
# from OpenMetadata's real column tags (see data-governance/ingest_and_tag_pii.py).
#
# input:  {"columns": [{"name": "email", "tags": ["PII.Sensitive"]}, ...],
#          "requester_role": "demo-agent"}
# output: {"mask_columns": ["email"]}

mask_columns[name] {
    col := input.columns[_]
    col.tags[_] == "PII.Sensitive"
    name := col.name
}
