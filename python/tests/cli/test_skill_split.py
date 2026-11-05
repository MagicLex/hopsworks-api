"""The split hops-train section 1 gives agents to copy, run as written."""

import re
from pathlib import Path

import hopsworks
import numpy as np
import pandas as pd
import pytest


SKILL = Path(hopsworks.__file__).parent / "skills/ml/hops-train/SKILL.md"


@pytest.fixture(scope="module")
def split():
    blocks = re.findall(r"```python\n(.*?)```", SKILL.read_text(), flags=re.S)
    code = next(b for b in blocks if "def split_by_key" in b)
    names: dict = {}
    exec(compile(code, str(SKILL), "exec"), names)
    return names


def test_key_column_takes_the_key_or_its_exact_prefixed_form(split):
    key_column = split["key_column"]
    assert key_column(["id", "customer_id"], "id") == "id"
    assert key_column(["customer_id", "fw1_orders_1_id"], "id") == "fw1_orders_1_id"
    with pytest.raises(ValueError, match="customer_id"):
        key_column(["customer_id", "amount"], "id")
    with pytest.raises(ValueError, match="fw1_orders_1_id"):
        key_column(["id", "fw1_orders_1_id"], "id")


def test_key_bucket_normalises_the_key(split):
    key_bucket = split["key_bucket"]
    assert key_bucket(1) == key_bucket(1.0) == key_bucket(np.int64(1)) == key_bucket(np.float64(1.0))
    assert key_bucket(None) == key_bucket(float("nan")) == key_bucket(pd.NA) == key_bucket(pd.NaT)
    assert key_bucket(1.5) != key_bucket(1)


def test_split_by_key_keeps_an_entity_in_one_part_and_drops_every_key(split):
    ids = np.repeat(np.arange(400), 3).astype(float)  # 3 rows per entity, the key read as float
    X = pd.DataFrame(
        {
            "fw1_orders_1_order_id": ids,
            "customer_id": ids % 7,
            "fw1_orders_1_order_ts": pd.Timestamp("2024-01-01"),
            "amount": np.arange(len(ids)),
        }
    )
    y = pd.DataFrame({"returned": np.arange(len(ids)) % 2})
    parts = split["split_by_key"](X, y, key="order_id", drop=["order_id", "customer_id", "order_ts"])
    X_train, X_val, X_test, y_train, y_val, y_test = parts
    assert list(X_train.columns) == ["amount"]
    assert len(X_train) + len(X_val) + len(X_test) == len(X)
    assert (y_train.index == X_train.index).all()
    entity = X["fw1_orders_1_order_id"]
    seen = [set(entity[p.index]) for p in (X_train, X_val, X_test)]
    assert not (seen[0] & seen[1] or seen[0] & seen[2] or seen[1] & seen[2])
    again = split["split_by_key"](X.iloc[::-1], y.iloc[::-1], key="order_id", drop=["customer_id"])
    assert set(again[2].index) == set(X_test.index)


def test_a_missing_key_splits_the_same_from_a_nullable_int_or_a_float_read(split):
    ids = [1, 2, None, 4] * 50
    as_int = pd.DataFrame({"order_id": pd.array(ids, dtype="Int64"), "amount": range(200)})
    as_float = pd.DataFrame({"order_id": pd.array(ids, dtype="float64"), "amount": range(200)})
    y = pd.DataFrame({"returned": [0, 1] * 100})
    key_bucket = split["key_bucket"]
    assert as_int["order_id"].map(key_bucket).tolist() == as_float["order_id"].map(key_bucket).tolist()
    a = split["split_by_key"](as_int, y, key="order_id")
    b = split["split_by_key"](as_float, y, key="order_id")
    for part_a, part_b in zip(a[:3], b[:3]):
        assert list(part_a.index) == list(part_b.index)


def test_split_by_key_takes_the_plain_key_when_a_join_repeats_it(split):
    ids = np.repeat(np.arange(300), 2)
    X = pd.DataFrame({"customer_id": ids, "fw1_features_1_customer_id": ids.astype(float), "logins": np.arange(600.0)})
    y = pd.Series(np.arange(600) % 2)
    X_train, X_val, X_test, *_ = split["split_by_key"](X, y, "customer_id")
    assert list(X_train.columns) == ["logins"]
    parts = [set(ids[f.index]) for f in (X_train, X_val, X_test)]
    assert not (parts[0] & parts[1]) and not (parts[0] & parts[2]) and not (parts[1] & parts[2])


def test_split_by_key_still_refuses_a_prefixed_key_of_another_entity(split):
    X = pd.DataFrame({"id": [1, 2, 3], "fw1_orders_1_id": [10, 20, 30], "amount": [1.0, 2.0, 3.0]})
    with pytest.raises(ValueError, match="fw1_orders_1_id"):
        split["split_by_key"](X, pd.Series([0, 1, 0]), "id")
