"""
Generic Scraper — Fallback adapter for platforms without a dedicated scraper.

This scraper attempts basic stock detection by loading the page and searching
for common stock-related keywords. It will work for some simpler sites but
is not reliable for JS-heavy platforms with anti-bot protection.
"""

import asyncio
from playwright.async_api import async_playwright, Page, BrowserContext, Browser

from scrapers.base import BaseScraper, StockResult
from platform_config import STATUS_IN_STOCK, STATUS_OUT_OF_STOCK, STATUS_NOT_AVAILABLE, STATUS_ERROR
from utils import (
    get_random_user_agent,
    get_stealth_browser_args,
    get_stealth_context_options,
    apply_stealth_scripts,
    random_delay,
)

# Keywords that indicate stock status
IN_STOCK_KEYWORDS = [
    "add to cart", "buy now", "add to bag", "add", "in stock",
    "available", "order now", "shop now",
]
OOS_KEYWORDS = [
    "out of stock", "sold out", "currently unavailable", "notify me",
    "not available", "unavailable", "coming soon", "out of stock",
    "no longer available", "discontinued",
]


class GenericScraper(BaseScraper):
    """Fallback scraper using keyword-based stock detection."""

    def __init__(self):
        super().__init__()
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    async def initialize(self, location_value=None) -> None:
        """Launch browser. Generic scraper doesn't handle location setting."""
        self.logger.info("Initializing generic scraper (no location setting)")

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=get_stealth_browser_args(),
        )

        ua = get_random_user_agent()
        context_opts = get_stealth_context_options(ua)
        self._context = await self._browser.new_context(**context_opts)
        self._page = await self._context.new_page()

        await apply_stealth_scripts(self._page)

        # Block heavy resources
        await self._context.route(
            "**/*.{png,jpg,jpeg,gif,svg,webp,woff,woff2,ttf,eot}",
            lambda route: route.abort()
        )

        self._initialized = True
        self.logger.info("Generic scraper initialized")

    async def check_stock(self, url: str) -> StockResult:
        """
        Visit a product page and attempt keyword-based stock detection.
        """
        if not self._initialized or not self._page:
            return self._make_error_result("Scraper not initialized", url)

        try:
            response = await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)

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

            if http_status >= 400:
                return StockResult(
                    status=STATUS_ERROR,
                    raw_text=f"HTTP {http_status}",
                    http_status=http_status,
                    error=f"HTTP error {http_status}",
                )

            await random_delay(2.0, 4.0)

            page_title = await self._page.title()
            page_text = await self._page.text_content("body") or ""
            page_text_lower = page_text.lower()

            # Check OOS keywords first (higher priority — if OOS is shown, it's OOS)
            for kw in OOS_KEYWORDS:
                if kw in page_text_lower:
                    return StockResult(
                        status=STATUS_OUT_OF_STOCK,
                        raw_text=f"Keyword match: '{kw}'",
                        http_status=http_status,
                        page_title=page_title,
                    )

            # Check in-stock keywords
            for kw in IN_STOCK_KEYWORDS:
                if kw in page_text_lower:
                    return StockResult(
                        status=STATUS_IN_STOCK,
                        raw_text=f"Keyword match: '{kw}'",
                        http_status=http_status,
                        page_title=page_title,
                    )

            return StockResult(
                status=STATUS_ERROR,
                raw_text=page_text[:200].strip(),
                http_status=http_status,
                page_title=page_title,
                error="No dedicated scraper for this platform — keyword detection inconclusive",
            )

        except asyncio.TimeoutError:
            return self._make_error_result("Page load timed out", url)
        except Exception as e:
            return self._make_error_result(f"Unexpected error: {str(e)}", url)

    async def cleanup(self) -> None:
        """Close browser and Playwright instance."""
        try:
            if self._page:
                await self._page.close()
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
            self.logger.info("Generic scraper cleaned up")
        except Exception as e:
            self.logger.warning(f"Cleanup error: {e}")
        finally:
            self._page = None
            self._context = None
            self._browser = None
            self._playwright = None
            self._initialized = False
