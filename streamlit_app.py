import csv
import hashlib
import io
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
import streamlit as st

APP_TITLE = "Autismo Conviction Calculator"
APP_DESCRIPTION = (
    "Evaluate market data snapshots against a local base dataset to produce "
    "a calibrated 0–100 conviction score, deterministic trade orientation, "
    "and a traceable rationale."
)

BASE_DATA_PATH = Path(__file__).resolve().parent / "data" / "base_dataset.json"
DATA_DIR = Path(__file__).resolve().parent / "data"
LEDGER_PATH = DATA_DIR / "ledger.csv"
SNAPSHOT_DIR = DATA_DIR / "snapshots"

CATEGORY_ORIENTATION = {
    "short": "Short idea",
    "long": "Long idea",
    "neutral": "No trade",
}


def ensure_data_directories() -> None:
    """Create data directories required by the application."""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)


@st.cache_data(show_spinner=False)
def load_base_dataset() -> Dict[str, Any]:
    if not BASE_DATA_PATH.exists():
        raise FileNotFoundError(
            "Base dataset not found. Please ensure data/base_dataset.json exists."
        )
    with BASE_DATA_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def parse_value(raw_value: Optional[str]) -> Optional[Any]:
    if raw_value is None:
        return None
    if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, bool):
        return float(raw_value)
    if isinstance(raw_value, str):
        stripped = raw_value.strip()
        if stripped.lower() in {"", "none", "null", "nan"}:
            return None
        if stripped.endswith("%"):
            stripped = stripped[:-1]
        stripped = stripped.replace(",", "")
        try:
            return float(stripped)
        except ValueError:
            return stripped
    return raw_value


def set_nested(container: Dict[str, Any], keys: Iterable[str], value: Any) -> None:
    keys = list(keys)
    current = container
    for key in keys[:-1]:
        current = current.setdefault(key, {})
    current[keys[-1]] = value


def flatten_dict(data: Dict[str, Any], parent_key: str = "") -> Dict[str, Any]:
    items: Dict[str, Any] = {}
    for key, value in data.items():
        new_key = f"{parent_key}.{key}" if parent_key else key
        if isinstance(value, dict):
            items.update(flatten_dict(value, new_key))
        elif isinstance(value, list):
            # Lists are tracked for presence but not flattened into scalar keys.
            items[new_key] = value
        else:
            items[new_key] = value
    return items


def get_nested_value(container: Dict[str, Any], path: str) -> Any:
    current: Any = container
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def parse_json_snapshot(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, list):
        if not payload:
            raise ValueError("JSON snapshot list is empty.")
        payload = payload[0]
    if isinstance(payload, dict) and "snapshot" in payload:
        payload = payload["snapshot"]
    if not isinstance(payload, dict):
        raise ValueError("Unsupported JSON snapshot structure.")
    snapshot = payload.copy()
    metadata = snapshot.get("metadata")
    if metadata is None or not isinstance(metadata, dict):
        snapshot["metadata"] = {}
    return snapshot


def parse_csv_snapshot(text: str) -> Dict[str, Any]:
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        raise ValueError("CSV snapshot is empty.")
    record = rows[0]
    snapshot: Dict[str, Any] = {}
    for key, value in record.items():
        if key is None:
            continue
        parsed_value = parse_value(value)
        if parsed_value is None:
            continue
        set_nested(snapshot, key.split("."), parsed_value)
    snapshot.setdefault("metadata", {})
    return snapshot


def parse_snapshot_file(uploaded_file: Any) -> Tuple[Dict[str, Any], bytes, str, str]:
    raw_bytes = uploaded_file.getvalue()
    checksum = hashlib.sha256(raw_bytes).hexdigest()
    text = raw_bytes.decode("utf-8-sig")
    try:
        payload = json.loads(text)
        snapshot = parse_json_snapshot(payload)
        return snapshot, raw_bytes, "json", checksum
    except json.JSONDecodeError:
        snapshot = parse_csv_snapshot(text)
        return snapshot, raw_bytes, "csv", checksum


def coerce_numeric(value: Any) -> Optional[float]:
    parsed = parse_value(value)
    return parsed if isinstance(parsed, (int, float)) else None


