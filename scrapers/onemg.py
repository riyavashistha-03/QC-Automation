"""
1mg (Tata 1mg) Scraper — Checks product stock availability on 1mg.com.

Optimized for stealth evasions and concurrency safety.
"""

import asyncio
from playwright.async_api import async_playwright, Page, BrowserContext, Browser
from playwright_stealth import Stealth

from scrapers.base import BaseScraper, StockResult
from platform_config import STATUS_IN_STOCK, STATUS_OUT_OF_STOCK, STATUS_NOT_AVAILABLE, STATUS_ERROR
from utils import get_random_user_agent


class OneMgScraper(BaseScraper):
    """Scraper for 1mg.com product pages."""

    def __init__(self):
        super().__init__()
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def initialize(self, location_value=None) -> None:
        """Launch browser with stealth evasions."""
        self.logger.info(f"Initializing 1mg scraper (location: {location_value})")

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
        )

        ua = get_random_user_agent()
        self._context = await self._browser.new_context(
            user_agent=ua,
            viewport={"width": 1280, "height": 720},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )

        self._initialized = True
        self.logger.info("1mg scraper initialized successfully")

    async def check_stock(self, url: str) -> StockResult:
        """Visit product page and check stock availability using a dedicated page."""
        if not self._context:
            return self._make_error_result("Scraper not initialized", url)

        page = await self._context.new_page()
        try:
            await Stealth().apply_stealth_async(page)
            response = await page.goto(url, wait_until="domcontentloaded", timeout=25000)

            if response is None:
                return self._make_error_result("No response received", url)

            http_status = response.status

            if http_status == 404:
                return StockResult(
                    status=STATUS_NOT_AVAILABLE,
                    raw_text="404 Not Found",
                    http_status=404,
                    error="URL broken/404",
                )

            if http_status == 429:
                return StockResult(
                    status=STATUS_ERROR,
                    raw_text="HTTP error 429",
                    http_status=429,
                    error="HTTP error 429 (Rate limited by Cloudflare)",
                )

            if http_status >= 400:
                return StockResult(
                    status=STATUS_ERROR,
                    raw_text=f"HTTP {http_status}",
                    http_status=http_status,
                    error=f"HTTP error {http_status}",
                )

            page_title = await page.title()
            page_text = (await page.text_content("body") or "").lower()

            # Cloudflare challenge check
            if "access denied" in page_title.lower() or "cloudflare" in page_title.lower():
                return StockResult(
                    status=STATUS_ERROR,
                    raw_text="Cloudflare challenge page",
                    http_status=429,
                    page_title=page_title,
                    error="HTTP error 429 (Cloudflare Access Denied)",
                )

            if "page not found" in page_text or "404" in page_title.lower():
                return StockResult(
                    status=STATUS_NOT_AVAILABLE,
                    raw_text="Page Not Found",
                    http_status=http_status,
                    page_title=page_title,
                    error="URL broken/404",
                )

            # Stock status detection
            # 1. Out of Stock indicators
            if any(kw in page_text for kw in [
                "out of stock", "currently unavailable", "notify me", "sold out", "delisted"
            ]):
                return StockResult(
                    status=STATUS_OUT_OF_STOCK,
                    raw_text="Detected Out of Stock indicators",
                    http_status=http_status,
                    page_title=page_title,
                )

            # 2. In Stock indicators
            if any(kw in page_text for kw in [
                "add to cart", "buy now", "add to bag", "view variants"
            ]):
                return StockResult(
                    status=STATUS_IN_STOCK,
                    raw_text="Detected Add to Cart / Buy Now indicators",
                    http_status=http_status,
                    page_title=page_title,
                )

            return StockResult(
                status=STATUS_ERROR,
                raw_text=page_text[:150].strip(),
                http_status=http_status,
                page_title=page_title,
                error="Could not determine stock status from page content",
            )

        except asyncio.TimeoutError:
            return self._make_error_result("Page load timed out", url)
        except Exception as e:
            return self._make_error_result(f"Unexpected error: {str(e)}", url)
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def cleanup(self) -> None:
        """Close browser instance."""
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
            self.logger.info("1mg scraper cleaned up")
        except Exception as e:
            self.logger.warning(f"Cleanup error: {e}")
        finally:
            self._context = None
            self._browser = None
            self._playwright = None
            self._initialized = False
