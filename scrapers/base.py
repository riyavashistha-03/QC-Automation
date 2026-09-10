"""
Base Scraper — Abstract interface and shared data types for all platform scrapers.

Every platform adapter must:
1. Subclass BaseScraper
2. Implement initialize(), check_stock(), and cleanup()
3. Handle its own location-setting logic inside initialize()
4. Populate scraped_fields with all extractable product data (price, name, stock, etc.)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
import logging


@dataclass
class StockResult:
    """Result of a stock check for a single product URL."""

    status: str
    """Canonical status: 'In Stock', 'Out of Stock', 'Not Available', or 'Error'."""

    raw_text: str = ""
    """The actual text/element found on the page that determined the status."""

    error: Optional[str] = None
    """Raw technical error message if the check failed. None if successful."""

    user_facing_error: Optional[str] = None
    """Clean, user-facing error status ('Page unavailable', 'Invalid/broken URL').
    Only set when there is an error. Shown in the UI instead of the raw error."""

    http_status: Optional[int] = None
    """HTTP status code of the page load, if available."""

    page_title: Optional[str] = None
    """Page title, useful for debugging broken URLs."""

    scraped_fields: dict = field(default_factory=dict)
    """All key/value pairs extracted from the live page.
    Example: {"stock_status": "In Stock", "price": "₹120", "product_name": "..."}
    Used by the multi-field comparison layer in the QC engine."""

    def is_error(self) -> bool:
        return self.status == "Error" or self.error is not None

    def __repr__(self) -> str:
        if self.error:
            return f"StockResult(status='{self.status}', error='{self.error}')"
        return f"StockResult(status='{self.status}', raw='{self.raw_text[:50]}')"


def classify_user_facing_error(error_msg: str | None, http_status: int | None) -> str:
    """
    Map a raw error message + HTTP status to a clean, user-facing error label.

    Returns one of:
        - "Invalid/broken URL" — for 404s and malformed URLs
        - "Page unavailable" — for all other failures (rate limit, timeout, blocked, etc.)
    """
    if http_status == 404:
        return "Invalid/broken URL"

    if error_msg:
        error_lower = str(error_msg).lower()
        if "404" in error_lower:
            return "Invalid/broken URL"
        if "malformed" in error_lower or "invalid url" in error_lower:
            return "Invalid/broken URL"

    # Everything else: 403, 405, 429, timeout, net::ERR, Cloudflare, etc.
    return "Page unavailable"


class BaseScraper(ABC):
    """
    Abstract base class for all platform scrapers.

    Lifecycle:
        scraper = PlatformScraper()
        await scraper.initialize(location_value)  # sets up browser + location
        for url in urls:
            result = await scraper.check_stock(url)
        await scraper.cleanup()

    Or use as an async context manager:
        async with PlatformScraper() as scraper:
            await scraper.initialize(location_value)
            result = await scraper.check_stock(url)
    """

    def __init__(self):
        self.logger = logging.getLogger(f"osa_qc.scraper.{self.__class__.__name__}")
        self._initialized = False

    @abstractmethod
    async def initialize(self, location_value) -> None:
        """
        Set up the browser context and configure the location.

        Args:
            location_value: Platform-specific location data.
                - For pincode-based platforms: str (e.g., "110001")
                - For lat/long platforms: dict (e.g., {"lat": 28.6, "long": 77.2})
        """
        ...

    @abstractmethod
    async def check_stock(self, url: str) -> StockResult:
        """
        Visit a product page and extract the current stock status + all scrapeable fields.

        This method should:
        1. Navigate to the URL
        2. Wait for stock-relevant elements to load
        3. Extract and interpret the stock status
        4. Extract additional fields (price, product name, etc.) into scraped_fields
        5. Return a StockResult

        The caller handles retry logic; this method should raise
        on transient errors (timeouts, network issues) so retries work.

        Args:
            url: Full product page URL

        Returns:
            StockResult with the determined status and scraped_fields
        """
        ...

    @abstractmethod
    async def cleanup(self) -> None:
        """Close browser contexts and release resources."""
        ...

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.cleanup()
        return False

    def _make_error_result(self, error_msg: str, url: str = "",
                           http_status: int | None = None) -> StockResult:
        """Convenience method to create an error StockResult with clean user-facing error."""
        self.logger.error(f"Error checking {url}: {error_msg}")
        user_facing = classify_user_facing_error(error_msg, http_status)
        return StockResult(
            status="Error",
            raw_text="",
            error=error_msg,
            user_facing_error=user_facing,
            http_status=http_status,
        )

    def _make_result(self, status: str, raw_text: str, **kwargs) -> StockResult:
        """Convenience method to create a successful StockResult."""
        self.logger.debug(f"Result: {status} (raw: '{raw_text[:80]}')")
        return StockResult(
            status=status,
            raw_text=raw_text,
            **kwargs,
        )
