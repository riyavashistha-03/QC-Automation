"""
OSA QC Automation — Streamlit UI

Main application interface supporting:
1. Data source selection (Database vs Excel upload)
2. Platform & Location filtering
3. Parallel execution with live streaming row-by-row updates
4. Mismatch results table
5. Formatted Excel report download
6. Expandable error log
"""

import time
import pandas as pd
import streamlit as st

import db_connector
import excel_handler
from platform_config import (
    PLATFORM_CONFIG,
    get_platform_names,
)
from qc_engine import run_qc
from utils import ErrorCollector

# ---------------------------------------------------------------------------
# Page Configuration
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="OSA QC Automation",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Session State Initialization
# ---------------------------------------------------------------------------

if "qc_results" not in st.session_state:
    st.session_state["qc_results"] = None  # Dict of {platform: DataFrame}
if "error_collector" not in st.session_state:
    st.session_state["error_collector"] = ErrorCollector()
if "is_running" not in st.session_state:
    st.session_state["is_running"] = False


# ---------------------------------------------------------------------------
# Header & Styling
# ---------------------------------------------------------------------------

st.title("🔍 OSA Quality Check Automation")
st.caption("Cross-check claimed On-Shelf Availability against live e-commerce stock status.")

# ---------------------------------------------------------------------------
# Sidebar Settings / Database Health
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("⚙️ Settings & Speed Controls")

    # DB Connection status check
    db_ok, db_msg = db_connector.test_connection()
    if db_ok:
        st.success("🟢 MySQL Database Connected")
    else:
        st.warning(f"🟡 Database Offline: {db_msg}")
        st.caption("You can still use **Excel Upload** mode!")

    st.markdown("---")
    st.subheader("⚡ Performance & Retries")
    
    concurrency = st.slider(
        "Parallel Workers (Speed)",
        min_value=1,
        max_value=5,
        value=3,
        help="Higher values check multiple items concurrently in parallel tabs for 3x-5x faster QC."
    )
    
    max_retries = st.number_input(
        "Max retries per URL",
        min_value=1,
        max_value=5,
        value=2
    )

    st.markdown("---")
    st.caption("OSA QC Engine v1.1 (Parallel & Streaming)")


# ---------------------------------------------------------------------------
# Main Controls Form
# ---------------------------------------------------------------------------

st.subheader("1. Configure QC Run")

col_source, col_platform, col_location = st.columns([1, 1.5, 1.5])

with col_source:
    data_source = st.radio(
        "Data Source",
        options=["From Database", "Upload Excel"],
        horizontal=False,
    )

available_platforms = get_platform_names()

with col_platform:
    if data_source == "From Database" and db_ok:
        db_tables = db_connector.get_platform_tables()
        if db_tables:
            available_platforms = db_tables

    selected_platforms = st.multiselect(
        "Platform(s)",
        options=["All Platforms"] + [PLATFORM_CONFIG[p]["display_name"] for p in available_platforms],
        default=[PLATFORM_CONFIG[available_platforms[0]]["display_name"]] if available_platforms else [],
        help="Select platforms to perform stock verification on."
    )

# Map display names back to platform keys
name_to_key = {config["display_name"]: key for key, config in PLATFORM_CONFIG.items()}
target_platform_keys = []
if "All Platforms" in selected_platforms or not selected_platforms:
    target_platform_keys = available_platforms
else:
    target_platform_keys = [name_to_key[name] for name in selected_platforms if name in name_to_key]

with col_location:
    if len(target_platform_keys) == 1 and data_source == "From Database" and db_ok:
        locations = db_connector.get_locations_for_platform(target_platform_keys[0])
        location_options = ["All Locations"] + locations
    else:
        all_locs = set()
        for p_key in target_platform_keys:
            all_locs.update(PLATFORM_CONFIG[p_key].get("location_mapping", {}).keys())
        location_options = ["All Locations"] + sorted(list(all_locs))

    selected_locations = st.multiselect(
        "Location(s)",
        options=location_options,
        default=["All Locations"],
    )

# Excel File Uploader (conditional)
uploaded_file = None
if data_source == "Upload Excel":
    st.markdown("---")
    uploaded_file = st.file_uploader(
        "Upload Excel File containing SKU data",
        type=["xlsx", "xls"],
        help="Ensure columns match the expected platform column names."
    )

st.markdown("---")

# ---------------------------------------------------------------------------
# Run QC Execution with Live Streaming Feed
# ---------------------------------------------------------------------------

run_button = st.button("🚀 Run QC Check", type="primary", use_container_width=True)

