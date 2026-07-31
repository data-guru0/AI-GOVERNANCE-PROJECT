package governance.data_access

test_pii_tagged_column_is_masked {
    r := mask_columns with input as {"columns": [
        {"name": "email", "tags": ["PII.Sensitive"]},
        {"name": "order_id", "tags": []},
    ]}
    r == {"email"}
}

test_no_tagged_columns_means_nothing_masked {
    r := mask_columns with input as {"columns": [{"name": "order_id", "tags": []}]}
    count(r) == 0
}

test_multiple_tagged_columns_all_masked {
    r := mask_columns with input as {"columns": [
        {"name": "email", "tags": ["PII.Sensitive"]},
        {"name": "ssn", "tags": ["PII.Sensitive"]},
        {"name": "status", "tags": []},
    ]}
    r == {"email", "ssn"}
}
