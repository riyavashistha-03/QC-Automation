"""
QC Engine — Orchestrates the fetch → scrape → compare → report pipeline.

Optimized with:
1. Concurrent parallel async workers (configurable 1-8x speedup)
2. Real-time row-by-row streaming callbacks (`row_callback`)
3. Retry logic with rate-limit exponential backoff
"""

import asyncio
import importlib
import logging
from typing import Callable, Optional

import pandas as pd

from platform_config import (
    PLATFORM_CONFIG,
    get_platform_config,
    get_location_value,
    normalize_osa_status,
    STATUS_IN_STOCK,
    STATUS_OUT_OF_STOCK,
    STATUS_ERROR,
)
from scrapers.base import BaseScraper, StockResult
from scrapers.generic import GenericScraper
from utils import ErrorCollector, setup_logger

logger = setup_logger("engine")


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


def _generate_remark(db_status: str, actual_status: str, result: StockResult) -> str:
    """Generate a human-readable remark describing the mismatch."""
    if result.error:
        if "404" in str(result.error):
            return "URL broken/404"
        if "timed out" in str(result.error).lower():
            return "Could not verify — page load timed out"
        return f"Could not verify — {result.error}"

    db_normalized = normalize_osa_status(db_status)
    actual_normalized = normalize_osa_status(actual_status)

    if db_normalized == STATUS_OUT_OF_STOCK and actual_normalized == STATUS_IN_STOCK:
        return "Marked Out of Stock, found In Stock"
    elif db_normalized == STATUS_IN_STOCK and actual_normalized == STATUS_OUT_OF_STOCK:
        return "Marked In Stock, found Out of Stock"
    elif actual_normalized == STATUS_ERROR:
        return f"Could not verify — {result.raw_text[:100]}"
    else:
        return f"Status mismatch: DB='{db_status}' vs Actual='{actual_status}'"


def _statuses_match(db_remark: str, actual_status: str) -> bool:
    """Check if the database remark and actual status agree."""
    db_normalized = normalize_osa_status(db_remark)
    actual_normalized = normalize_osa_status(actual_status)

    if db_normalized == STATUS_IN_STOCK and actual_normalized == STATUS_IN_STOCK:
        return True
    if db_normalized == STATUS_OUT_OF_STOCK and actual_normalized == STATUS_OUT_OF_STOCK:
        return True
    if actual_normalized == STATUS_ERROR:
        return False
    return False


async def _check_single_product(
    scraper: BaseScraper,
    url: str,
    max_retries: int = 3,
) -> StockResult:
    """Check stock for a single URL with retry logic."""
    last_result = None
    for attempt in range(1, max_retries + 1):
        try:
            result = await scraper.check_stock(url)

            if not result.is_error():
                return result

            last_result = result
            if result.http_status == 404:
                return result

            if attempt < max_retries:
                wait = 5.0 * attempt if result.http_status == 429 else 1.5 * attempt
                logger.warning(f"Retry {attempt}/{max_retries} for {url} in {wait:.1f}s")
                await asyncio.sleep(wait)

        except Exception as e:
            last_result = StockResult(
                status=STATUS_ERROR,
                raw_text="",
                error=str(e),
            )
            if attempt < max_retries:
                await asyncio.sleep(2.0 * attempt)

    return last_result or StockResult(
        status=STATUS_ERROR,
        raw_text="",
        error="All retry attempts exhausted",
    )


async def _run_qc_async(
    df: pd.DataFrame,
    platform: str,
    location_id: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    row_callback: Optional[Callable[[dict], None]] = None,
    error_collector: Optional[ErrorCollector] = None,
    max_retries: int = 3,
    concurrency: int = 3,
) -> pd.DataFrame:
    """
    Async implementation of the QC check pipeline with concurrent workers
    and live row-by-row streaming callbacks.
    """
    config = get_platform_config(platform)
    col_map = config["columns"]

    url_col = col_map.get("product_url", "pdp_page_url")
    osa_col = col_map.get("osa_remark", "osa_remark")
    sku_col = col_map.get("sku_id", "sku_id")

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

    scraper = _get_scraper_instance(platform)
    await scraper.initialize(location_value)

    mismatches = []
    completed_count = 0
    lock = asyncio.Lock()

    # Build queue of items
    queue = asyncio.Queue()
    for row_idx, row in df.iterrows():
        queue.put_nowait((row_idx, row))

    async def worker(worker_id: int):
        nonlocal completed_count
        # Stagger worker launches slightly (0.8s * worker_id) to avoid connection burst
        await asyncio.sleep(0.8 * worker_id)

        while not queue.empty():
            try:
                row_idx, row = queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            url = str(row.get(url_col, ""))
            db_remark = str(row.get(osa_col, ""))
            sku_id = str(row.get(sku_col, f"row_{row_idx}"))
            display_row_no = row_idx + 1

            if not url or url == "nan":
                result_status = "Error"
                remark = "No product URL provided"
                if error_collector:
                    error_collector.add(sku_id, url, "missing_url", "No URL provided")
                is_match = False
            else:
                result = await _check_single_product(
                    scraper, url, max_retries=max_retries
                )
                result_status = result.status
                is_match = _statuses_match(db_remark, result_status)
                remark = "Matched ✅" if is_match else _generate_remark(db_remark, result_status, result)

                if result.is_error() and error_collector:
                    error_collector.add(sku_id, url, "scrape_error", result.error or "Unknown error")

            row_info = {
                "Row": display_row_no,
                "SKU ID": sku_id,
                "Claimed Status": db_remark,
                "Actual Status": result_status,
                "Result": "MATCHED ✅" if is_match else ("ERROR ⚠️" if result_status == "Error" else "MISMATCH ❌"),
                "Remark": remark,
                "URL": url,
            }

            async with lock:
                completed_count += 1
                if not is_match:
                    mismatch_row = row.copy()
                    mismatch_row["Actual_Status"] = result_status
                    mismatch_row["Remark"] = remark
                    mismatches.append(mismatch_row)

                if row_callback:
                    row_callback(row_info)

                if progress_callback:
                    progress_callback(completed_count, total)

            queue.task_done()
            # Jitter delay between requests per worker
            await asyncio.sleep(1.0)

    try:
        num_workers = min(concurrency, total) if total > 0 else 1
        workers = [asyncio.create_task(worker(i)) for i in range(num_workers)]
        await asyncio.gather(*workers)

        logger.info(f"QC complete: {len(mismatches)} mismatches out of {total} rows")
        if mismatches:
            return pd.DataFrame(mismatches)
        else:
            return pd.DataFrame()
    finally:
        await scraper.cleanup()


def run_qc(
    df: pd.DataFrame,
    platform: str,
    location_id: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    row_callback: Optional[Callable[[dict], None]] = None,
    error_collector: Optional[ErrorCollector] = None,
    max_retries: int = 3,
    concurrency: int = 3,
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
                        max_retries, concurrency
                    )
                )
                return future.result()
        else:
            return loop.run_until_complete(
                _run_qc_async(
                    df, platform, location_id,
                    progress_callback, row_callback, error_collector,
                    max_retries, concurrency
                )
            )
    except RuntimeError:
        return asyncio.run(
            _run_qc_async(
                df, platform, location_id,
                progress_callback, row_callback, error_collector,
                max_retries, concurrency
            )
        )
