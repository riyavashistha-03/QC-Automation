"""
QC Engine — Orchestrates the fetch → scrape → compare → report pipeline.

Enhanced with:
1. Concurrent parallel async workers (configurable 1-8x speedup)
2. Real-time row-by-row streaming callbacks (`row_callback`)
3. Retry logic with exponential backoff for rate-limit errors
4. Batch processing (BATCH_SIZE rows at a time with pauses between)
5. Multi-field comparison (price, product name, stock — not just OSA)
6. Progress persistence (partial results saved per batch)
7. Clean user-facing error messages (no raw HTTP errors in reports)
"""

import asyncio
import importlib
import logging
from dataclasses import dataclass
from typing import Callable, Optional

import pandas as pd

from platform_config import (
    PLATFORM_CONFIG,
    get_platform_config,
    get_location_value,
    get_field_mapping,
    normalize_osa_status,
    normalize_price,
    STATUS_IN_STOCK,
    STATUS_OUT_OF_STOCK,
    STATUS_ERROR,
)
from scrapers.base import BaseScraper, StockResult, classify_user_facing_error
from scrapers.generic import GenericScraper
from utils import (
    ErrorCollector,
    setup_logger,
    random_delay,
    exponential_backoff_delay,
    get_rotating_user_agent,
    normalize_text_for_comparison,
)

logger = setup_logger("engine")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BATCH_SIZE = 50  # Rows per batch before pausing
LARGE_FILE_THRESHOLD = 100  # Rows above which concurrency is capped
MAX_CONCURRENCY_LARGE = 2  # Max workers for large files
INTER_REQUEST_DELAY_MIN = 2.0  # Min delay between requests per worker (seconds)
INTER_REQUEST_DELAY_MAX = 5.0  # Max delay between requests per worker (seconds)
INTER_BATCH_DELAY_MIN = 5.0  # Min pause between batches (seconds)
INTER_BATCH_DELAY_MAX = 10.0  # Max pause between batches (seconds)
RATE_LIMIT_CODES = {403, 405, 429}  # HTTP codes that trigger exponential backoff


# ---------------------------------------------------------------------------
# Multi-field comparison
# ---------------------------------------------------------------------------

@dataclass
class FieldMismatch:
    """Represents a single field mismatch between claimed and actual values."""
    field_name: str
    claimed: str
    actual: str

    def to_string(self) -> str:
        return f"{self.field_name} mismatch: claimed {self.claimed}, found {self.actual}"


def compare_fields(
    row: pd.Series,
    scraped_fields: dict,
    field_mapping: dict[str, str],
    df_columns: list[str],
) -> list[FieldMismatch]:
    """
    Compare claimed values (from the input row) against actual scraped values
    for every mapped field that exists in BOTH the input and the scrape result.

    Args:
        row: The input data row (pandas Series)
        scraped_fields: Dict of field_key → scraped value from the live page
        field_mapping: Maps input column names → scraper field keys
                       e.g. {"osa_remark": "stock_status", "mrp": "price"}
        df_columns: List of column names in the DataFrame

    Returns:
        List of FieldMismatch objects for every field that doesn't match
    """
    mismatches = []

    for input_col, scrape_key in field_mapping.items():
        # Only compare fields that exist in BOTH the input data and the scrape result
        if input_col not in df_columns:
            continue
        if scrape_key not in scraped_fields:
            continue

        claimed_raw = str(row.get(input_col, "")).strip()
        actual_raw = str(scraped_fields[scrape_key]).strip()

        # Skip empty/NaN values
        if not claimed_raw or claimed_raw.lower() == "nan":
            continue
        if not actual_raw or actual_raw.lower() == "nan":
            continue

        # Field-specific comparison logic
        if scrape_key == "stock_status":
            claimed_normalized = normalize_osa_status(claimed_raw)
            actual_normalized = normalize_osa_status(actual_raw)
            if claimed_normalized != actual_normalized:
                mismatches.append(FieldMismatch(
                    field_name="Stock status",
                    claimed=claimed_raw,
                    actual=actual_raw,
                ))

        elif scrape_key == "price":
            claimed_price = normalize_price(claimed_raw)
            actual_price = normalize_price(actual_raw)
            if claimed_price is not None and actual_price is not None:
                # Allow small float tolerance (₹0.50)
                if abs(claimed_price - actual_price) > 0.50:
                    mismatches.append(FieldMismatch(
                        field_name="Price",
                        claimed=f"₹{claimed_price:.0f}",
                        actual=f"₹{actual_price:.0f}",
                    ))

        else:
            # Generic text comparison (product name, pack size, etc.)
            if normalize_text_for_comparison(claimed_raw) != normalize_text_for_comparison(actual_raw):
                # For product names, only flag if substantially different
                # (minor formatting differences are expected)
                if scrape_key == "product_name":
                    # Simple containment check — if one contains the other, it's close enough
                    c = normalize_text_for_comparison(claimed_raw)
                    a = normalize_text_for_comparison(actual_raw)
                    if c in a or a in c:
                        continue
                mismatches.append(FieldMismatch(
                    field_name=input_col.replace("_", " ").title(),
                    claimed=claimed_raw[:80],
                    actual=actual_raw[:80],
                ))

    return mismatches


