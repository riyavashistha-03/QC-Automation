"""
OSA QC Automation — Streamlit UI

Main application interface supporting:
1. Data source selection (Database vs Excel upload)
2. Platform & Location filtering
3. Random sampling mode (check N random SKUs instead of all)
4. Parallel execution with live streaming row-by-row updates
5. Batch processing with progress persistence
6. Multi-field mismatch results (price, stock, name)
7. Formatted Excel report download with sample info
8. Clean user-facing error log (no raw HTTP errors)
"""

import random
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
if "partial_results" not in st.session_state:
    st.session_state["partial_results"] = None
if "sample_info" not in st.session_state:
    st.session_state["sample_info"] = None
if "stop_flag" not in st.session_state:
    st.session_state["stop_flag"] = None  # None, "stopped", or "paused"


# ---------------------------------------------------------------------------
# Header & Styling
# ---------------------------------------------------------------------------

st.title("🔍 OSA Quality Check Automation")
st.caption(
    "Cross-check claimed product data (stock status, price, etc.) "
    "against live e-commerce pages."
)

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
        help=(
            "Higher values check multiple items concurrently. "
            "For large files (100+ rows), this is auto-capped to 2 "
            "to avoid rate-limiting."
        ),
    )
    
    max_retries = st.number_input(
        "Max retries per URL",
        min_value=1,
        max_value=5,
        value=3,
        help="Uses exponential backoff between retries.",
    )

    st.markdown("---")
    st.caption("OSA QC Engine v2.0 (Multi-field, Batched, Anti-block)")


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
        help="Select platforms to perform QC verification on.",
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
        help="Ensure columns match the expected platform column names.",
    )

# ---------------------------------------------------------------------------
# Random Sampling Controls
# ---------------------------------------------------------------------------

st.markdown("---")
st.subheader("2. Sampling Mode")

col_sample_toggle, col_sample_size = st.columns([1, 1])

with col_sample_toggle:
    sampling_mode = st.radio(
        "SKU Selection",
        options=["Check all SKUs", "Check random sample"],
        horizontal=True,
        help="Use random sampling for large files where a full audit isn't needed.",
    )

sample_size_input = None
if sampling_mode == "Check random sample":
    with col_sample_size:
        sample_size_input = st.number_input(
            "How many random SKUs to check",
            min_value=1,
            max_value=10000,
            value=50,
            step=10,
            help="The sample is pulled AFTER platform and location filters.",
        )

st.markdown("---")

# ---------------------------------------------------------------------------
# Run QC Execution with Live Streaming Feed
# ---------------------------------------------------------------------------

col_run, col_stop = st.columns([3, 1])
with col_run:
    run_button = st.button("🚀 Run QC Check", type="primary", use_container_width=True)
with col_stop:
    stop_button = st.button("🛑 Stop QC", type="secondary", use_container_width=True)

