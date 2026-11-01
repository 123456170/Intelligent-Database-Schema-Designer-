from __future__ import annotations

import hashlib
import io
import json
import random
import re
import sqlite3
import time
import zipfile
from datetime import datetime, timedelta, timezone
from itertools import combinations
from typing import Any

import pandas as pd
import streamlit as st


APP_NAME = "Intelligent Database Schema Designer — Live Demo"
SCHEMA_VERSION = "0.1.0"

EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
PHONE_RE = re.compile(r"^\+?\d[\d\s().-]{6,}\d$")
ID_RE = re.compile(r"(^|_)(id|code|key)(_|$)|_id$", re.IGNORECASE)
ID_VALUE_RE = re.compile(r"^[A-Za-z]{2,10}-\d{1,12}$")
DELIMITERS = [";", ",", "|"]
BOOLEAN_VALUES = {"true", "false", "yes", "no", "1", "0"}

COUNTRIES = {
    "United States",
    "Canada",
    "United Kingdom",
    "Germany",
    "France",
    "India",
    "Japan",
    "Australia",
    "Brazil",
    "Mexico",
}

CITY_BY_COUNTRY = {
    "United States": ["New York", "Austin", "Seattle", "Denver"],
    "Canada": ["Toronto", "Vancouver", "Montreal"],
    "United Kingdom": ["London", "Manchester", "Edinburgh"],
    "Germany": ["Berlin", "Munich", "Hamburg"],
    "France": ["Paris", "Lyon", "Marseille"],
    "India": ["Bengaluru", "Mumbai", "Delhi"],
    "Japan": ["Tokyo", "Osaka", "Kyoto"],
    "Australia": ["Sydney", "Melbourne", "Perth"],
    "Brazil": ["Sao Paulo", "Rio de Janeiro"],
    "Mexico": ["Mexico City", "Monterrey"],
}

FIRST_NAMES = [
    "Ava", "Liam", "Noah", "Emma", "Olivia", "Sophia", "Mason", "Ethan",
    "Isabella", "Mia", "Arjun", "Priya", "Hiro", "Yuki", "Luis", "Ana"
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Martinez", "Patel", "Kim", "Nguyen", "Muller", "Dubois"
]

PRODUCT_NOUNS = [
    "Widget", "Sensor", "Adapter", "Module", "Controller", "Router",
    "Display", "Battery", "Cable", "Mount", "Filter", "Pump"
]

CATEGORIES = [
    "Electronics", "Industrial", "Networking", "Power", "Accessories",
    "Sensors", "Compute", "Logistics"
]

TAG_OPTIONS = [
    "urgent", "fragile", "gift", "backorder", "international",
    "priority", "regulated", "hazmat"
]

NOTES_TEMPLATES = [
    "Customer requested delivery to {city}",
    "Replacement for {product}",
    "Priority handling requested",
    "Warehouse transfer pending",
    "",
]

NULLABLE_COLUMNS = [
    "customer_email",
    "customer_phone",
    "tags",
    "notes",
    "ship_city",
]