def build_remark(mismatches: list[FieldMismatch], result: StockResult) -> str:
    """
    Build the Remark string for a row.

    - If the page failed to load: returns the clean user-facing error
    - If there are mismatches: returns pipe-delimited mismatch descriptions
    - If everything matches: returns "Matched ✅"
    """
    if result.is_error():
        return result.user_facing_error or "Page unavailable"

    if not mismatches:
        return "Matched ✅"

    return " | ".join(m.to_string() for m in mismatches)


# ---------------------------------------------------------------------------
# Scraper instantiation
# ---------------------------------------------------------------------------

def _get_scraper_instance(platform: str) -> BaseScraper:
    """
    Dynamically instantiate the correct scraper for a platform.
    Falls back to GenericScraper if the platform-specific one isn't available.
    """
    config = PLATFORM_CONFIG.get(platform)
    if not config:
        logger.warning(f"No config for platform '{platform}', using GenericScraper")
        return GenericScraper()

    module_name = config.get("scraper_module", f"scrapers.{platform}")
    class_name = config.get("scraper_class", "GenericScraper")

    try:
        module = importlib.import_module(module_name)
        scraper_class = getattr(module, class_name)
        return scraper_class()
    except (ImportError, AttributeError) as e:
        logger.warning(f"Could not load {module_name}.{class_name}: {e}. Using GenericScraper.")
        return GenericScraper()


# ---------------------------------------------------------------------------
# Single-product check with improved retry logic
# ---------------------------------------------------------------------------

async def _check_single_product(
    scraper: BaseScraper,
    url: str,
    max_retries: int = 3,
) -> StockResult:
    """Check stock for a single URL with exponential backoff retry logic."""
    last_result = None
    for attempt in range(1, max_retries + 1):
        try:
            result = await scraper.check_stock(url)

            if not result.is_error():
                return result

            last_result = result

            # Don't retry 404s — those are permanent
            if result.http_status == 404:
                return result

            if attempt < max_retries:
                # Use exponential backoff for rate-limit errors
                if result.http_status in RATE_LIMIT_CODES:
                    wait = exponential_backoff_delay(attempt, base=3.0)
                else:
                    wait = exponential_backoff_delay(attempt, base=1.5)

                logger.warning(
                    f"Retry {attempt}/{max_retries} for {url} in {wait:.1f}s "
                    f"(HTTP {result.http_status})"
                )
                await asyncio.sleep(wait)

        except Exception as e:
            last_result = StockResult(
                status=STATUS_ERROR,
                raw_text="",
                error=str(e),
                user_facing_error=classify_user_facing_error(str(e), None),
            )
            if attempt < max_retries:
                wait = exponential_backoff_delay(attempt, base=2.0)
                await asyncio.sleep(wait)

    return last_result or StockResult(
        status=STATUS_ERROR,
        raw_text="",
        error="All retry attempts exhausted",
        user_facing_error="Page unavailable",
    )


# ---------------------------------------------------------------------------
# Main async QC pipeline
# ---------------------------------------------------------------------------