def apply_decay(value: float, ingest_time: datetime, as_of_time: Optional[datetime], decay_days: Optional[int]) -> float:
    if not decay_days or not as_of_time:
        return value
    age_seconds = (ingest_time - as_of_time).total_seconds()
    if age_seconds <= 0:
        return value
    decay_factor = math.exp(-age_seconds / (decay_days * 86400))
    return value * decay_factor


def calculate_score(
    snapshot: Dict[str, Any],
    base_dataset: Dict[str, Any],
    ingest_time: datetime,
    as_of_time: Optional[datetime]
) -> Dict[str, Any]:
    categories = base_dataset.get("categories", {})
    category_labels = {
        key: meta.get("label", key.replace("_", " ").title())
        for key, meta in categories.items()
    }

    contributions: List[Dict[str, Any]] = []
    missing_by_category: Dict[str, List[str]] = {}
    presence_mask: Dict[str, Dict[str, bool]] = {}
    category_pressures: Dict[str, float] = {}
    coverage_by_category: Dict[str, float] = {}

    total_weight_available = 0.0
    total_weight_present = 0.0
    weighted_pressure_sum = 0.0
    coverage_sum = 0.0

    for category_key, metadata in categories.items():
        fields = metadata.get("fields", {})
        category_snapshot = snapshot.get(category_key, {})
        if not isinstance(category_snapshot, dict):
            category_snapshot = {}

        category_total_weight = sum(field.get("weight", 1.0) for field in fields.values())
        category_present_weight = 0.0
        category_effect_sum = 0.0

        for field_key, field_meta in fields.items():
            value = get_nested_value(category_snapshot, field_key)
            presence_mask.setdefault(category_key, {})[field_key] = value is not None
            if value is None:
                missing_by_category.setdefault(category_key, []).append(field_key)
                continue

            numeric_value = coerce_numeric(value)
            if numeric_value is None:
                missing_by_category.setdefault(category_key, []).append(f"{field_key} (non-numeric)")
                continue

            if category_key == "catalysts" and field_key == "tilt":
                numeric_value = apply_decay(
                    float(numeric_value),
                    ingest_time,
                    as_of_time,
                    metadata.get("decay_days"),
                )

            std = field_meta.get("std") or 1.0
            mean = field_meta.get("mean", 0.0)
            direction = field_meta.get("direction", 0.0)
            weight = field_meta.get("weight", 1.0)

            normalized = (numeric_value - mean) / std
            weighted_effect = math.tanh(normalized) * weight
            directed_effect = weighted_effect * direction

            contributions.append(
                {
                    "category": category_labels[category_key],
                    "field": field_key,
                    "raw_value": numeric_value,
                    "effect": directed_effect,
                    "description": field_meta.get("description", ""),
                }
            )

            category_effect_sum += directed_effect
            category_present_weight += weight

        if category_total_weight == 0:
            category_pressures[category_key] = 0.0
            coverage_by_category[category_key] = 0.0
            continue

        category_pressure = category_effect_sum / category_total_weight
        coverage = category_present_weight / category_total_weight

        category_pressures[category_key] = category_pressure
        coverage_by_category[category_key] = coverage

        weighted_pressure_sum += category_pressure * coverage
        coverage_sum += coverage
        total_weight_available += category_total_weight
        total_weight_present += category_present_weight

    overall_coverage = 0.0
    if total_weight_available > 0:
        overall_coverage = total_weight_present / total_weight_available

    composite_pressure = weighted_pressure_sum / coverage_sum if coverage_sum > 0 else 0.0
    adjusted_pressure = composite_pressure * overall_coverage
    adjusted_pressure = max(-1.0, min(1.0, adjusted_pressure))

    score = 50.0 + adjusted_pressure * 50.0
    score = max(0.0, min(100.0, score))

    if score <= 30:
        orientation_key = "short"
    elif score >= 70:
        orientation_key = "long"
    else:
        orientation_key = "neutral"
    orientation = CATEGORY_ORIENTATION[orientation_key]

    top_contributors = sorted(
        contributions,
        key=lambda item: abs(item["effect"]),
        reverse=True,
    )[:5]

    missing_summary = {
        category_labels[key]: fields
        for key, fields in missing_by_category.items()
        if fields
    }

    known_paths = {
        f"{category}.{field}" for category, meta in categories.items() for field in meta.get("fields", {}).keys()
    }

    flattened_snapshot = flatten_dict({k: v for k, v in snapshot.items() if k != "metadata"})
    unknown_fields = [key for key in flattened_snapshot.keys() if key not in known_paths]

    rationale_lines: List[str] = []
    for item in top_contributors:
        if abs(item["effect"]) < 1e-6:
            continue
        direction = "bullish" if item["effect"] > 0 else "bearish"
        rationale_lines.append(
            f"{item['category']}: {item['field']} {direction} influence ({item['effect']:+.2f})"
        )

    if missing_summary:
        missing_strings = [
            f"{category}: missing {', '.join(fields)}"
            for category, fields in missing_summary.items()
        ]
        rationale_lines.append("Data gaps — " + "; ".join(missing_strings))

    if unknown_fields:
        rationale_lines.append(
            "Additional fields observed without base metadata: "
            + ", ".join(sorted(unknown_fields))
        )

    if not rationale_lines:
        rationale_lines.append("Inputs align with neutral historical context.")

    labeled_presence_mask = {
        category_labels.get(key, key.replace("_", " ").title()): fields
        for key, fields in presence_mask.items()
    }

    return {
        "score": score,
        "orientation": orientation,
        "orientation_key": orientation_key,
        "composite_pressure": adjusted_pressure,
        "overall_coverage": overall_coverage,
        "category_pressures": {
            category_labels[key]: category_pressures.get(key, 0.0)
            for key in category_pressures
        },
        "coverage_by_category": {
            category_labels[key]: coverage_by_category.get(key, 0.0)
            for key in coverage_by_category
        },
        "top_contributors": top_contributors,
        "missing_summary": missing_summary,
        "unknown_fields": unknown_fields,
        "presence_mask": labeled_presence_mask,
        "rationale_lines": rationale_lines,
    }