if stop_button:
    st.session_state["stop_flag"] = "stopped"
    st.warning("⏹️ Stop requested — the current batch will finish, then QC will halt.")

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
    st.session_state["partial_results"] = {}
    st.session_state["sample_info"] = None
    st.session_state["stop_flag"] = None  # Reset stop flag on new run

    # Progress & Live Inspection Container
    st.markdown("### 🛰️ Live Verification Stream")
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    # Live summary counters (5 columns now: total, matches, mismatches, errors, blocked)
    col_m1, col_m2, col_m3, col_m4, col_m5 = st.columns(5)
    metric_total = col_m1.empty()
    metric_matches = col_m2.empty()
    metric_mismatches = col_m3.empty()
    metric_errors = col_m4.empty()
    metric_blocked = col_m5.empty()
    
    # Live streaming table
    st.caption("👇 Real-time row-by-row inspection feed (updates as each item completes):")
    live_feed_placeholder = st.empty()

    results_by_platform = {}
    live_stream_rows = []
    
    stats = {
        "checked": 0,
        "total": 0,
        "matches": 0,
        "mismatches": 0,
        "errors": 0,
        "blocked": 0,
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

        # Apply random sampling if selected
        actual_sample_size = len(df_input)
        is_sample = False

        if sampling_mode == "Check random sample" and sample_size_input is not None:
            is_sample = True
            filtered_count = len(df_input)

            if sample_size_input >= filtered_count:
                # Sample size >= available — check all and notify
                st.info(
                    f"Only {filtered_count} SKUs match your filters for "
                    f"{display_name} — checking all {filtered_count}."
                )
                actual_sample_size = filtered_count
            else:
                # Sample the requested number
                actual_sample_size = sample_size_input
                df_input = df_input.sample(n=actual_sample_size, random_state=None)
                df_input = df_input.reset_index(drop=True)

            # Get the SKU column for logging
            sku_col = PLATFORM_CONFIG[platform_key]["columns"].get("sku_id", "sku_id")
            sampled_skus = []
            if sku_col in df_input.columns:
                sampled_skus = df_input[sku_col].astype(str).tolist()

            st.session_state["sample_info"] = {
                "is_sample": True,
                "sample_size": actual_sample_size,
                "total_available": filtered_count,
                "sampled_skus": sampled_skus,
            }

        stats["total"] += len(df_input)
        status_text.text(
            f"Running QC on {len(df_input)} items for {display_name} "
            f"using {concurrency} parallel workers "
            f"(batches of 50)..."
        )

        def update_progress(current, total):
            overall_pct = (p_idx + (current / total)) / len(target_platform_keys)
            progress_bar.progress(min(overall_pct, 1.0))
            blocked = stats['blocked']
            blocked_str = f", {blocked} blocked/retrying" if blocked > 0 else ""
            status_text.text(
                f"[{display_name}] Checked {stats['checked'] + current} / "
                f"{stats['total']}{blocked_str}"
                f" ({overall_pct * 100:.0f}% total)"
            )

        def on_row_complete(row_info: dict):
            stats["checked"] += 1
            if row_info["Result"] == "MATCHED ✅":
                stats["matches"] += 1
            elif row_info["Result"] == "MISMATCH ❌":
                stats["mismatches"] += 1
            else:
                stats["errors"] += 1
                # Track blocked count from error status
                remark = str(row_info.get("Remark", ""))
                if "page unavailable" in remark.lower():
                    stats["blocked"] += 1

            live_stream_rows.append(row_info)
            
            # Update metrics live
            metric_total.metric("Items Checked", f"{stats['checked']} / {stats['total']}")
            metric_matches.metric("Matches ✅", stats["matches"])
            metric_mismatches.metric("Mismatches ❌", stats["mismatches"])
            metric_errors.metric("Errors ⚠️", stats["errors"])
            metric_blocked.metric("Blocked 🚫", stats["blocked"])

            # Update live feed table (show last 25 rows)
            df_live = pd.DataFrame(live_stream_rows)
            display_cols = [
                c for c in ["Row", "SKU ID", "Location", "Claimed Status",
                            "Actual Status", "Result", "Remark", "URL"]
                if c in df_live.columns
            ]
            live_feed_placeholder.dataframe(
                df_live.tail(25)[display_cols],
                use_container_width=True,
                hide_index=True,
            )

        def on_batch_complete(partial_mismatches):
            """Persist partial results after each batch."""
            st.session_state["partial_results"][platform_key] = (
                pd.DataFrame(partial_mismatches) if partial_mismatches
                else pd.DataFrame()
            )

        # Run QC Engine with Live Streaming Callback
        loc_arg = (
            selected_locations[0]
            if len(selected_locations) == 1
            and selected_locations[0] != "All Locations"
            else None
        )

        def check_stop_flag() -> str | None:
            """Called by the engine between rows — returns 'stopped'/'paused' or None."""
            return st.session_state.get("stop_flag")

        mismatches_df = run_qc(
            df=df_input,
            platform=platform_key,
            location_id=loc_arg,
            progress_callback=update_progress,
            row_callback=on_row_complete,
            error_collector=st.session_state["error_collector"],
            max_retries=max_retries,
            concurrency=concurrency,
            batch_callback=on_batch_complete,
            stop_check=check_stop_flag,
        )

        results_by_platform[platform_key] = mismatches_df

    was_stopped = st.session_state.get("stop_flag") in ("stopped", "paused")
    progress_bar.progress(1.0)
    if was_stopped:
        status_text.text("⏹️ Quality Check Stopped — partial results saved.")
        st.warning("QC was stopped early. Partial results are shown below.")
    else:
        status_text.text("✅ Quality Check Completed!")
    st.session_state["qc_results"] = results_by_platform
    st.session_state["is_running"] = False
    st.session_state["stop_flag"] = None  # Reset for next run


# ---------------------------------------------------------------------------
# Final Results View & Excel Report Download
# ---------------------------------------------------------------------------

if st.session_state["qc_results"] is not None:
    st.markdown("---")
    st.subheader("3. Final Mismatch Report & Export")

    # Show sample info banner if applicable
    sample_info = st.session_state.get("sample_info")
    if sample_info and sample_info.get("is_sample"):
        st.info(
            f"📊 **Sample run**: {sample_info['sample_size']} of "
            f"{sample_info['total_available']} SKUs randomly selected. "
            f"This is not a full audit."
        )

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
        report_buffer = excel_handler.generate_report(
            results_dict,
            sample_info=sample_info,
        )
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
        st.success("🎉 All checked items match their claimed data!")

    # User-facing Error Log (clean messages only)
    errors = st.session_state["error_collector"].get_user_facing_errors()
    if errors:
        with st.expander(
            f"⚠️ Issues Summary ({len(errors)} items could not be verified)",
            expanded=False,
        ):
            st.dataframe(pd.DataFrame(errors), use_container_width=True)

    # Debug Error Log (raw technical details, collapsed)
    raw_errors = st.session_state["error_collector"].get_errors()
    if raw_errors:
        with st.expander("🔧 Debug Log (raw technical errors)", expanded=False):
            st.dataframe(pd.DataFrame(raw_errors), use_container_width=True)

# ---------------------------------------------------------------------------
# Show partial results if a previous run was interrupted
# ---------------------------------------------------------------------------

if (
    st.session_state.get("partial_results")
    and not st.session_state.get("is_running")
    and st.session_state.get("qc_results") is None
):
    st.markdown("---")
    st.warning("⚠️ A previous run was interrupted. Partial results are available:")
    partial = st.session_state["partial_results"]
    for p_key, df_partial in partial.items():
        if not df_partial.empty:
            display_name = PLATFORM_CONFIG.get(p_key, {}).get("display_name", p_key)
            st.write(f"**{display_name}**: {len(df_partial)} mismatches found so far")
            st.dataframe(df_partial, use_container_width=True, hide_index=True)
