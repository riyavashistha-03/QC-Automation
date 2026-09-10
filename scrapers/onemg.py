"""
1mg (Tata 1mg) Scraper — Checks product stock availability on 1mg.com.

Optimized for stealth evasions, concurrency safety, and multi-field extraction.
Extracts: stock status, price (MRP), product name.
"""

import asyncio
import re
from playwright.async_api import async_playwright, Page, BrowserContext, Browser
from playwright_stealth import Stealth

from scrapers.base import BaseScraper, StockResult, classify_user_facing_error
from platform_config import STATUS_IN_STOCK, STATUS_OUT_OF_STOCK, STATUS_NOT_AVAILABLE, STATUS_ERROR
from utils import get_rotating_user_agent, random_delay


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

        # Create initial context — UA will be rotated per-page
        ua = get_rotating_user_agent()
        self._context = await self._browser.new_context(
            user_agent=ua,
            viewport={"width": 1280, "height": 720},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )

        # Block heavy resources to save bandwidth and speed up loads
        await self._context.route(
            "**/*.{png,jpg,jpeg,gif,svg,webp,woff,woff2,ttf,eot}",
            lambda route: route.abort()
        )

        self._initialized = True
        self.logger.info("1mg scraper initialized successfully")

    async def _extract_price(self, page: Page) -> str | None:
        """Extract the product price/MRP from the page."""
        try:
            # Try common 1mg price selectors
            price_selectors = [
                "[class*='PriceBox'] [class*='price']",
                "[class*='price-box'] span",
                "[class*='PriceBoxPlan498__price']",
                "[class*='DrugPriceBox__best-price']",
                "span[class*='mrp']",
                "[class*='price']",
            ]
            for selector in price_selectors:
                el = page.locator(selector).first
                try:
                    if await el.is_visible(timeout=1000):
                        text = await el.text_content()
                        if text and re.search(r'\d', text):
                            return text.strip()
                except Exception:
                    continue

            # Fallback: search page text for price pattern
            page_text = await page.text_content("body") or ""
            price_match = re.search(r'₹\s*[\d,]+(?:\.\d{1,2})?', page_text)
            if price_match:
                return price_match.group(0).strip()

        except Exception as e:
            self.logger.debug(f"Could not extract price: {e}")
        return None

    async def _extract_product_name(self, page: Page) -> str | None:
        """Extract the product name from the page."""
        try:
            name_selectors = [
                "h1[class*='DrugHeader']",
                "h1[class*='ProductTitle']",
                "h1",
            ]
            for selector in name_selectors:
                el = page.locator(selector).first
                try:
                    if await el.is_visible(timeout=1000):
                        text = await el.text_content()
                        if text and len(text.strip()) > 2:
                            return text.strip()
                except Exception:
                    continue
        except Exception as e:
            self.logger.debug(f"Could not extract product name: {e}")
        return None

    async def check_stock(self, url: str) -> StockResult:
        """Visit product page, check stock, and extract all scrapeable fields."""
        if not self._context:
            return self._make_error_result("Scraper not initialized", url)

        page = await self._context.new_page()
        try:
            # Rotate UA per page for anti-fingerprinting
            ua = get_rotating_user_agent()
            await page.set_extra_http_headers({"User-Agent": ua})
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
                    user_facing_error="Invalid/broken URL",
                )

            if http_status == 429:
                return StockResult(
                    status=STATUS_ERROR,
                    raw_text="HTTP error 429",
                    http_status=429,
                    error="HTTP error 429 (Rate limited by Cloudflare)",
                    user_facing_error="Page unavailable",
                )

            if http_status in (403, 405):
                return StockResult(
                    status=STATUS_ERROR,
                    raw_text=f"HTTP {http_status}",
                    http_status=http_status,
                    error=f"HTTP error {http_status} (Blocked by server)",
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

            # Wait for page to render
            await random_delay(1.5, 3.0)

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
                    user_facing_error="Page unavailable",
                )

            if "page not found" in page_text or "404" in page_title.lower():
                return StockResult(
                    status=STATUS_NOT_AVAILABLE,
                    raw_text="Page Not Found",
                    http_status=http_status,
                    page_title=page_title,
                    error="URL broken/404",
                    user_facing_error="Invalid/broken URL",
                )

            # Extract all scrapeable fields
            scraped_fields = {}

            # Extract price
            price = await self._extract_price(page)
            if price:
                scraped_fields["price"] = price

            # Extract product name
            product_name = await self._extract_product_name(page)
            if product_name:
                scraped_fields["product_name"] = product_name

            # Stock status detection
            # 1. Out of Stock indicators
            if any(kw in page_text for kw in [
                "out of stock", "currently unavailable", "notify me", "sold out", "delisted"
            ]):
                scraped_fields["stock_status"] = STATUS_OUT_OF_STOCK
                return StockResult(
                    status=STATUS_OUT_OF_STOCK,
                    raw_text="Detected Out of Stock indicators",
                    http_status=http_status,
                    page_title=page_title,
                    scraped_fields=scraped_fields,
                )

            # 2. In Stock indicators
            if any(kw in page_text for kw in [
                "add to cart", "buy now", "add to bag", "view variants"
            ]):
                scraped_fields["stock_status"] = STATUS_IN_STOCK
                return StockResult(
                    status=STATUS_IN_STOCK,
                    raw_text="Detected Add to Cart / Buy Now indicators",
                    http_status=http_status,
                    page_title=page_title,
                    scraped_fields=scraped_fields,
                )

            return StockResult(
                status=STATUS_ERROR,
                raw_text=page_text[:150].strip(),
                http_status=http_status,
                page_title=page_title,
                error="Could not determine stock status from page content",
                user_facing_error="Page unavailable",
                scraped_fields=scraped_fields,
            )

        except asyncio.TimeoutError:
            return self._make_error_result("Page load timed out", url)
        except Exception as e:
            error_msg = str(e)
            return self._make_error_result(f"Unexpected error: {error_msg}", url)
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