def save_snapshot(raw_bytes: bytes, checksum: str, extension: str, ingest_time: datetime) -> str:
    ensure_data_directories()
    filename = f"{ingest_time.strftime('%Y%m%dT%H%M%S%f')}_{checksum[:8]}.{extension}"
    path = SNAPSHOT_DIR / filename
    with path.open("wb") as handle:
        handle.write(raw_bytes)
    return str(path.relative_to(Path(__file__).resolve().parent))


def append_to_ledger(entry: Dict[str, Any]) -> None:
    ensure_data_directories()
    fieldnames = [
        "timestamp",
        "as_of",
        "symbol",
        "score",
        "orientation",
        "coverage_overall",
        "coverage_by_category",
        "category_pressures",
        "top_contributors",
        "missing_summary",
        "unknown_fields",
        "snapshot_checksum",
        "snapshot_path",
        "base_version",
        "rationale",
    ]
    exists = LEDGER_PATH.exists()
    with LEDGER_PATH.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerow(entry)


def load_ledger() -> pd.DataFrame:
    if not LEDGER_PATH.exists():
        return pd.DataFrame()
    return pd.read_csv(LEDGER_PATH)


st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)
st.caption(APP_DESCRIPTION)

try:
    BASE_DATA = load_base_dataset()
except FileNotFoundError as error:
    st.error(str(error))
    st.stop()

metadata_col, version_col = st.columns([3, 1])
metadata_col.write("Base dataset description:")
metadata_col.write(BASE_DATA.get("description", "No description provided."))
version_col.metric("Base version", BASE_DATA.get("version", "unknown"))

uploaded_file = st.file_uploader("Upload market snapshot", type=["json", "csv"], accept_multiple_files=False)

if "last_evaluation" not in st.session_state:
    st.session_state["last_evaluation"] = None

