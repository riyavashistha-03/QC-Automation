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

if "dark_mode" not in st.session_state:
    theme_param = st.query_params.get("theme", "")
    st.session_state["dark_mode"] = (theme_param == "dark")

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


def on_theme_change():
    """Sync dark mode state with URL query parameter for persistence."""
    if st.session_state.get("dark_mode"):
        st.query_params["theme"] = "dark"
    else:
        st.query_params["theme"] = "light"


# ---------------------------------------------------------------------------
# Theme & Custom Styling (Light / Dark Mode)
# ---------------------------------------------------------------------------

is_dark = st.session_state.get("dark_mode", False)

if is_dark:
    st.markdown("""
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">

<style>
/* Font */
.stApp, .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6,
.stApp p, .stApp label, .stApp .stMarkdown, .stApp span {
    font-family: 'Inter', sans-serif !important;
}

/* App Background & Base Layout */
.stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
    background-color: #0E1117 !important;
    color: #E2E8F0 !important;
}

/* Header */
header[data-testid="stHeader"] {
    background-color: rgba(14, 17, 23, 0.9) !important;
}

/* Sidebar */
[data-testid="stSidebar"], [data-testid="stSidebar"] > div:first-child {
    background-color: #161B22 !important;
    border-right: 1px solid #30363D !important;
}
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3,
[data-testid="stSidebar"] h4, [data-testid="stSidebar"] h5, [data-testid="stSidebar"] h6 {
    color: #F8FAFC !important;
}
[data-testid="stSidebar"] p, [data-testid="stSidebar"] label {
    color: #CBD5E1 !important;
}
[data-testid="stSidebar"] hr {
    border-color: #30363D !important;
}

/* Hero Header */
.hero-header {
    background: linear-gradient(135deg, #18192E 0%, #221D49 50%, #2C215A 100%) !important;
    border-radius: 14px;
    padding: 1.8rem 2.2rem;
    margin-bottom: 1.2rem;
    border: 1px solid #3F3A73 !important;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4) !important;
}
.hero-header h1 {
    font-size: 1.7rem !important;
    font-weight: 700 !important;
    margin-bottom: 0.25rem !important;
    color: #F5F3FF !important;
}
.hero-header p {
    font-size: 0.92rem !important;
    color: #C4B5FD !important;
    margin: 0 !important;
}

/* Section Label */
.section-label {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    margin: 0.6rem 0 0.4rem 0;
}
.section-label .num {
    background: linear-gradient(135deg, #7C6FE0, #9B8FFF) !important;
    color: white !important;
    width: 24px; height: 24px;
    border-radius: 7px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-size: 0.78rem;
    font-weight: 700;
}
.section-label .text {
    font-size: 1.05rem;
    font-weight: 600;
    color: #EDE9FE !important;
}

/* Live Badge */
.live-badge {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    background: rgba(99, 102, 241, 0.15) !important;
    border: 1px solid #6366F1 !important;
    border-radius: 8px;
    padding: 0.4rem 0.9rem;
    margin-bottom: 0.6rem;
}
.live-badge .dot {
    width: 8px; height: 8px;
    background: #818CF8 !important;
    border-radius: 50%;
    animation: pulse 1.4s ease-in-out infinite;
}
.live-badge span {
    font-size: 0.88rem;
    font-weight: 600;
    color: #C7D2FE !important;
}
@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
}

/* Metric cards */
[data-testid="stMetric"] {
    background-color: #161B22 !important;
    border: 1px solid #30363D !important;
    border-radius: 10px;
    padding: 0.6rem 0.8rem;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.25) !important;
}
[data-testid="stMetricLabel"] p {
    color: #94A3B8 !important;
}
[data-testid="stMetricValue"] div {
    color: #F8FAFC !important;
}

/* BaseWeb Selects & MultiSelects */
div[data-baseweb="select"] > div {
    background-color: #1F242C !important;
    border-color: #374151 !important;
    color: #F8FAFC !important;
    border-radius: 8px !important;
}
div[data-baseweb="select"] span {
    color: #F8FAFC !important;
}
div[data-baseweb="tag"] {
    background-color: #2D3340 !important;
    border: 1px solid #434B5D !important;
}
div[data-baseweb="tag"] span {
    color: #EDE9FE !important;
}
div[data-baseweb="popover"], div[data-baseweb="menu"], ul[role="listbox"] {
    background-color: #1F242C !important;
    border: 1px solid #374151 !important;
}
li[role="option"] {
    background-color: #1F242C !important;
    color: #E2E8F0 !important;
}
li[role="option"]:hover, li[role="option"][aria-selected="true"] {
    background-color: #312E81 !important;
    color: #FFFFFF !important;
}

/* Inputs */
input[type="text"], input[type="number"], .stTextInput input, .stNumberInput input {
    background-color: #1F242C !important;
    border: 1px solid #374151 !important;
    color: #F8FAFC !important;
    border-radius: 8px !important;
}
input[type="text"]:focus, input[type="number"]:focus, .stTextInput input:focus, .stNumberInput input:focus {
    border-color: #7C6FE0 !important;
    box-shadow: 0 0 0 1px #7C6FE0 !important;
}
button[data-testid="stNumberInputStepUp"], button[data-testid="stNumberInputStepDown"] {
    background-color: #242936 !important;
    color: #CBD5E1 !important;
    border-color: #374151 !important;
}

/* Radio & Checkbox */
[data-testid="stRadio"] label p, [data-testid="stCheckbox"] label p {
    color: #E2E8F0 !important;
}

/* File Uploader */
[data-testid="stFileUploader"] section {
    background-color: #161B22 !important;
    border: 1px dashed #374151 !important;
    border-radius: 10px !important;
}
[data-testid="stFileUploader"] section * {
    color: #94A3B8 !important;
}
[data-testid="stFileUploader"] button {
    background-color: #21262D !important;
    color: #F8FAFC !important;
    border: 1px solid #374151 !important;
}

/* Buttons */
button[data-testid="baseButton-secondary"] {
    background-color: #21262D !important;
    color: #F0F6FC !important;
    border: 1px solid #363B42 !important;
    border-radius: 8px !important;
}
button[data-testid="baseButton-secondary"]:hover {
    background-color: #30363D !important;
    border-color: #8B949E !important;
    color: #FFFFFF !important;
}
button[data-testid="baseButton-primary"] {
    background: linear-gradient(135deg, #7C6FE0, #9084F5) !important;
    color: #FFFFFF !important;
    border: none !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
    box-shadow: 0 4px 14px rgba(124, 111, 224, 0.35) !important;
}
.stDownloadButton > button {
    background: linear-gradient(135deg, #7C6FE0, #9084F5) !important;
    color: white !important;
    border: none !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
    box-shadow: 0 4px 14px rgba(124, 111, 224, 0.35) !important;
}

/* Dataframe & Tables */
[data-testid="stDataFrame"], [data-testid="stTable"] {
    background-color: #161B22 !important;
    border: 1px solid #30363D !important;
    border-radius: 10px !important;
}

/* Tabs */
button[data-baseweb="tab"] {
    color: #94A3B8 !important;
}
button[data-baseweb="tab"][aria-selected="true"] {
    color: #A79BFD !important;
}
div[data-baseweb="tab-highlight"] {
    background-color: #7C6FE0 !important;
}

/* Expanders */
[data-testid="stExpander"] {
    background-color: #161B22 !important;
    border: 1px solid #30363D !important;
    border-radius: 10px !important;
}
[data-testid="stExpander"] summary {
    color: #E2E8F0 !important;
}
[data-testid="stExpander"] summary:hover {
    color: #A79BFD !important;
}

/* Sliders */
[data-testid="stSlider"] div {
    color: #E2E8F0 !important;
}

/* Alerts */
.stAlert {
    border-radius: 10px !important;
    background-color: #161B22 !important;
    border: 1px solid #30363D !important;
}

/* Text & Captions */
.stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6,
.stApp p, .stApp label, .stApp .stMarkdown {
    color: #E2E8F0;
}
.stCaptionContainer p, caption, .stCaption {
    color: #94A3B8 !important;
}
hr {
    border-color: #30363D !important;
}
</style>
""", unsafe_allow_html=True)
else:
    st.markdown("""
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">

<style>
/* Font */
.stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp h5, .stApp h6,
.stApp p, .stApp label, .stApp .stMarkdown {
    font-family: 'Inter', sans-serif !important;
}

/* Hero Header */
.hero-header {
    background: linear-gradient(135deg, #E8E0FF 0%, #D4C6FF 40%, #C4B5FD 100%);
    border-radius: 14px;
    padding: 1.8rem 2.2rem;
    margin-bottom: 1.2rem;
    border: 1px solid #DDD6FE;
}
.hero-header h1 {
    font-size: 1.7rem !important;
    font-weight: 700 !important;
    margin-bottom: 0.25rem !important;
    color: #2D2B55 !important;
}
.hero-header p {
    font-size: 0.92rem !important;
    color: #5B5589 !important;
    margin: 0 !important;
}

/* Section Label */
.section-label {
    display: inline-flex;
    align-items: center;
    gap: 0.5rem;
    margin: 0.6rem 0 0.4rem 0;
}
.section-label .num {
    background: linear-gradient(135deg, #7C6FE0, #9B8FFF);
    color: white;
    width: 24px; height: 24px;
    border-radius: 7px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-size: 0.78rem;
    font-weight: 700;
}
.section-label .text {
    font-size: 1.05rem;
    font-weight: 600;
    color: #2D2B55;
}

/* Live Badge */
.live-badge {
    display: inline-flex;
    align-items: center;
    gap: 0.45rem;
    background: #EEF2FF;
    border: 1px solid #C7D2FE;
    border-radius: 8px;
    padding: 0.4rem 0.9rem;
    margin-bottom: 0.6rem;
}
.live-badge .dot {
    width: 8px; height: 8px;
    background: #6366F1;
    border-radius: 50%;
    animation: pulse 1.4s ease-in-out infinite;
}
.live-badge span { font-size: 0.88rem; font-weight: 600; color: #3730A3; }
@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
}

/* Metric cards */
[data-testid="stMetric"] {
    border: 1px solid #E9E5F5;
    border-radius: 10px;
    padding: 0.6rem 0.8rem;
}

/* Download button */
.stDownloadButton > button {
    background-color: #7C6FE0 !important;
    color: white !important;
    border: none !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
}

/* Rounded alerts */
.stAlert { border-radius: 10px !important; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.markdown("""
<div class="hero-header">
    <h1>🔍 OSA Quality Check Automation</h1>
    <p>Cross-check claimed product data (stock status, price, etc.) against live e-commerce pages.</p>
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Sidebar Settings / Database Health
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### 🎨 Display")
    st.toggle(
        "🌙 Dark Mode",
        key="dark_mode",
        on_change=on_theme_change,
        help="Switch between Light and Dark visual themes",
    )
    st.markdown("---")
    st.header("⚙️ Settings")

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
    st.caption("OSA QC Engine v2.0 · Multi-field · Batched · Anti-block")


# ---------------------------------------------------------------------------
# Main Controls Form
# ---------------------------------------------------------------------------

st.markdown('<div class="section-label"><span class="num">1</span><span class="text">Configure QC Run</span></div>', unsafe_allow_html=True)

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
st.markdown('<div class="section-label"><span class="num">2</span><span class="text">Sampling Mode</span></div>', unsafe_allow_html=True)

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
    st.markdown('<div class="live-badge"><div class="dot"></div><span>Live Verification Stream</span></div>', unsafe_allow_html=True)
    
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
    st.markdown('<div class="section-label"><span class="num">3</span><span class="text">Final Mismatch Report & Export</span></div>', unsafe_allow_html=True)

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
    else:
        st.success("🎉 All checked items match their claimed data!")

    # Download Report Button — always available when results exist
    report_buffer = excel_handler.generate_report(
        results_dict,
        sample_info=sample_info,
    )
    report_filename = excel_handler.get_report_filename(list(results_dict.keys()))

    st.download_button(
        label="📥 Download QC Report (Excel)",
        data=report_buffer,
        file_name=report_filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
        use_container_width=True,
    )

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
