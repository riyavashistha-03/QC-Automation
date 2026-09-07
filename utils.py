"""
Utility functions — User-agent rotation, retry decorator, delay helpers, logging, status normalization.
"""

import asyncio
import logging
import os
import random
import functools
from datetime import datetime


def get_random_user_agent() -> str:
    """Return a consistent modern Windows Chrome user-agent matching Playwright Chromium."""
    uas = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    ]
    return random.choice(uas)


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

    def add(self, sku_id: str, url: str, error_type: str, message: str):
        self.errors.append({
            "sku_id": str(sku_id),
            "url": url,
            "error_type": error_type,
            "message": message,
            "timestamp": datetime.now().isoformat(),
        })

    def get_errors(self) -> list[dict]:
        return self.errors

    def count(self) -> int:
        return len(self.errors)

    def clear(self):
        self.errors.clear()
