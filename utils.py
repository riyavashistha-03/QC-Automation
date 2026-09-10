"""
Utility functions — User-agent rotation, retry decorator, delay helpers, logging, status normalization.
"""

import asyncio
import logging
import os
import random
import re
import functools
from datetime import datetime


# ---------------------------------------------------------------------------
# User-Agent Pool (diverse browsers/OS combinations to avoid fingerprinting)
# ---------------------------------------------------------------------------

_USER_AGENTS = [
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    # Chrome on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Edge on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    # Firefox on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    # Firefox on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:123.0) Gecko/20100101 Firefox/123.0",
    # Chrome on Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Safari on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
]

# Global counter for deterministic rotation
_ua_counter = 0


def get_random_user_agent() -> str:
    """Return a random user-agent string from the pool."""
    return random.choice(_USER_AGENTS)


def get_rotating_user_agent() -> str:
    """
    Return a user-agent that cycles deterministically through the pool.
    Avoids repeating the same UA on consecutive requests.
    """
    global _ua_counter
    ua = _USER_AGENTS[_ua_counter % len(_USER_AGENTS)]
    _ua_counter += 1
    return ua


def get_stealth_browser_args() -> list[str]:
    """Return Chromium launch arguments that help avoid bot detection."""
    return [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
    ]


def get_stealth_context_options(user_agent: str | None = None) -> dict:
    """Return browser context options that help avoid bot detection."""
    ua = user_agent or get_random_user_agent()
    return {
        "user_agent": ua,
        "viewport": {"width": 1280, "height": 720},
        "locale": "en-IN",
        "timezone_id": "Asia/Kolkata",
    }


async def apply_stealth_scripts(page) -> None:
    """Inject JS to hide automation indicators."""
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-IN', 'en-US', 'en'] });
        window.chrome = { runtime: {} };
    """)


# ---------------------------------------------------------------------------
# Random delay
# ---------------------------------------------------------------------------

async def random_delay(min_s: float = 1.0, max_s: float = 3.0) -> None:
    """Async sleep for a random duration between min_s and max_s seconds."""
    delay = random.uniform(min_s, max_s)
    await asyncio.sleep(delay)


def random_delay_sync(min_s: float = 1.0, max_s: float = 3.0) -> None:
    """Synchronous sleep for a random duration between min_s and max_s seconds."""
    import time
    delay = random.uniform(min_s, max_s)
    time.sleep(delay)


# ---------------------------------------------------------------------------
# Exponential backoff
# ---------------------------------------------------------------------------

def exponential_backoff_delay(attempt: int, base: float = 3.0, max_delay: float = 60.0) -> float:
    """
    Calculate exponential backoff delay with jitter.

    Args:
        attempt: The retry attempt number (0-indexed)
        base: Base delay in seconds
        max_delay: Maximum delay cap in seconds

    Returns:
        Delay in seconds: base * 2^attempt + random jitter (0–2s)
    """
    delay = base * (2 ** attempt) + random.uniform(0, 2.0)
    return min(delay, max_delay)


# ---------------------------------------------------------------------------
# Price normalization for multi-field comparison
# ---------------------------------------------------------------------------

def normalize_price(raw: str) -> float | None:
    """
    Normalize a price string to a float for comparison.
    Handles ₹, Rs, commas, and whitespace.

    Examples:
        '₹120.00' → 120.0
        'Rs. 1,450' → 1450.0
        'MRP ₹ 999' → 999.0
        '' → None
    """
    if not raw:
        return None
    raw = str(raw).strip()
    # Remove currency symbols and text
    cleaned = re.sub(r'[₹$]', '', raw)
    cleaned = re.sub(r'(?i)(rs\.?|mrp|inr)', '', cleaned)
    cleaned = cleaned.replace(',', '').strip()
    # Extract the first number (int or float)
    match = re.search(r'(\d+(?:\.\d+)?)', cleaned)
    if match:
        return float(match.group(1))
    return None


def normalize_text_for_comparison(raw: str) -> str:
    """
    Normalize text for fuzzy field comparison.
    Strips whitespace, lowercases, removes extra spaces.
    """
    if not raw:
        return ""
    return re.sub(r'\s+', ' ', str(raw).strip().lower())


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logger(name: str, log_dir: str = "logs") -> logging.Logger:
    """Configure a logger with both file and console handlers."""
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger(f"osa_qc.{name}")
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_fmt = logging.Formatter("[%(levelname)s] %(name)s: %(message)s")
    console_handler.setFormatter(console_fmt)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_handler = logging.FileHandler(
        os.path.join(log_dir, f"{name}_{timestamp}.log"),
        encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(file_fmt)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger


# ---------------------------------------------------------------------------
# Error log collector
# ---------------------------------------------------------------------------

class ErrorCollector:
    """Collects errors during a QC run for display in the UI."""

    def __init__(self):
        self.errors: list[dict] = []

    def add(self, sku_id: str, url: str, error_type: str, message: str,
            user_facing_message: str | None = None):
        """
        Add an error to the collector.

        Args:
            sku_id: SKU identifier
            url: Product URL that failed
            error_type: Technical error category
            message: Raw technical error message (for debug log)
            user_facing_message: Clean message for the user (shown in UI)
        """
        self.errors.append({
            "sku_id": str(sku_id),
            "url": url,
            "error_type": error_type,
            "message": message,
            "user_facing_message": user_facing_message or "Page unavailable",
            "timestamp": datetime.now().isoformat(),
        })

    def get_errors(self) -> list[dict]:
        """Get all errors (includes raw technical details)."""
        return self.errors

    def get_user_facing_errors(self) -> list[dict]:
        """Get errors with only clean, user-facing messages."""
        return [
            {
                "sku_id": e["sku_id"],
                "url": e["url"],
                "status": e["user_facing_message"],
                "timestamp": e["timestamp"],
            }
            for e in self.errors
        ]

    def count(self) -> int:
        return len(self.errors)

    def clear(self):
        self.errors.clear()