if run_button:
    if data_source == "Upload Excel" and not uploaded_file:
        st.error("Please upload an Excel file before running the check.")
        st.stop()

    if not target_platform_keys:
        st.error("Please select at least one platform.")
        st.stop()

    st.session_state["qc_results"] = {}
    st.session_state["error_collector"].clear()
    st.session_state["is_running"] = True

    # Progress & Live Inspection Container
    st.markdown("### 🛰️ Live Verification Stream")
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    # Live summary counters
    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    metric_total = col_m1.empty()
    metric_matches = col_m2.empty()
    metric_mismatches = col_m3.empty()
    metric_errors = col_m4.empty()
    
    # Live streaming table
    st.caption("👇 Real-time row-by-row inspection feed (updates as each item completes):")
    live_feed_placeholder = st.empty()

    results_by_platform = {}
    live_stream_rows = []
    
    stats = {
        "checked": 0,
        "matches": 0,
        "mismatches": 0,
        "errors": 0,
    }

    for p_idx, platform_key in enumerate(target_platform_keys):
        display_name = PLATFORM_CONFIG[platform_key]["display_name"]

        # Fetch Data
        if data_source == "From Database":
            if not db_ok:
                st.error("Database is not connected.")
                st.stop()
            status_text.text(f"Fetching data from database for {display_name}...")
            df_input = db_connector.fetch_data(platform_key, locations=selected_locations)
        else:
            status_text.text(f"Parsing uploaded file for {display_name}...")
            df_input = excel_handler.parse_upload(uploaded_file, platform=platform_key)

        if df_input.empty:
            st.warning(f"No records found for {display_name}.")
            continue

        status_text.text(f"Running QC on {len(df_input)} items for {display_name} using {concurrency} parallel workers...")

        def update_progress(current, total):
            overall_pct = (p_idx + (current / total)) / len(target_platform_keys)
            progress_bar.progress(min(overall_pct, 1.0))
            status_text.text(f"[{display_name}] Processing item {current} of {total} ({overall_pct*100:.0f}% total)...")

        def on_row_complete(row_info: dict):
            stats["checked"] += 1
            if row_info["Result"] == "MATCHED ✅":
                stats["matches"] += 1
            elif row_info["Result"] == "MISMATCH ❌":
                stats["mismatches"] += 1
            else:
                stats["errors"] += 1

            live_stream_rows.append(row_info)
            
            # Update metrics live
            metric_total.metric("Items Checked", stats["checked"])
            metric_matches.metric("Matches ✅", stats["matches"])
            metric_mismatches.metric("Mismatches ❌", stats["mismatches"])
            metric_errors.metric("Errors ⚠️", stats["errors"])

            # Update live feed table
            df_live = pd.DataFrame(live_stream_rows)
            live_feed_placeholder.dataframe(
                df_live.tail(20)[["Row", "SKU ID", "Claimed Status", "Actual Status", "Result", "Remark", "URL"]],
                use_container_width=True,
                hide_index=True,
            )

        # Run QC Engine with Live Streaming Callback
        loc_arg = selected_locations[0] if len(selected_locations) == 1 and selected_locations[0] != "All Locations" else None
        mismatches_df = run_qc(
            df=df_input,
            platform=platform_key,
            location_id=loc_arg,
            progress_callback=update_progress,
            row_callback=on_row_complete,
            error_collector=st.session_state["error_collector"],
            max_retries=max_retries,
            concurrency=concurrency,
        )

        results_by_platform[platform_key] = mismatches_df

    progress_bar.progress(1.0)
    status_text.text("✅ Quality Check Completed!")
    st.session_state["qc_results"] = results_by_platform
    st.session_state["is_running"] = False


# ---------------------------------------------------------------------------
# Final Results View & Excel Report Download
# ---------------------------------------------------------------------------

if st.session_state["qc_results"] is not None:
    st.markdown("---")
    st.subheader("2. Final Mismatch Report & Export")

    results_dict = st.session_state["qc_results"]
    total_mismatches = sum(len(df) for df in results_dict.values())

    if total_mismatches > 0:
        tabs = st.tabs([PLATFORM_CONFIG[k]["display_name"] for k in results_dict.keys()])

        for tab, (p_key, df_res) in zip(tabs, results_dict.items()):
            with tab:
                if df_res.empty:
                    st.success("🎉 No mismatches found for this platform!")
                else:
                    st.dataframe(
                        df_res,
                        use_container_width=True,
                        hide_index=True,
                    )

        # Download Report Button
        report_buffer = excel_handler.generate_report(results_dict)
        report_filename = excel_handler.get_report_filename(list(results_dict.keys()))

        st.download_button(
            label="📥 Download Detailed Excel Mismatch Report",
            data=report_buffer,
            file_name=report_filename,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True,
        )
    else:
        st.success("🎉 All checked items match their claimed OSA status!")

    # Expandable Error Log
    errors = st.session_state["error_collector"].get_errors()
    if errors:
        with st.expander(f"⚠️ View Detailed Error Log ({len(errors)} errors)", expanded=False):
            st.dataframe(pd.DataFrame(errors), use_container_width=True)
