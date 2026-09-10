"""
Generic Scraper — Fallback adapter for platforms without a dedicated scraper.

This scraper attempts basic stock detection by loading the page and searching
for common stock-related keywords. It will work for some simpler sites but
is not reliable for JS-heavy platforms with anti-bot protection.

Extracts stock status via keyword matching; price/product name extraction
is best-effort via common HTML patterns.
"""

import asyncio
import re
from playwright.async_api import async_playwright, Page, BrowserContext, Browser

from scrapers.base import BaseScraper, StockResult, classify_user_facing_error
from platform_config import STATUS_IN_STOCK, STATUS_OUT_OF_STOCK, STATUS_NOT_AVAILABLE, STATUS_ERROR
from utils import (
    get_rotating_user_agent,
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

    async def initialize(self, location_value=None) -> None:
        """Launch browser. Generic scraper doesn't handle location setting."""
        self.logger.info("Initializing generic scraper (no location setting)")

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=get_stealth_browser_args(),
        )

        ua = get_rotating_user_agent()
        context_opts = get_stealth_context_options(ua)
        self._context = await self._browser.new_context(**context_opts)

        # Block heavy resources
        await self._context.route(
            "**/*.{png,jpg,jpeg,gif,svg,webp,woff,woff2,ttf,eot}",
            lambda route: route.abort()
        )

        self._initialized = True
        self.logger.info("Generic scraper initialized")

    async def _extract_price(self, page: Page) -> str | None:
        """Best-effort price extraction via common patterns."""
        try:
            page_text = await page.text_content("body") or ""
            price_match = re.search(r'₹\s*[\d,]+(?:\.\d{1,2})?', page_text)
            if price_match:
                return price_match.group(0).strip()
        except Exception:
            pass
        return None

    async def _extract_product_name(self, page: Page) -> str | None:
        """Best-effort product name extraction via h1 tag."""
        try:
            el = page.locator("h1").first
            if await el.is_visible(timeout=2000):
                text = await el.text_content()
                if text and len(text.strip()) > 2:
                    return text.strip()
        except Exception:
            pass
        return None

    async def check_stock(self, url: str) -> StockResult:
        """
        Visit a product page and attempt keyword-based stock detection.
        Also extracts price and product name on a best-effort basis.
        """
        if not self._initialized or not self._context:
            return self._make_error_result("Scraper not initialized", url)

        # Create a fresh page per request
        page = await self._context.new_page()
        try:
            # Rotate UA per request
            ua = get_rotating_user_agent()
            await page.set_extra_http_headers({"User-Agent": ua})
            await apply_stealth_scripts(page)

            response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            if response is None:
                return self._make_error_result("No response received", url)

            http_status = response.status

            if http_status == 404:
                return StockResult(
                    status=STATUS_NOT_AVAILABLE,
                    raw_text="404 Not Found",
                    http_status=404,
                    error="URL broken/404",
                    user_facing_error="Invalid/broken URL",
                )

            if http_status in (403, 405, 429):
                return StockResult(
                    status=STATUS_ERROR,
                    raw_text=f"HTTP {http_status}",
                    http_status=http_status,
                    error=f"HTTP error {http_status}",
                    user_facing_error="Page unavailable",
                )

            if http_status >= 400:
                return StockResult(
                    status=STATUS_ERROR,
                    raw_text=f"HTTP {http_status}",
                    http_status=http_status,
                    error=f"HTTP error {http_status}",
                    user_facing_error="Page unavailable",
                )

            await random_delay(2.0, 4.0)

            page_title = await page.title()
            page_text = await page.text_content("body") or ""
            page_text_lower = page_text.lower()

            # Extract all scrapeable fields
            scraped_fields = {}

            price = await self._extract_price(page)
            if price:
                scraped_fields["price"] = price

            product_name = await self._extract_product_name(page)
            if product_name:
                scraped_fields["product_name"] = product_name

            # Check OOS keywords first (higher priority — if OOS is shown, it's OOS)
            for kw in OOS_KEYWORDS:
                if kw in page_text_lower:
                    scraped_fields["stock_status"] = STATUS_OUT_OF_STOCK
                    return StockResult(
                        status=STATUS_OUT_OF_STOCK,
                        raw_text=f"Keyword match: '{kw}'",
                        http_status=http_status,
                        page_title=page_title,
                        scraped_fields=scraped_fields,
                    )

            # Check in-stock keywords
            for kw in IN_STOCK_KEYWORDS:
                if kw in page_text_lower:
                    scraped_fields["stock_status"] = STATUS_IN_STOCK
                    return StockResult(
                        status=STATUS_IN_STOCK,
                        raw_text=f"Keyword match: '{kw}'",
                        http_status=http_status,
                        page_title=page_title,
                        scraped_fields=scraped_fields,
                    )

            return StockResult(
                status=STATUS_ERROR,
                raw_text=page_text[:200].strip(),
                http_status=http_status,
                page_title=page_title,
                error="No dedicated scraper for this platform — keyword detection inconclusive",
                user_facing_error="Page unavailable",
                scraped_fields=scraped_fields,
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
        """Close browser and Playwright instance."""
        try:
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
            self._context = None
            self._browser = None
            self._playwright = None
            self._initialized = False