COLUMNS = [
    "order_id",
    "line_number",
    "order_date",
    "customer_id",
    "customer_name",
    "customer_email",
    "customer_country",
    "customer_phone",
    "product_id",
    "product_name",
    "product_category",
    "unit_price",
    "quantity",
    "discount_pct",
    "line_total",
    "ship_city",
    "ship_country",
    "tags",
    "notes",
    "source_system",
    "ingested_at",
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def stable_hash_int(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16) & 0xFFFFFFFF


def df_fingerprint(df: pd.DataFrame) -> str:
    return sha256_text(df.to_csv(index=False))


def clean_notnull(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return series.notna()
    cleaned = series.astype(str).str.strip()
    return series.notna() & (cleaned != "") & (~cleaned.str.lower().isin(["nan", "none", "null"]))


def unique_preserve(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def city_for(country: str) -> str:
    cities = CITY_BY_COUNTRY.get(country, ["Springfield"])
    return random.Random(stable_hash_int(country)).choice(cities)


def table_name_from_id(col: str) -> str:
    base = col.replace("_id", "").strip()
    if base == "order":
        return "orders"
    if base == "customer":
        return "customers"
    if base == "product":
        return "products"
    return f"{base}s"


def set_progress(bar: Any, value: float, text: str) -> None:
    """
    Streamlit progress values must be between 0.0 and 1.0 in current versions.
    This helper prevents invalid values such as 100.0 from crashing the app.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = 0.0

    if v < 0.0:
        v = 0.0
    elif v > 1.0:
        v = 1.0

    bar.progress(v, text=text)


def generate_demo_data(
    target_rows: int,
    seed: int,
    missing_rate: float,
    duplicate_rate: float,
    unicode_rate: float,
) -> pd.DataFrame:
    rng = random.Random(seed)
    target_rows = max(50, int(target_rows))

    num_customers = max(8, target_rows // 12)
    num_products = max(10, target_rows // 20)

    customers = []
    for i in range(num_customers):
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)
        country = rng.choice(sorted(COUNTRIES))
        customers.append(
            {
                "customer_id": f"CUST-{i + 1:05d}",
                "customer_name": f"{first} {last}",
                "customer_email": f"{first.lower()}.{last.lower()}@example.com",
                "customer_country": country,
                "customer_phone": f"+1-555-{rng.randint(100, 999)}-{rng.randint(1000, 9999)}",
            }
        )

    products = []
    for i in range(num_products):
        noun = rng.choice(PRODUCT_NOUNS)
        category = rng.choice(CATEGORIES)
        products.append(
            {
                "product_id": f"PROD-{i + 1:05d}",
                "product_name": f"{noun} {chr(65 + (i % 26))}{i + 1:03d}",
                "product_category": category,
                "unit_price": round(rng.uniform(5.0, 500.0), 2),
            }
        )

    start_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
    base_rows: list[dict[str, Any]] = []
    order_seq = 0

    while len(base_rows) < target_rows:
        order_seq += 1
        cust = rng.choice(customers)
        prod_choices = [rng.choice(products) for _ in range(rng.randint(1, 4))]

        order_dt = start_date + timedelta(
            days=rng.randint(0, 300),
            hours=rng.randint(0, 23),
            minutes=rng.randint(0, 59),
        )
        ship_country = cust["customer_country"] if rng.random() < 0.8 else rng.choice(sorted(COUNTRIES))
        ship_city = city_for(ship_country)

        for line_no, prod in enumerate(prod_choices, start=1):
            qty = rng.randint(1, 5)
            discount = rng.choice([0.0, 0.0, 5.0, 10.0, 15.0, 20.0])
            unit_price = float(prod["unit_price"])
            line_total = round(qty * unit_price * (1.0 - discount / 100.0), 2)

            if rng.random() < 0.04:
                line_total = round(line_total * rng.uniform(0.85, 1.15), 2)

            tag_count = rng.randint(1, 3) if rng.random() < 0.55 else 0
            tags = ";".join(rng.sample(TAG_OPTIONS, tag_count)) if tag_count else ""

            notes = rng.choice(NOTES_TEMPLATES).format(product=prod["product_name"], city=ship_city)

            row = {
                "order_id": f"ORD-{order_seq:06d}",
                "line_number": line_no,
                "order_date": order_dt.isoformat(),
                "customer_id": cust["customer_id"],
                "customer_name": cust["customer_name"],
                "customer_email": cust["customer_email"],
                "customer_country": cust["customer_country"],
                "customer_phone": cust["customer_phone"],
                "product_id": prod["product_id"],
                "product_name": prod["product_name"],
                "product_category": prod["product_category"],
                "unit_price": unit_price,
                "quantity": qty,
                "discount_pct": discount,
                "line_total": line_total,
                "ship_city": ship_city,
                "ship_country": ship_country,
                "tags": tags,
                "notes": notes,
                "source_system": rng.choice(["ERP", "WEB", "MOBILE", "PARTNER"]),
                "ingested_at": (order_dt + timedelta(hours=rng.randint(1, 48))).isoformat(),
            }

            if rng.random() < 0.20:
                row["customer_email"] = str(row["customer_email"]).upper()

            if rng.random() < unicode_rate:
                row["customer_name"] = f"{row['customer_name']} é"
                row["notes"] = f"{row['notes']} 🚚"

            if rng.random() < missing_rate:
                col = rng.choice(NULLABLE_COLUMNS)
                row[col] = None if rng.random() < 0.5 else ""

            base_rows.append(row)

    if duplicate_rate > 0 and base_rows:
        dup_count = int(len(base_rows) * duplicate_rate)
        for _ in range(dup_count):
            orig = rng.choice(base_rows)
            dup = orig.copy()
            dup["notes"] = f"{dup.get('notes', '')} duplicate".strip()
            base_rows.append(dup)

    df = pd.DataFrame(base_rows, columns=COLUMNS)
    return df


def infer_semantic_type(col_name: str, series: pd.Series) -> str:
    samples = series.dropna().astype(str).str.strip()
    samples = samples[samples != ""]
    if samples.empty:
        return "unknown"

    sample_list = samples.head(100).tolist()

    for d in DELIMITERS:
        token_counts = [len(re.split(re.escape(d), s)) for s in sample_list]
        avg_tokens = sum(token_counts) / len(token_counts)
        if avg_tokens > 1.15 and max(token_counts) > 1:
            return "repeated_group"

    email_ratio = sum(1 for s in sample_list if EMAIL_RE.fullmatch(s)) / len(sample_list)
    if email_ratio > 0.70:
        return "email"

    phone_ratio = sum(1 for s in sample_list if PHONE_RE.match(s)) / len(sample_list)
    if phone_ratio > 0.60:
        return "phone"

    numeric_vals = pd.to_numeric(samples, errors="coerce")
    numeric_ratio = float(numeric_vals.notna().mean())
    if numeric_ratio > 0.80:
        name_lower = col_name.lower()
        if any(k in name_lower for k in ["price", "amount", "total", "cost", "discount", "revenue"]):
            return "currency"
        return "numeric"

    if any(k in col_name.lower() for k in ["date", "time", "timestamp", "at"]):
        parsed = pd.to_datetime(samples, errors="coerce")
        if float(parsed.notna().mean()) > 0.70:
            return "datetime"

    country_values = {c.lower() for c in COUNTRIES}
    country_ratio = sum(1 for s in sample_list if s.lower() in country_values) / len(sample_list)
    if "country" in col_name.lower() or country_ratio > 0.70:
        return "country"

    if ID_RE.search(col_name):
        return "identifier"

    id_pattern_ratio = sum(1 for s in sample_list if ID_VALUE_RE.fullmatch(s)) / len(sample_list)
    if id_pattern_ratio > 0.80:
        return "identifier"

    lowered = {s.lower() for s in sample_list}
    if lowered and lowered <= BOOLEAN_VALUES:
        return "boolean"

    if samples.nunique() <= max(20, int(len(samples) * 0.05)):
        return "categorical"

    avg_len = sum(len(s) for s in sample_list) / len(sample_list)
    if avg_len > 20:
        return "text"

    return "categorical"


def profile_columns(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    n_rows = len(df)

    for col in df.columns:
        series = df[col]
        notnull_mask = clean_notnull(series)
        nn = int(notnull_mask.sum())
        valid = series[notnull_mask]

        unique_count = int(valid.astype(str).nunique()) if nn else 0
        null_percent = float(1.0 - (nn / n_rows)) if n_rows else 0.0
        unique_ratio = float(unique_count / nn) if nn else 0.0
        semantic_type = infer_semantic_type(col, series)

        min_value = None
        max_value = None
        mean_value = None
        avg_length = None

        if semantic_type in {"numeric", "currency"}:
            num = pd.to_numeric(valid, errors="coerce").dropna()
            if not num.empty:
                min_value = float(num.min())
                max_value = float(num.max())
                mean_value = float(num.mean())
        else:
            if not valid.empty:
                avg_length = float(valid.astype(str).str.len().mean())

        vc = valid.astype(str).value_counts().head(5)
        top_values = {str(k): int(v) for k, v in vc.items()}

        repeated_delimiter = None
        avg_tokens = 0.0
        if semantic_type == "repeated_group":
            best_avg = 0.0
            for d in DELIMITERS:
                tokens = valid.astype(str).apply(lambda s, delim=d: len(re.split(re.escape(delim), s)))
                avg = float(tokens.mean()) if len(tokens) else 0.0
                if avg > best_avg:
                    best_avg = avg
                    repeated_delimiter = d
            avg_tokens = best_avg

        rows.append(
            {
                "column": col,
                "dtype": str(series.dtype),
                "semantic_type": semantic_type,
                "non_null_count": nn,
                "null_percent": null_percent,
                "unique_count": unique_count,
                "unique_ratio": unique_ratio,
                "min_value": min_value,
                "max_value": max_value,
                "mean_value": mean_value,
                "avg_length": avg_length,
                "top_values": top_values,
                "repeated_delimiter": repeated_delimiter,
                "avg_tokens": avg_tokens,
            }
        )

    return pd.DataFrame(rows)


def candidate_keys(df: pd.DataFrame, profile_df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []

    profile = profile_df.set_index("column")
    known = [c for c in ["order_id", "line_number", "customer_id", "product_id"] if c in df.columns]
    identifier_cols = [c for c in df.columns if profile.loc[c, "semantic_type"] == "identifier"]
    keyish = unique_preserve(identifier_cols + known)
    keyish = [c for c in keyish if profile.loc[c, "non_null_count"] > 0]
    keyish = keyish[:8]

    results = []

    def add_key(cols: list[str]) -> None:
        mask = pd.Series(True, index=df.index)
        for c in cols:
            mask &= clean_notnull(df[c])
        sub = df.loc[mask, cols]
        total = len(sub)
        if total == 0:
            return

        unique = int(sub.astype(str).drop_duplicates().shape[0])
        completeness = float(total / len(df))
        uniqueness = float(unique / total)
        score = uniqueness * (0.7 + 0.3 * completeness)

        if completeness == 1.0 and uniqueness >= 0.999:
            kind = "primary_key_candidate"
        elif uniqueness >= 0.95:
            kind = "unique_key_candidate"
        else:
            kind = "near_key"

        results.append(
            {
                "key_columns": cols,
                "kind": kind,
                "uniqueness": uniqueness,
                "completeness": completeness,
                "score": score,
                "sample_values": sub.astype(str).drop_duplicates().head(3).to_dict("records"),
            }
        )

    for c in keyish:
        add_key([c])

    for r in range(2, min(3, len(keyish)) + 1):
        for combo in combinations(keyish, r):
            add_key(list(combo))

    results.sort(key=lambda x: (-x["score"], -x["uniqueness"], len(x["key_columns"])))
    return results[:25]


def compute_functional_dependencies(
    df: pd.DataFrame,
    profile_df: pd.DataFrame,
    confidence_threshold: float,
) -> list[dict[str, Any]]:
    if df.empty:
        return []

    profile = profile_df.set_index("column")
    max_det_card = min(1000, max(200, len(df) // 2))

    det_cols = [
        c
        for c in df.columns
        if profile.loc[c, "unique_count"] <= max_det_card
        and profile.loc[c, "semantic_type"] not in {"text", "datetime", "numeric", "currency", "unknown"}
    ]

    results = []

    for det in det_cols:
        for dep in df.columns:
            if dep == det:
                continue
            if profile.loc[dep, "non_null_count"] < 5:
                continue

            mask = clean_notnull(df[det]) & clean_notnull(df[dep])
            tmp = df.loc[mask, [det, dep]].copy()
            if len(tmp) < 10:
                continue

            tmp[det] = tmp[det].astype(str)
            grouped = tmp.groupby(det, dropna=True)[dep].nunique()
            support_groups = int(len(grouped))
            if support_groups < 5:
                continue

            deterministic_groups = int((grouped == 1).sum())
            confidence = float(deterministic_groups / support_groups)
            violations = int((grouped - 1).clip(lower=0).sum())

            if confidence >= confidence_threshold:
                evidence = []
                bad_groups = grouped[grouped > 1].head(3).index.tolist()
                for bg in bad_groups:
                    dep_values = tmp.loc[tmp[det] == bg, dep].dropna().astype(str).unique().tolist()[:5]
                    evidence.append({"determinant_value": str(bg), "dependent_values": dep_values})

                if not evidence:
                    evidence = tmp.head(3).astype(str).to_dict("records")

                results.append(
                    {
                        "determinant": det,
                        "dependent": dep,
                        "confidence": confidence,
                        "support_groups": support_groups,
                        "support_rows": int(len(tmp)),
                        "violations": violations,
                        "evidence": evidence,
                    }
                )

    results.sort(key=lambda x: (-x["confidence"], -x["support_rows"]))
    return results[:100]


def foreign_key_candidates(
    df: pd.DataFrame,
    profile_df: pd.DataFrame,
    fds: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if df.empty:
        return []

    profile = profile_df.set_index("column")
    fd_attrs: dict[str, set[str]] = {}

    for fd in fds:
        det = fd["determinant"]
        dep = fd["dependent"]
        if det in profile.index and profile.loc[det, "semantic_type"] == "identifier":
            fd_attrs.setdefault(det, set()).add(dep)

    candidates = []

    for id_col in ["customer_id", "product_id", "order_id"]:
        if id_col not in df.columns:
            continue

        attrs = sorted(a for a in fd_attrs.get(id_col, set()) if a != id_col)
        parent_table = table_name_from_id(id_col)

        if id_col == "customer_id":
            child_table = "orders"
        elif id_col == "product_id":
            child_table = "order_items"
        elif id_col == "order_id":
            child_table = "order_items"
        else:
            child_table = f"fact_{parent_table}"

        containment = float(clean_notnull(df[id_col]).mean())
        confidence = min(1.0, 0.55 + 0.35 * containment + 0.05 * min(len(attrs), 5))

        candidates.append(
            {
                "child_table": child_table,
                "child_column": id_col,
                "parent_table": parent_table,
                "parent_column": id_col,
                "containment": containment,
                "confidence": confidence,
                "determined_attributes": attrs[:12],
                "evidence": f"{len(attrs)} attributes show strong sample-level dependency on {id_col}.",
            }
        )

    fallback = [
        ("customer_id", "orders", "customers"),
        ("product_id", "order_items", "products"),
        ("order_id", "order_items", "orders"),
    ]
    for id_col, child_table, parent_table in fallback:
        if id_col not in df.columns:
            continue
        if any(c["child_table"] == child_table and c["child_column"] == id_col for c in candidates):
            continue
        containment = float(clean_notnull(df[id_col]).mean())
        candidates.append(
            {
                "child_table": child_table,
                "child_column": id_col,
                "parent_table": parent_table,
                "parent_column": id_col,
                "containment": containment,
                "confidence": 0.65,
                "determined_attributes": [],
                "evidence": "Naming convention and identifier semantics suggest a dimension reference.",
            }
        )

    candidates.sort(key=lambda x: -x["confidence"])
    return candidates


def detect_repeated_groups(profile_df: pd.DataFrame, columns: list[str]) -> list[dict[str, Any]]:
    repeats = profile_df[profile_df["semantic_type"] == "repeated_group"]
    owner = "order_id" if "order_id" in columns else None
    suggestions = []

    for _, row in repeats.iterrows():
        col = row["column"]
        if owner is None:
            child_table = f"row_{col}"
            owner_key = "row_id"
        else:
            base = owner.replace("_id", "")
            child_table = f"{base}_tags" if "tag" in col.lower() else f"{base}_{col}"
            owner_key = owner

        child_column = "tag" if "tag" in col.lower() else col
        confidence = 0.95 if float(row["avg_tokens"]) > 1.5 else 0.80

        suggestions.append(
            {
                "source_column": col,
                "owner_key": owner_key,
                "child_table": child_table,
                "child_column": child_column,
                "delimiter": row["repeated_delimiter"] or ";",
                "avg_tokens": float(row["avg_tokens"]),
                "confidence": confidence,
                "rationale": "Column contains multiple atomic tokens and violates First Normal Form.",
            }
        )

    return suggestions


def normalization_advisor(
    df: pd.DataFrame,
    profile_df: pd.DataFrame,
    keys: list[dict[str, Any]],
    fds: list[dict[str, Any]],
    fks: list[dict[str, Any]],
    repeats: list[dict[str, Any]],
) -> dict[str, Any]:
    profile = profile_df.set_index("column")
    fd_attrs: dict[str, set[str]] = {}

    for fd in fds:
        fd_attrs.setdefault(fd["determinant"], set()).add(fd["dependent"])

    tables = []
    line_item_cols = {"line_number", "quantity", "discount_pct", "line_total"}

    for id_col in ["customer_id", "product_id", "order_id"]:
        if id_col not in df.columns:
            continue

        attrs = sorted(a for a in fd_attrs.get(id_col, set()) if a != id_col)

        if id_col == "customer_id":
            fallback = [c for c in df.columns if c.startswith("customer_") and c != id_col]
        elif id_col == "product_id":
            fallback = [c for c in df.columns if c.startswith("product_") and c != id_col]
            if "unit_price" in df.columns:
                fallback.append("unit_price")
        else:
            fallback = [
                c
                for c in ["order_date", "customer_id", "ship_city", "ship_country", "source_system", "ingested_at"]
                if c in df.columns
            ]

        cols = unique_preserve([id_col] + attrs + fallback)
        cols = [c for c in cols if c not in line_item_cols]

        tables.append(
            {
                "name": table_name_from_id(id_col),
                "primary_key": [id_col],
                "columns": cols,
                "basis": f"Sample functional dependencies centered on {id_col}",
                "normal_form": "3NF candidate",
            }
        )

    fact_cols = [
        c
        for c in ["order_id", "line_number", "product_id", "quantity", "discount_pct", "line_total"]
        if c in df.columns
    ]
    if "order_id" in fact_cols and "line_number" in fact_cols:
        tables.append(
            {
                "name": "order_items",
                "primary_key": ["order_id", "line_number"],
                "columns": fact_cols,
                "basis": "Repeated order-line grain detected from composite key candidates",
                "normal_form": "Fact table",
            }
        )

    for rep in repeats:
        if rep["owner_key"] == "order_id":
            tables.append(
                {
                    "name": rep["child_table"],
                    "primary_key": ["order_id", rep["child_column"]],
                    "columns": ["order_id", rep["child_column"]],
                    "basis": rep["rationale"],
                    "normal_form": "1NF repair",
                    "source_column": rep["source_column"],
                }
            )

    alternatives = [
        {
            "rank": 1,
            "name": "Conservative 1NF cleanup",
            "description": "Split repeated groups only and keep a wide fact table with a surrogate item key.",
            "tradeoffs": "Low migration risk, but redundancy and update anomalies remain.",
            "recommended": False,
        },
        {
            "rank": 2,
            "name": "Normalized 3NF / star-like design",
            "description": "Separate customers, products, orders, order_items, and order_tags.",
            "tradeoffs": "Stronger integrity and less redundancy; more joins for analytical queries.",
            "recommended": True,
        },
        {
            "rank": 3,
            "name": "BCNF-oriented decomposition",
            "description": "Further split order-level shipping/address entities if additional dependencies are confirmed.",
            "tradeoffs": "Maximum dependency purity, but highest modeling and query complexity.",
            "recommended": False,
        },
    ]

    table_names = {t["name"] for t in tables}
    table_cols = {t["name"]: set(t["columns"]) for t in tables}

    indexes = []
    if "orders" in table_names and "customer_id" in table_cols["orders"]:
        indexes.append({"table": "orders", "columns": ["customer_id"], "reason": "Foreign key lookup"})
    if "order_items" in table_names:
        if "product_id" in table_cols["order_items"]:
            indexes.append({"table": "order_items", "columns": ["product_id"], "reason": "Foreign key lookup"})
        if "order_id" in table_cols["order_items"]:
            indexes.append({"table": "order_items", "columns": ["order_id"], "reason": "Parent order joins"})
    if "order_tags" in table_names:
        indexes.append({"table": "order_tags", "columns": ["order_id"], "reason": "Parent order joins"})
    if "customers" in table_names and "customer_country" in table_cols["customers"]:
        indexes.append({"table": "customers", "columns": ["customer_country"], "reason": "Filtering/grouping"})
    if "products" in table_names and "product_category" in table_cols["products"]:
        indexes.append({"table": "products", "columns": ["product_category"], "reason": "Filtering/grouping"})
    if "orders" in table_names and "order_date" in table_cols["orders"]:
        indexes.append({"table": "orders", "columns": ["order_date"], "reason": "Time-range queries"})

    seen_indexes = {(idx["table"], tuple(idx["columns"])) for idx in indexes}
    for t in tables:
        if t["name"] != "orders" and "order_id" in t["columns"]:
            key = (t["name"], ("order_id",))
            if key not in seen_indexes:
                indexes.append({"table": t["name"], "columns": ["order_id"], "reason": "Parent order joins"})
                seen_indexes.add(key)

    checks = []
    if "order_items" in table_names:
        checks.extend(
            [
                {"table": "order_items", "expression": "quantity > 0", "reason": "Prevent non-positive quantities"},
                {"table": "order_items", "expression": "discount_pct BETWEEN 0 AND 100", "reason": "Keep discount as percentage"},
                {"table": "order_items", "expression": "line_total >= 0", "reason": "Prevent negative line totals"},
            ]
        )

    return {
        "recommended_tables": tables,
        "alternatives": alternatives,
        "indexes": indexes,
        "checks": checks,
        "key_summary": keys[:5],
        "fk_summary": fks[:10],
    }


def sql_type(col: str, semantic: str, dialect: str) -> str:
    name = col.lower()

    if dialect == "sqlite":
        if name in {"quantity", "line_number"}:
            return "INTEGER"
        if semantic == "currency" or any(k in name for k in ["price", "amount", "total", "cost", "discount"]):
            return "REAL"
        if semantic == "numeric":
            return "INTEGER" if any(k in name for k in ["count", "qty", "number"]) else "REAL"
        if semantic == "datetime":
            return "TEXT"
        if semantic == "boolean":
            return "INTEGER"
        return "TEXT"

    if name in {"quantity", "line_number"}:
        return "INTEGER"
    if name == "discount_pct":
        return "NUMERIC(5,2)"
    if semantic == "currency" or any(k in name for k in ["price", "amount", "total", "cost"]):
        return "NUMERIC(12,2)"
    if semantic == "numeric":
        return "INTEGER" if any(k in name for k in ["count", "qty", "number"]) else "NUMERIC(18,6)"
    if semantic == "datetime":
        return "TIMESTAMPTZ"
    if semantic == "boolean":
        return "BOOLEAN"
    return "TEXT"


def q(name: str) -> str:
    return f'"{name}"'


def generate_ddl(advisor: dict[str, Any], profile_df: pd.DataFrame, dialect: str) -> tuple[str, str]:
    profile = profile_df.set_index("column")
    default_profile = {"semantic_type": "text", "null_percent": 0.0}
    table_names = {t["name"] for t in advisor["recommended_tables"]}
    lines = []

    if dialect == "sqlite":
        lines.append("PRAGMA foreign_keys = ON;")

    for table in advisor["recommended_tables"]:
        col_defs = []

        for c in table["columns"]:
            prow = profile.loc[c] if c in profile.index else default_profile
            typ = sql_type(c, str(prow.get("semantic_type", "text")), dialect)
            not_null = ""

            if c in table["primary_key"]:
                not_null = " NOT NULL"
            elif float(prow.get("null_percent", 1.0)) == 0.0 and c in {"customer_id", "product_id", "order_id", "line_number"}:
                not_null = " NOT NULL"

            col_defs.append(f"  {q(c)} {typ}{not_null}")

        pk = ", ".join(q(c) for c in table["primary_key"])
        col_defs.append(f"  PRIMARY KEY ({pk})")

        if table["name"] == "orders" and "customer_id" in table["columns"] and "customers" in table_names:
            col_defs.append(f"  FOREIGN KEY ({q('customer_id')}) REFERENCES {q('customers')} ({q('customer_id')})")

        if table["name"] == "order_items" and "product_id" in table["columns"] and "products" in table_names:
            col_defs.append(f"  FOREIGN KEY ({q('product_id')}) REFERENCES {q('products')} ({q('product_id')})")

        if table["name"] != "orders" and "order_id" in table["columns"] and "orders" in table_names:
            col_defs.append(f"  FOREIGN KEY ({q('order_id')}) REFERENCES {q('orders')} ({q('order_id')})")

        for chk in advisor.get("checks", []):
            if chk["table"] == table["name"]:
                col_defs.append(f"  CHECK ({chk['expression']})")

        lines.append(f"CREATE TABLE {q(table['name'])} (\n" + ",\n".join(col_defs) + "\n);")

    for idx in advisor.get("indexes", []):
        if idx["table"] not in table_names:
            continue
        idx_name = "idx_" + idx["table"] + "_" + "_".join(idx["columns"])
        cols = ", ".join(q(c) for c in idx["columns"])
        lines.append(f"CREATE INDEX IF NOT EXISTS {q(idx_name)} ON {q(idx['table'])} ({cols});")

    up = "\n\n".join(lines)
    down = "\n".join(
        f"DROP TABLE IF EXISTS {q(t['name'])};"
        for t in reversed(advisor["recommended_tables"])
    )
    return up, down


def prepare_normalized_frames(df: pd.DataFrame, advisor: dict[str, Any]) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    table_by_name = {t["name"]: t for t in advisor["recommended_tables"]}

    for name, table in table_by_name.items():
        cols = table["columns"]
        pk = table["primary_key"]

        if name == "order_items":
            base = df.dropna(subset=["order_id", "line_number"]).copy()
            base = base.sort_values(["order_id", "line_number"]).drop_duplicates(["order_id", "line_number"], keep="first")
            for c in ["quantity", "discount_pct", "line_total"]:
                if c in base.columns:
                    base[c] = pd.to_numeric(base[c], errors="coerce")
            frames[name] = base.reindex(columns=cols)

        elif "source_column" in table:
            source_col = str(table["source_column"])
            owner_key = pk[0] if pk else "row_id"
            child_col = pk[1] if len(pk) > 1 else "value"

            rows = []
            if source_col in df.columns and owner_key in df.columns:
                for oid, val in df[[owner_key, source_col]].dropna().itertuples(index=False):
                    if pd.isna(val):
                        continue
                    for token in re.split(r"[;,|]", str(val)):
                        token = token.strip()
                        if token:
                            rows.append({owner_key: oid, child_col: token})

            child_df = (
                pd.DataFrame(rows, columns=[owner_key, child_col]).drop_duplicates()
                if rows
                else pd.DataFrame(columns=[owner_key, child_col])
            )
            frames[name] = child_df.reindex(columns=cols)

        else:
            id_col = pk[0]
            attr_cols = [c for c in cols if c != id_col]
            sub = df.dropna(subset=[id_col]).copy()
            if "ingested_at" in sub.columns:
                sub = sub.sort_values("ingested_at")

            if sub.empty:
                frame = pd.DataFrame(columns=cols)
            else:
                grouped = sub.groupby(id_col, as_index=False).first()
                for c in attr_cols:
                    if c not in grouped.columns:
                        grouped[c] = None
                frame = grouped.reindex(columns=cols)

            frames[name] = frame

    def backfill_parent(parent_table: str, key_col: str, missing_ids: set[Any]) -> None:
        if parent_table not in table_by_name or not missing_ids:
            return

        cols = table_by_name[parent_table]["columns"]
        pk = table_by_name[parent_table]["primary_key"]

        if key_col not in df.columns:
            add = pd.DataFrame({key_col: list(missing_ids)})
        else:
            src = df[df[key_col].isin(missing_ids)].copy()
            if src.empty:
                add = pd.DataFrame({key_col: list(missing_ids)})
            else:
                if "ingested_at" in src.columns:
                    src = src.sort_values("ingested_at")
                add = src.groupby(key_col, as_index=False).first()

        existing = frames.get(parent_table, pd.DataFrame(columns=cols))
        frames[parent_table] = (
            pd.concat([existing, add], ignore_index=True)
            .reindex(columns=cols)
            .drop_duplicates(subset=pk, keep="first")
        )

    if "orders" in frames and "order_items" in frames:
        missing = set(frames["order_items"]["order_id"].dropna()) - set(frames["orders"]["order_id"].dropna())
        backfill_parent("orders", "order_id", missing)

    if "products" in frames and "order_items" in frames:
        missing = set(frames["order_items"]["product_id"].dropna()) - set(frames["products"]["product_id"].dropna())
        backfill_parent("products", "product_id", missing)

    if "customers" in frames and "orders" in frames and "customer_id" in frames["orders"].columns:
        missing = set(frames["orders"]["customer_id"].dropna()) - set(frames["customers"]["customer_id"].dropna())
        backfill_parent("customers", "customer_id", missing)

    if "order_tags" in frames and "orders" in frames:
        missing = set(frames["order_tags"]["order_id"].dropna()) - set(frames["orders"]["order_id"].dropna())
        backfill_parent("orders", "order_id", missing)

    return frames


def run_sqlite_dry_run(
    frames: dict[str, pd.DataFrame],
    ddl_up: str,
    advisor: dict[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ddl_executed": False,
        "validation_passed": False,
        "inserted_rows": {},
        "table_counts": {},
        "primary_key_duplicates": {},
        "orphan_counts": {},
        "line_total_mismatches": None,
        "error": None,
    }

    conn = None
    try:
        conn = sqlite3.connect(":memory:")
        cur = conn.cursor()
        cur.executescript(ddl_up)
        result["ddl_executed"] = True

        for name, frame in frames.items():
            if frame.empty:
                result["inserted_rows"][name] = 0
                continue

            clean = frame.copy()
            for c in clean.columns:
                if pd.api.types.is_datetime64_any_dtype(clean[c]):
                    clean[c] = clean[c].astype(str)

            clean = clean.where(pd.notnull(clean), None)
            data = clean.values.tolist()

            cols = ", ".join(q(c) for c in clean.columns)
            ph = ", ".join("?" for _ in clean.columns)
            cur.executemany(f"INSERT INTO {q(name)} ({cols}) VALUES ({ph})", data)
            result["inserted_rows"][name] = len(frame)

        conn.commit()

        for name in frames:
            result["table_counts"][name] = int(cur.execute(f"SELECT COUNT(*) FROM {q(name)}").fetchone()[0])

        for table in advisor["recommended_tables"]:
            tname = table["name"]
            if tname not in frames:
                continue
            pk = table["primary_key"]
            group_cols = ", ".join(q(c) for c in pk)
            sql = (
                f"SELECT COUNT(*) FROM ("
                f"SELECT {group_cols} FROM {q(tname)} "
                f"GROUP BY {group_cols} HAVING COUNT(*) > 1"
                f")"
            )
            result["primary_key_duplicates"][tname] = int(cur.execute(sql).fetchone()[0])

        fk_checks = [
            ("order_items", "order_id", "orders", "order_id"),
            ("order_items", "product_id", "products", "product_id"),
            ("orders", "customer_id", "customers", "customer_id"),
            ("order_tags", "order_id", "orders", "order_id"),
        ]

        for child, child_col, parent, parent_col in fk_checks:
            if child in frames and parent in frames:
                sql = (
                    f"SELECT COUNT(*) FROM {q(child)} AS c "
                    f"LEFT JOIN {q(parent)} AS p ON c.{q(child_col)} = p.{q(parent_col)} "
                    f"WHERE p.{q(parent_col)} IS NULL AND c.{q(child_col)} IS NOT NULL"
                )
                result["orphan_counts"][f"{child}->{parent}"] = int(cur.execute(sql).fetchone()[0])

        if "order_items" in frames and "products" in frames:
            sql = (
                "SELECT COUNT(*) FROM " + q("order_items") + " AS oi "
                "JOIN " + q("products") + " AS p ON oi." + q("product_id") + " = p." + q("product_id") + " "
                "WHERE oi." + q("quantity") + " IS NOT NULL "
                "AND oi." + q("discount_pct") + " IS NOT NULL "
                "AND oi." + q("line_total") + " IS NOT NULL "
                "AND p." + q("unit_price") + " IS NOT NULL "
                "AND ABS(oi." + q("line_total") + " - ("
                "CAST(oi." + q("quantity") + " AS REAL) * CAST(p." + q("unit_price") + " AS REAL) * "
                "(1.0 - CAST(oi." + q("discount_pct") + " AS REAL) / 100.0)"
                ")) > 0.05"
            )
            result["line_total_mismatches"] = int(cur.execute(sql).fetchone()[0])

        result["validation_passed"] = (
            all(v == 0 for v in result["primary_key_duplicates"].values())
            and all(v == 0 for v in result["orphan_counts"].values())
        )

    except Exception as exc:
        result["error"] = str(exc)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    return result


def run_pipeline(params: dict[str, Any]) -> dict[str, Any]:
    logs: list[dict[str, str]] = []

    def log(event: str, detail: str = "") -> None:
        logs.append({"timestamp": utc_now_iso(), "event": event, "detail": detail})

    log("pipeline_start", json.dumps(params, sort_keys=True))

    df = generate_demo_data(
        target_rows=int(params["rows"]),
        seed=int(params["seed"]),
        missing_rate=float(params["missing_rate"]),
        duplicate_rate=float(params["duplicate_rate"]),
        unicode_rate=float(params["unicode_rate"]),
    )
    log("source_loaded", f"{len(df)} rows, {len(df.columns)} columns")

    fp = df_fingerprint(df)
    log("input_fingerprint", fp)

    profile = profile_columns(df)
    log("profiling_complete", f"{len(profile)} columns profiled")

    keys = candidate_keys(df, profile)
    log("candidate_keys", f"{len(keys)} candidates")

    fds = compute_functional_dependencies(df, profile, float(params["fd_threshold"]))
    log("functional_dependencies", f"{len(fds)} dependencies above threshold")

    fks = foreign_key_candidates(df, profile, fds)
    log("foreign_key_candidates", f"{len(fks)} candidates")

    repeats = detect_repeated_groups(profile, list(df.columns))
    log("repeated_groups", f"{len(repeats)} violations")

    advisor = normalization_advisor(df, profile, keys, fds, fks, repeats)
    log("normalization_advisor", f"{len(advisor['recommended_tables'])} proposed tables")

    sqlite_up, sqlite_down = generate_ddl(advisor, profile, "sqlite")
    postgres_up, postgres_down = generate_ddl(advisor, profile, "postgres")
    log("ddl_generated", "SQLite and PostgreSQL DDL emitted")

    frames = prepare_normalized_frames(df, advisor)
    dry = run_sqlite_dry_run(frames, sqlite_up, advisor)

    dry_status = "PASS" if dry.get("validation_passed") else (dry.get("error") or "COMPLETED_WITH_WARNINGS")
    log("sqlite_dry_run", dry_status)

    config_hash = sha256_text(json.dumps(params, sort_keys=True))

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "config": params,
        "config_hash": config_hash,
        "fingerprint": fp,
        "row_count": int(len(df)),
        "column_count": int(len(df.columns)),
        "data": df,
        "profile": profile,
        "candidate_keys": keys,
        "functional_dependencies": fds,
        "foreign_keys": fks,
        "repeated_groups": repeats,
        "advisor": advisor,
        "frames": frames,
        "ddl": {
            "sqlite_up": sqlite_up,
            "sqlite_down": sqlite_down,
            "postgres_up": postgres_up,
            "postgres_down": postgres_down,
        },
        "dry_run": dry,
        "logs": logs,
    }


def build_suggestions(result: dict[str, Any]) -> list[dict[str, str]]:
    suggestions = []
    advisor = result["advisor"]

    for t in advisor["recommended_tables"]:
        suggestions.append(
            {
                "id": f"create_table_{t['name']}",
                "title": f"Create table: {t['name']}",
                "detail": "Columns: " + ", ".join(t["columns"]) + " | PK: " + ", ".join(t["primary_key"]) + " | Basis: " + t.get("basis", ""),
                "category": "schema",
            }
        )

    for fk in result["foreign_keys"]:
        suggestions.append(
            {
                "id": f"fk_{fk['child_table']}_{fk['child_column']}_{fk['parent_table']}",
                "title": f"Add foreign key: {fk['child_table']}.{fk['child_column']} -> {fk['parent_table']}.{fk['parent_column']}",
                "detail": f"Confidence: {fk['confidence']:.2f} | Containment: {fk['containment']:.2f} | {fk['evidence']}",
                "category": "referential_integrity",
            }
        )

    for rep in result["repeated_groups"]:
        suggestions.append(
            {
                "id": f"split_{rep['source_column']}",
                "title": f"Split repeated group: {rep['source_column']}",
                "detail": f"Move into {rep['child_table']}({rep['child_column']}) | Avg tokens: {rep['avg_tokens']:.2f}",
                "category": "normalization",
            }
        )

    for idx in advisor["indexes"]:
        suggestions.append(
            {
                "id": f"index_{idx['table']}_{'_'.join(idx['columns'])}",
                "title": f"Create index: {idx['table']}({', '.join(idx['columns'])})",
                "detail": idx["reason"],
                "category": "performance",
            }
        )

    for chk in advisor["checks"]:
        check_digest = sha256_text(chk["expression"])[:12]
        suggestions.append(
            {
                "id": f"check_{chk['table']}_{check_digest}",
                "title": f"Add CHECK: {chk['table']} ({chk['expression']})",
                "detail": chk["reason"],
                "category": "data_quality",
            }
        )

    return suggestions


def build_report(result: dict[str, Any], decisions: dict[str, str]) -> dict[str, Any]:
    profile = result["profile"]
    keys = result["candidate_keys"]

    completeness = float(1.0 - profile["null_percent"].mean()) if not profile.empty else 0.0
    best_key_score = float(max((k["score"] for k in keys), default=0.0))

    return {
        "schema_version": result["schema_version"],
        "generated_at": utc_now_iso(),
        "project": "Intelligent Database Schema Designer",
        "mode": "CPU-only local Streamlit prototype",
        "config": result["config"],
        "config_hash": result["config_hash"],
        "input_fingerprint_sha256": result["fingerprint"],
        "source_shape": {
            "rows": result["row_count"],
            "columns": result["column_count"],
        },
        "metrics": {
            "completeness": completeness,
            "best_candidate_key_score": best_key_score,
            "functional_dependency_count": len(result["functional_dependencies"]),
            "foreign_key_candidate_count": len(result["foreign_keys"]),
            "repeated_group_count": len(result["repeated_groups"]),
            "sqlite_dry_run": result["dry_run"],
        },
        "candidate_keys": keys[:20],
        "functional_dependencies": result["functional_dependencies"][:50],
        "foreign_keys": result["foreign_keys"],
        "repeated_groups": result["repeated_groups"],
        "normalization_alternatives": result["advisor"]["alternatives"],
        "recommended_tables": result["advisor"]["recommended_tables"],
        "review_decisions": decisions,
        "limitations": [
            "Inference is sample-based and may miss constraints not visible in the loaded rows.",
            "Functional dependency confidence is empirical, not proof of business semantics.",
            "The demo uses synthetic data; production use requires human review and authorized datasets.",
            "No destructive database changes are applied automatically.",
        ],
    }


def build_artifact_zip(result: dict[str, Any], decisions: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    report = build_report(result, decisions)

    keys_df = pd.DataFrame(result["candidate_keys"])
    if not keys_df.empty and "key_columns" in keys_df.columns:
        keys_df["key_columns"] = keys_df["key_columns"].apply(lambda x: ", ".join(x))

    fds_df = pd.DataFrame(result["functional_dependencies"])
    fks_df = pd.DataFrame(result["foreign_keys"])

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("demo_source.csv", result["data"].to_csv(index=False))
        zf.writestr("column_profile.csv", result["profile"].to_csv(index=False))
        zf.writestr("candidate_keys.csv", keys_df.to_csv(index=False) if not keys_df.empty else "")
        zf.writestr("functional_dependencies.csv", fds_df.to_csv(index=False) if not fds_df.empty else "")
        zf.writestr("foreign_key_candidates.csv", fks_df.to_csv(index=False) if not fks_df.empty else "")
        zf.writestr("migration_sqlite_up.sql", result["ddl"]["sqlite_up"])
        zf.writestr("migration_sqlite_down.sql", result["ddl"]["sqlite_down"])
        zf.writestr("migration_postgres_up.sql", result["ddl"]["postgres_up"])
        zf.writestr("migration_postgres_down.sql", result["ddl"]["postgres_down"])
        zf.writestr("report.json", json.dumps(report, indent=2, default=str))

        for name, frame in result["frames"].items():
            zf.writestr(f"normalized_{name}.csv", frame.to_csv(index=False))

    return buf.getvalue()


st.set_page_config(page_title=APP_NAME, layout="wide", initial_sidebar_state="expanded")

st.title(APP_NAME)
st.caption(
    "Live CPU-only prototype: profiles denormalized data, infers candidate keys, sample functional dependencies, "
    "foreign-key candidates, repeated groups, suggests normalization, emits SQLite/PostgreSQL DDL, and dry-runs in temporary SQLite."
)

with st.sidebar:
    st.header("Simulation controls")
    seed = st.number_input("Random seed", min_value=0, max_value=999999, value=42, step=1)
    rows = st.slider("Target source rows", min_value=200, max_value=5000, value=1200, step=100)
    missing_rate = st.slider("Missing-value rate", min_value=0.0, max_value=0.30, value=0.04, step=0.01)
    duplicate_rate = st.slider("Duplicate natural-key rate", min_value=0.0, max_value=0.20, value=0.03, step=0.01)
    unicode_rate = st.slider("Unicode edge-case rate", min_value=0.0, max_value=0.20, value=0.05, step=0.01)
    fd_threshold = st.slider("FD confidence threshold", min_value=0.50, max_value=1.00, value=0.85, step=0.01)
    auto_run = st.checkbox("Auto-run when settings change", value=True)

    generate_clicked = st.button("Generate new demo")
    reset_review_clicked = st.button("Reset review decisions")

params = {
    "seed": int(seed),
    "rows": int(rows),
    "missing_rate": float(missing_rate),
    "duplicate_rate": float(duplicate_rate),
    "unicode_rate": float(unicode_rate),
    "fd_threshold": float(fd_threshold),
}

params_hash = sha256_text(json.dumps(params, sort_keys=True))

if "pipeline" not in st.session_state:
    st.session_state.pipeline = None
if "params_hash" not in st.session_state:
    st.session_state.params_hash = None
if "run_counter" not in st.session_state:
    st.session_state.run_counter = 0

need_run = st.session_state.pipeline is None or st.session_state.params_hash != params_hash

if reset_review_clicked:
    prefix = f"review_r{st.session_state.run_counter}_"
    for k in list(st.session_state.keys()):
        if k.startswith(prefix):
            del st.session_state[k]
    st.rerun()

if generate_clicked:
    need_run = True

if need_run and (st.session_state.pipeline is None or auto_run):
    bar = st.progress(0.0, text="Loading realistic synthetic denormalized data...")
    set_progress(bar, 0.0, "Loading realistic synthetic denormalized data...")
    time.sleep(0.05)

    try:
        result = run_pipeline(params)
    except Exception as exc:
        st.error(f"Pipeline failed: {exc}")
        st.stop()

    set_progress(bar, 1.0, "Live demo ready")
    st.session_state.pipeline = result
    st.session_state.params_hash = params_hash
    st.session_state.run_counter += 1
    time.sleep(0.2)
    st.rerun()

if st.session_state.pipeline is None:
    st.warning("Press 'Generate new demo' to start.")
    st.stop()

result = st.session_state.pipeline
df = result["data"]
profile = result["profile"]
advisor = result["advisor"]

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Rows", result["row_count"])
m2.metric("Columns", result["column_count"])
m3.metric("Candidate keys", len(result["candidate_keys"]))
m4.metric("FDs detected", len(result["functional_dependencies"]))
m5.metric("SQLite dry-run", "Pass" if result["dry_run"].get("validation_passed") else "Warnings")

st.caption(
    f"Input SHA-256: `{result['fingerprint'][:16]}…` | "
    f"Config hash: `{result['config_hash'][:12]}…` | "
    f"Generated: `{result['generated_at']}`"
)

tabs = st.tabs(
    [
        "Data Preview",
        "Profiling",
        "Keys & Dependencies",
        "Normalization Advisor",
        "DDL & Dry-run",
        "Human Review & Export",
        "Demo Log",
    ]
)

with tabs[0]:
    st.subheader("Denormalized source sample")
    st.dataframe(df.head(100), hide_index=True, use_container_width=True)

    with st.expander("Dtypes and first values"):
        preview = pd.DataFrame(
            {
                "column": df.columns,
                "dtype": [str(t) for t in df.dtypes],
                "example": [str(df[c].dropna().iloc[0]) if not df[c].dropna().empty else "" for c in df.columns],
            }
        )
        st.dataframe(preview, hide_index=True, use_container_width=True)

    st.download_button(
        "Download demo source CSV",
        df.to_csv(index=False).encode("utf-8"),
        file_name="demo_source.csv",
        mime="text/csv",
    )

with tabs[1]:
    st.subheader("Column profiling")
    display_profile = profile[
        [
            "column",
            "dtype",
            "semantic_type",
            "null_percent",
            "unique_count",
            "unique_ratio",
            "avg_tokens",
        ]
    ].copy()
    display_profile["null_percent"] = display_profile["null_percent"].round(4)
    display_profile["unique_ratio"] = display_profile["unique_ratio"].round(4)
    st.dataframe(display_profile, hide_index=True, use_container_width=True)

    chart_df = profile.set_index("column")[["null_percent"]].rename(columns={"null_percent": "Null %"})
    st.bar_chart(chart_df)

    selected_col = st.selectbox("Inspect column", profile["column"].tolist())
    selected_row = profile[profile["column"] == selected_col].iloc[0]
    st.json(json.loads(json.dumps(selected_row.to_dict(), default=str)), expanded=True)

with tabs[2]:
    st.subheader("Candidate keys")
    keys_df = pd.DataFrame(result["candidate_keys"])
    if keys_df.empty:
        st.info("No strong candidate keys detected.")
    else:
        keys_display = keys_df.copy()
        keys_display["key_columns"] = keys_display["key_columns"].apply(lambda x: ", ".join(x))
        st.dataframe(
            keys_display[["key_columns", "kind", "uniqueness", "completeness", "score"]],
            hide_index=True,
            use_container_width=True,
        )

    st.subheader("Functional dependencies (TANE-style on sample)")
    fds_df = pd.DataFrame(result["functional_dependencies"])
    if fds_df.empty:
        st.info("No functional dependencies above the configured threshold.")
    else:
        st.dataframe(
            fds_df[["determinant", "dependent", "confidence", "support_groups", "support_rows", "violations"]],
            hide_index=True,
            use_container_width=True,
        )
        with st.expander("Evidence for top dependencies"):
            st.json(json.loads(json.dumps(fds_df.head(10)["evidence"].tolist(), default=str)), expanded=True)

    st.subheader("Inclusion dependencies / foreign-key candidates")
    fks_df = pd.DataFrame(result["foreign_keys"])
    if fks_df.empty:
        st.info("No foreign-key candidates detected.")
    else:
        st.dataframe(
            fks_df[
                [
                    "child_table",
                    "child_column",
                    "parent_table",
                    "parent_column",
                    "containment",
                    "confidence",
                ]
            ],
            hide_index=True,
            use_container_width=True,
        )

with tabs[3]:
    st.subheader("Ranked normalization alternatives")
    for alt in advisor["alternatives"]:
        badge = " ✅ Recommended" if alt["recommended"] else ""
        with st.container(border=True):
            st.markdown(f"**Rank {alt['rank']}: {alt['name']}**{badge}")
            st.write(alt["description"])
            st.caption(f"Trade-offs: {alt['tradeoffs']}")

    st.subheader("Proposed tables")
    for t in advisor["recommended_tables"]:
        with st.expander(f"{t['name']} — PK: {', '.join(t['primary_key'])}", expanded=False):
            st.write("Columns:", ", ".join(t["columns"]))
            st.caption(t.get("basis", ""))

    if result["repeated_groups"]:
        st.subheader("Repeated-group repairs")
        rep_df = pd.DataFrame(result["repeated_groups"])
        st.dataframe(rep_df, hide_index=True, use_container_width=True)

with tabs[4]:
    dialect = st.radio("SQL dialect", ["SQLite", "PostgreSQL"], horizontal=True)

    if dialect == "SQLite":
        up_sql = result["ddl"]["sqlite_up"]
        down_sql = result["ddl"]["sqlite_down"]
    else:
        up_sql = result["ddl"]["postgres_up"]
        down_sql = result["ddl"]["postgres_down"]

    st.subheader("Migration UP")
    st.code(up_sql, language="sql")
    st.download_button("Download UP SQL", up_sql, file_name=f"{dialect.lower()}_up.sql", mime="text/sql")

    with st.expander("Rollback DOWN"):
        st.code(down_sql, language="sql")
        st.download_button("Download DOWN SQL", down_sql, file_name=f"{dialect.lower()}_down.sql", mime="text/sql")

    st.subheader("Temporary SQLite dry-run")
    dry = result["dry_run"]
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("DDL executed", "Yes" if dry.get("ddl_executed") else "No")
    d2.metric("Validation passed", "Yes" if dry.get("validation_passed") else "No")
    d3.metric("Inserted tables", len(dry.get("inserted_rows", {})))
    d4.metric("Line-total mismatches", dry.get("line_total_mismatches", "n/a"))

    if dry.get("error"):
        st.error(f"Dry-run error: {dry['error']}")
    else:
        st.json(json.loads(json.dumps(dry, default=str)), expanded=True)

    if st.button("Re-run SQLite dry-run"):
        with st.spinner("Re-running temporary SQLite dry-run..."):
            frames = prepare_normalized_frames(df, advisor)
            sqlite_up, _ = generate_ddl(advisor, profile, "sqlite")
            new_dry = run_sqlite_dry_run(frames, sqlite_up, advisor)
            result["dry_run"] = new_dry
            result["frames"] = frames
            st.session_state.pipeline = result
        st.success("Dry-run refreshed.")

with tabs[5]:
    st.subheader("Human review checkpoint")
    st.caption(
        "This prototype never applies destructive changes automatically. Accept/reject suggestions, then export a reviewable change-set."
    )

    suggestions = build_suggestions(result)
    decisions: dict[str, str] = {}
    options = ["Accept", "Reject", "Needs discussion"]

    for s in suggestions:
        radio_key = f"review_r{st.session_state.run_counter}_{s['id']}"
        current = st.session_state.get(radio_key, "Needs discussion")
        idx = options.index(current) if current in options else 2
        choice = st.radio(s["title"], options, index=idx, key=radio_key, horizontal=True)
        st.caption(s["detail"])
        decisions[s["id"]] = choice

    accepted = [s for s in suggestions if decisions.get(s["id"]) == "Accept"]
    rejected = [s for s in suggestions if decisions.get(s["id"]) == "Reject"]
    pending = [s for s in suggestions if decisions.get(s["id"]) == "Needs discussion"]

    st.subheader("Change-set summary")
    c1, c2, c3 = st.columns(3)
    c1.metric("Accepted", len(accepted))
    c2.metric("Rejected", len(rejected))
    c3.metric("Needs discussion", len(pending))

    report = build_report(result, decisions)
    report_json = json.dumps(report, indent=2, default=str)

    zip_bytes = build_artifact_zip(result, decisions)

    st.download_button(
        "Download JSON report",
        report_json.encode("utf-8"),
        file_name="schema_designer_report.json",
        mime="application/json",
    )

    st.download_button(
        "Download full artifact bundle (ZIP)",
        zip_bytes,
        file_name="schema_designer_artifacts.zip",
        mime="application/zip",
    )

    with st.expander("Preview accepted actions"):
        if accepted:
            st.json(json.loads(json.dumps(accepted, default=str)), expanded=True)
        else:
            st.info("No accepted actions yet.")

with tabs[6]:
    st.subheader("Structured demo log")
    log_df = pd.DataFrame(result["logs"])
    st.dataframe(log_df, hide_index=True, use_container_width=True)

    st.subheader("Safety and limitations")
    st.markdown(
        """
- Raw input is treated as immutable evidence and fingerprinted with SHA-256.
- DDL is generated as reviewable text artifacts.
- Dry-run executes only in an in-memory/temporary SQLite database.
- No `DROP` or `ALTER` is executed automatically against a persistent database.
- Consequential schema changes require human approval.
- Sample-based inference can miss real-world constraints not present in the loaded data.
        """
    )