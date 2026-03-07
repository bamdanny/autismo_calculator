import json
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from calculator_core import (
    append_to_ledger,
    calculate_score,
    load_base_dataset,
    parse_iso_datetime,
    parse_snapshot_file,
    save_snapshot,
    LEDGER_PATH,
)

APP_TITLE = "Autismo Conviction Calculator"
APP_DESCRIPTION = (
    "Evaluate market data snapshots against a local base dataset to produce "
    "a calibrated 0–100 conviction score, deterministic trade orientation, "
    "and a traceable rationale."
)


def load_ledger() -> pd.DataFrame:
    if not LEDGER_PATH.exists():
        return pd.DataFrame()
    return pd.read_csv(LEDGER_PATH)


@st.cache_data(show_spinner=False)
def get_base_data():
    return load_base_dataset()


st.set_page_config(page_title=APP_TITLE, layout="wide")
st.title(APP_TITLE)
st.caption(APP_DESCRIPTION)

try:
    BASE_DATA = get_base_data()
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