async def _run_qc_async(
    df: pd.DataFrame,
    platform: str,
    location_id: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    row_callback: Optional[Callable[[dict], None]] = None,
    error_collector: Optional[ErrorCollector] = None,
    max_retries: int = 3,
    concurrency: int = 3,
    batch_callback: Optional[Callable[[list], None]] = None,
    stop_check: Optional[Callable[[], str]] = None,
) -> pd.DataFrame:
    """
    Async implementation of the QC check pipeline with:
    - Batch processing (BATCH_SIZE rows at a time)
    - Concurrent workers with randomized inter-request delays
    - Multi-field comparison (price, name, stock — not just OSA)
    - Live row-by-row streaming callbacks
    - Progress persistence via batch_callback
    """
    config = get_platform_config(platform)
    col_map = config["columns"]
    field_mapping = get_field_mapping(platform)

    url_col = col_map.get("product_url", "pdp_page_url")
    osa_col = col_map.get("osa_remark", "osa_remark")
    sku_col = col_map.get("sku_id", "sku_id")
    loc_col = col_map.get("location_id", "location_id")

    for needed_col, actual_col in [(url_col, "product_url"), (osa_col, "osa_remark")]:
        if needed_col not in df.columns:
            raise ValueError(
                f"Column '{needed_col}' (mapped from '{actual_col}') not found in data. "
                f"Available columns: {list(df.columns)}"
            )

    total = len(df)
    logger.info(f"Starting QC for {total} rows on {platform} (concurrency={concurrency})")

    location_value = None
    if location_id:
        location_value = get_location_value(platform, location_id)

    # Cap concurrency for large files to avoid rate-limiting
    effective_concurrency = concurrency
    if total > LARGE_FILE_THRESHOLD:
        effective_concurrency = min(concurrency, MAX_CONCURRENCY_LARGE)
        logger.info(
            f"Large file detected ({total} rows), capping concurrency to "
            f"{effective_concurrency} workers"
        )

    scraper = _get_scraper_instance(platform)
    await scraper.initialize(location_value)

    all_mismatches = []
    completed_count = 0
    blocked_count = 0
    stopped_reason = None  # "stopped" or "paused" if user interrupted
    lock = asyncio.Lock()

    df_columns = list(df.columns)

    # Split into batches
    rows_list = list(df.iterrows())
    batches = [rows_list[i:i + BATCH_SIZE] for i in range(0, len(rows_list), BATCH_SIZE)]
    num_batches = len(batches)

    logger.info(f"Processing in {num_batches} batch(es) of up to {BATCH_SIZE} rows each")

    try:
        for batch_idx, batch_rows in enumerate(batches):
            # Check stop flag between batches
            if stop_check:
                flag = stop_check()
                if flag in ("stopped", "paused"):
                    stopped_reason = flag
                    logger.info(f"QC {flag} by user after batch {batch_idx}/{num_batches}")
                    break

            batch_num = batch_idx + 1
            logger.info(f"Starting batch {batch_num}/{num_batches} ({len(batch_rows)} rows)")

            # Build queue for this batch
            queue = asyncio.Queue()
            for row_idx, row in batch_rows:
                queue.put_nowait((row_idx, row))

            async def worker(worker_id: int):
                nonlocal completed_count, blocked_count
                # Stagger worker launches slightly to avoid connection burst
                await asyncio.sleep(1.0 * worker_id)

                while not queue.empty():
                    # Check stop flag between rows
                    if stop_check:
                        flag = stop_check()
                        if flag in ("stopped", "paused"):
                            nonlocal stopped_reason
                            stopped_reason = flag
                            # Drain the queue so other workers also stop
                            while not queue.empty():
                                try:
                                    queue.get_nowait()
                                    queue.task_done()
                                except asyncio.QueueEmpty:
                                    break
                            break

                    try:
                        row_idx, row = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

                    url = str(row.get(url_col, ""))
                    db_remark = str(row.get(osa_col, ""))
                    sku_id = str(row.get(sku_col, f"row_{row_idx}"))
                    row_location = str(row.get(loc_col, "")) if loc_col in df_columns else ""
                    display_row_no = row_idx + 1

                    if not url or url == "nan":
                        result = StockResult(
                            status=STATUS_ERROR,
                            raw_text="",
                            error="No product URL provided",
                            user_facing_error="Invalid/broken URL",
                        )
                        mismatches_for_row = []
                        remark = "No product URL provided"
                        if error_collector:
                            error_collector.add(
                                sku_id, url, "missing_url",
                                "No URL provided",
                                user_facing_message="Invalid/broken URL",
                            )
                    else:
                        result = await _check_single_product(
                            scraper, url, max_retries=max_retries
                        )

                        if result.is_error():
                            # Page failed — use clean user-facing error, no field comparison
                            mismatches_for_row = []
                            remark = result.user_facing_error or "Page unavailable"
                            if error_collector:
                                error_collector.add(
                                    sku_id, url, "scrape_error",
                                    result.error or "Unknown error",
                                    user_facing_message=remark,
                                )
                            # Track blocked/retrying count
                            if result.http_status in RATE_LIMIT_CODES:
                                async with lock:
                                    blocked_count += 1
                        else:
                            # Page loaded — run multi-field comparison
                            mismatches_for_row = compare_fields(
                                row, result.scraped_fields, field_mapping, df_columns
                            )
                            remark = build_remark(mismatches_for_row, result)

                    is_match = (
                        not result.is_error()
                        and len(mismatches_for_row) == 0
                    )

                    # Determine result label
                    if is_match:
                        result_label = "MATCHED ✅"
                    elif result.is_error():
                        result_label = "ERROR ⚠️"
                    else:
                        result_label = "MISMATCH ❌"

                    row_info = {
                        "Row": display_row_no,
                        "SKU ID": sku_id,
                        "Location": row_location,
                        "Claimed Status": db_remark,
                        "Actual Status": result.status if not result.is_error() else (
                            result.user_facing_error or "Page unavailable"
                        ),
                        "Result": result_label,
                        "Remark": remark,
                        "URL": url,
                    }

                    async with lock:
                        completed_count += 1
                        if not is_match:
                            mismatch_row = row.copy()
                            mismatch_row["Actual_Status"] = result.status
                            mismatch_row["Remark"] = remark
                            if row_location:
                                mismatch_row["Location"] = row_location
                            all_mismatches.append(mismatch_row)

                        if row_callback:
                            row_callback(row_info)

                        if progress_callback:
                            progress_callback(completed_count, total)

                    queue.task_done()

                    # Randomized inter-request delay per worker
                    await random_delay(INTER_REQUEST_DELAY_MIN, INTER_REQUEST_DELAY_MAX)

            # Launch workers for this batch
            num_workers = min(effective_concurrency, len(batch_rows))
            workers = [asyncio.create_task(worker(i)) for i in range(num_workers)]
            await asyncio.gather(*workers)

            # Persist partial results after each batch
            if batch_callback:
                batch_callback(all_mismatches.copy())

            # Pause between batches (skip after last batch)
            if batch_idx < num_batches - 1:
                logger.info(
                    f"Batch {batch_num} complete. Pausing before next batch..."
                )
                await random_delay(INTER_BATCH_DELAY_MIN, INTER_BATCH_DELAY_MAX)

        if stopped_reason:
            logger.info(
                f"QC {stopped_reason}: {completed_count}/{total} rows processed, "
                f"{len(all_mismatches)} mismatches found"
            )
        else:
            logger.info(f"QC complete: {len(all_mismatches)} mismatches out of {total} rows")
        if all_mismatches:
            return pd.DataFrame(all_mismatches)
        else:
            return pd.DataFrame()
    finally:
        await scraper.cleanup()


# ---------------------------------------------------------------------------
# Synchronous wrapper
# ---------------------------------------------------------------------------

def run_qc(
    df: pd.DataFrame,
    platform: str,
    location_id: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    row_callback: Optional[Callable[[dict], None]] = None,
    error_collector: Optional[ErrorCollector] = None,
    max_retries: int = 3,
    concurrency: int = 3,
    batch_callback: Optional[Callable[[list], None]] = None,
    stop_check: Optional[Callable[[], str]] = None,
) -> pd.DataFrame:
    """
    Synchronous wrapper for the QC check pipeline.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(
                    asyncio.run,
                    _run_qc_async(
                        df, platform, location_id,
                        progress_callback, row_callback, error_collector,
                        max_retries, concurrency, batch_callback, stop_check
                    )
                )
                return future.result()
        else:
            return loop.run_until_complete(
                _run_qc_async(
                    df, platform, location_id,
                    progress_callback, row_callback, error_collector,
                    max_retries, concurrency, batch_callback, stop_check
                )
            )
    except RuntimeError:
        return asyncio.run(
            _run_qc_async(
                df, platform, location_id,
                progress_callback, row_callback, error_collector,
                max_retries, concurrency, batch_callback, stop_check
            )
        )