if uploaded_file is not None:
    try:
        snapshot, raw_bytes, file_format, checksum = parse_snapshot_file(uploaded_file)
    except ValueError as exc:
        st.error(f"Unable to parse snapshot: {exc}")
        st.stop()

    metadata = snapshot.setdefault("metadata", {})
    symbol = metadata.get("symbol") or metadata.get("universe")
    as_of_str = metadata.get("as_of")
    as_of_time = parse_iso_datetime(as_of_str)

    st.subheader("Snapshot preview")
    preview_container = st.expander("Show parsed snapshot", expanded=False)
    preview_container.json(snapshot)

    ingest_time = datetime.now(timezone.utc)

    if st.button("Evaluate snapshot", type="primary"):
        results = calculate_score(snapshot, BASE_DATA, ingest_time, as_of_time)
        snapshot_path = save_snapshot(raw_bytes, checksum, file_format, ingest_time)
        rationale_text = " | ".join(results["rationale_lines"])
        ledger_entry = {
            "timestamp": ingest_time.isoformat(),
            "as_of": as_of_str or ingest_time.isoformat(),
            "symbol": symbol or "",
            "score": f"{results['score']:.2f}",
            "orientation": results["orientation"],
            "coverage_overall": f"{results['overall_coverage']:.4f}",
            "coverage_by_category": json.dumps(results["coverage_by_category"]),
            "category_pressures": json.dumps(results["category_pressures"]),
            "top_contributors": json.dumps(results["top_contributors"]),
            "missing_summary": json.dumps(results["missing_summary"]),
            "unknown_fields": json.dumps(results["unknown_fields"]),
            "snapshot_checksum": checksum,
            "snapshot_path": snapshot_path,
            "base_version": BASE_DATA.get("version", "unknown"),
            "rationale": rationale_text,
        }
        append_to_ledger(ledger_entry)

        st.session_state["last_evaluation"] = {
            "results": results,
            "metadata": metadata,
            "symbol": symbol,
            "checksum": checksum,
            "snapshot_path": snapshot_path,
            "ingest_time": ingest_time,
            "as_of": as_of_str or ingest_time.isoformat(),
            "format": file_format,
        }
        st.success("Snapshot evaluated and logged to ledger.")

if st.session_state.get("last_evaluation"):
    evaluation = st.session_state["last_evaluation"]
    results = evaluation["results"]

    st.subheader("Latest evaluation")
    score_col, orientation_col, coverage_col = st.columns(3)
    score_col.metric("Conviction score", f"{results['score']:.1f}")
    orientation_col.metric("Orientation", results["orientation"])
    coverage_col.metric("Coverage", f"{results['overall_coverage'] * 100:.1f}%")

    st.markdown("### Rationale")
    for line in results["rationale_lines"]:
        st.markdown(f"- {line}")

    if results["missing_summary"]:
        st.warning(
            "Data gaps reduce conviction: "
            + "; ".join(
                f"{category}: {', '.join(fields)}" for category, fields in results["missing_summary"].items()
            )
        )

    if results["unknown_fields"]:
        st.info(
            "Fields without base metadata were observed: "
            + ", ".join(sorted(results["unknown_fields"]))
        )

    category_rows = []
    for category, pressure in results["category_pressures"].items():
        category_rows.append(
            {
                "Category": category,
                "Pressure (-1 to 1)": round(pressure, 3),
                "Coverage": round(results["coverage_by_category"].get(category, 0.0), 3),
            }
        )
    category_df = pd.DataFrame(category_rows).set_index("Category")
    st.markdown("### Category diagnostics")
    st.dataframe(category_df, use_container_width=True)

    contributor_rows = []
    for item in results["top_contributors"]:
        contributor_rows.append(
            {
                "Category": item["category"],
                "Field": item["field"],
                "Effect": round(item["effect"], 3),
                "Value": item["raw_value"],
                "Description": item.get("description", ""),
            }
        )
    if contributor_rows:
        contributor_df = pd.DataFrame(contributor_rows)
        st.markdown("### Top contributing signals")
        st.dataframe(contributor_df, use_container_width=True)

    presence_rows = []
    for category, fields in results["presence_mask"].items():
        for field, present in fields.items():
            presence_rows.append(
                {
                    "Category": category,
                    "Field": field,
                    "Present": bool(present),
                }
            )
    if presence_rows:
        presence_df = pd.DataFrame(presence_rows)
        st.markdown("### Field presence mask")
        st.dataframe(presence_df, use_container_width=True)

ledger_df = load_ledger()
if not ledger_df.empty:
    st.subheader("Ledger history")
    st.dataframe(ledger_df.tail(25).iloc[::-1], use_container_width=True)
else:
    st.info("No ledger entries yet. Upload and evaluate a snapshot to begin tracking history.")
