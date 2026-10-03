"""
PharmEasy Scraper — Checks product stock availability on pharmeasy.in.

Location setting: Pincode based.
Stock detection: Looks for "Add to Cart" button vs "Out of Stock" / "Notify Me".
Multi-field extraction: stock status, price, product name.
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


class PharmeasyScraper(BaseScraper):
    """Scraper for pharmeasy.in product pages."""

    def __init__(self):
        super().__init__()
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._location_value = None

    async def initialize(self, location_value) -> None:
        """
        Launch browser and set delivery location on PharmEasy.

        Args:
            location_value: Pincode string (e.g., "110001")
        """
        self.logger.info(f"Initializing PharmEasy scraper with location: {location_value}")
        self._location_value = location_value

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

        if location_value:
            setup_page = await self._context.new_page()
            await apply_stealth_scripts(setup_page)
            await self._set_location(setup_page, location_value)
            await setup_page.close()

        self._initialized = True
        self.logger.info("PharmEasy scraper initialized")

    async def _set_location(self, page: Page, pincode: str) -> None:
        """Set delivery pincode on PharmEasy."""
        try:
            await page.goto("https://pharmeasy.in", wait_until="domcontentloaded", timeout=30000)
            await random_delay(1.0, 2.0)

            await page.evaluate(f"""
                localStorage.setItem('pincode', '{pincode}');
                localStorage.setItem('deliveryPincode', '{pincode}');
            """)

            await self._context.add_cookies([
                {"name": "pincode", "value": str(pincode), "domain": ".pharmeasy.in", "path": "/"},
            ])

            self.logger.info(f"PharmEasy location set to pincode={pincode}")
        except Exception as e:
            self.logger.warning(f"Could not set PharmEasy location: {e}")

    async def _extract_price(self, page: Page) -> str | None:
        """Extract the product price from the page."""
        try:
            price_selectors = [
                "[class*='Price']",
                "[class*='price']",
                "[data-testid*='price']",
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
            for selector in ["h1", "[class*='ProductName']", "[class*='product-name']"]:
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
        """Visit a PharmEasy product page and determine stock availability."""
        if not self._initialized or not self._context:
            return self._make_error_result("Scraper not initialized", url)

        page = await self._context.new_page()
        try:
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

            scraped_fields = {}

            price = await self._extract_price(page)
            if price:
                scraped_fields["price"] = price

            product_name = await self._extract_product_name(page)
            if product_name:
                scraped_fields["product_name"] = product_name

            # Check for "Add to Cart" button
            add_btn = page.locator(
                "button:has-text('Add to Cart'), button:has-text('ADD TO CART'), "
                "button:has-text('Add'), [class*='addToCart'], [class*='add-to-cart']"
            )
            if await add_btn.count() > 0:
                first_btn = add_btn.first
                if await first_btn.is_visible(timeout=3000):
                    raw = await first_btn.text_content() or "Add to Cart"
                    scraped_fields["stock_status"] = STATUS_IN_STOCK
                    return StockResult(
                        status=STATUS_IN_STOCK,
                        raw_text=raw.strip(),
                        http_status=http_status,
                        page_title=page_title,
                        scraped_fields=scraped_fields,
                    )

            # Check OOS keywords
            if any(kw in page_text_lower for kw in [
                "out of stock", "notify me", "currently unavailable",
                "not available", "sold out", "not serviceable"
            ]):
                scraped_fields["stock_status"] = STATUS_OUT_OF_STOCK
                return StockResult(
                    status=STATUS_OUT_OF_STOCK,
                    raw_text="OOS keyword detected in page text",
                    http_status=http_status,
                    page_title=page_title,
                    scraped_fields=scraped_fields,
                )

            if any(kw in page_text_lower for kw in ["add to cart", "buy now"]):
                scraped_fields["stock_status"] = STATUS_IN_STOCK
                return StockResult(
                    status=STATUS_IN_STOCK,
                    raw_text="Add to Cart keyword detected in page text",
                    http_status=http_status,
                    page_title=page_title,
                    scraped_fields=scraped_fields,
                )

            return StockResult(
                status=STATUS_ERROR,
                raw_text=page_text[:200].strip(),
                http_status=http_status,
                page_title=page_title,
                error="Could not determine stock status — selectors may need calibration",
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
            self.logger.info("PharmEasy scraper cleaned up")
        except Exception as e:
            self.logger.warning(f"Cleanup error: {e}")
        finally:
            self._context = None
            self._browser = None
            self._playwright = None
            self._initialized = False
