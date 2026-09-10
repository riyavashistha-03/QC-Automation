"""
Blinkit Scraper — Checks product stock availability on blinkit.com.

Location setting: Lat/Long based (Blinkit uses dark stores; stock varies by coordinates).
Stock detection: Looks for "Add" / "Add to cart" button vs "Notify Me" / "Out of Stock".
Multi-field extraction: stock status, price, product name.

This is a SKELETON implementation. The selectors and location-setting flow need to be
calibrated with real Blinkit product URLs once sample data is provided.
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


class BlinkitScraper(BaseScraper):
    """Scraper for blinkit.com product pages."""

    def __init__(self):
        super().__init__()
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._location_value = None

    async def initialize(self, location_value) -> None:
        """
        Launch browser and set the delivery location on Blinkit.

        Args:
            location_value: Dict with "lat" and "long" keys
                           (e.g., {"lat": 28.6139, "long": 77.2090})
        """
        self.logger.info(f"Initializing Blinkit scraper with location: {location_value}")
        self._location_value = location_value

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=get_stealth_browser_args(),
        )

        ua = get_rotating_user_agent()
        context_opts = get_stealth_context_options(ua)

        # Blinkit needs geolocation permissions
        if isinstance(location_value, dict) and "lat" in location_value:
            context_opts["geolocation"] = {
                "latitude": location_value["lat"],
                "longitude": location_value["long"],
            }
            context_opts["permissions"] = ["geolocation"]

        self._context = await self._browser.new_context(**context_opts)

        # Block heavy resources
        await self._context.route(
            "**/*.{png,jpg,jpeg,gif,svg,webp,woff,woff2,ttf,eot}",
            lambda route: route.abort()
        )

        # Set location via cookies/localStorage on a one-time page
        if isinstance(location_value, dict) and "lat" in location_value:
            setup_page = await self._context.new_page()
            await apply_stealth_scripts(setup_page)
            await self._set_location(setup_page, location_value)
            await setup_page.close()

        self._initialized = True
        self.logger.info("Blinkit scraper initialized")

    async def _set_location(self, page: Page, location: dict) -> None:
        """
        Set location on Blinkit.

        Blinkit uses lat/long to determine the nearest dark store.
        This tries cookie injection first, then falls back to UI interaction.
        """
        try:
            lat = location["lat"]
            lng = location["long"]

            # Navigate to homepage to establish session
            await page.goto("https://blinkit.com", wait_until="domcontentloaded", timeout=30000)
            await random_delay(1.0, 2.0)

            # Try setting via localStorage (Blinkit stores location data locally)
            await page.evaluate(f"""
                localStorage.setItem('lat', '{lat}');
                localStorage.setItem('lng', '{lng}');
                localStorage.setItem('latitude', '{lat}');
                localStorage.setItem('longitude', '{lng}');
            """)

            # Set via cookies
            await self._context.add_cookies([
                {"name": "lat", "value": str(lat), "domain": ".blinkit.com", "path": "/"},
                {"name": "lon", "value": str(lng), "domain": ".blinkit.com", "path": "/"},
                {"name": "lng", "value": str(lng), "domain": ".blinkit.com", "path": "/"},
            ])

            self.logger.info(f"Blinkit location set to lat={lat}, lng={lng}")

        except Exception as e:
            self.logger.warning(f"Could not set Blinkit location: {e}")

    async def _extract_price(self, page: Page) -> str | None:
        """Extract the product price from the page."""
        try:
            price_selectors = [
                "[class*='Product__pricing'] span",
                "[class*='price']",
                "[class*='Price']",
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

            # Fallback: regex in page text
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
                "h1[class*='Product__title']",
                "h1",
                "[class*='ProductName']",
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
        """
        Visit a Blinkit product page and determine stock availability.
        Extracts price and product name in addition to stock status.

        SKELETON — selectors need calibration with real URLs.
        """
        if not self._initialized or not self._context:
            return self._make_error_result("Scraper not initialized", url)

        # Create a fresh page per request to avoid stale sessions
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

            await random_delay(2.0, 4.0)  # Blinkit is JS-heavy, needs more time

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

            # Check for "Add" button (Blinkit's add to cart)
            # TODO: Update selectors with real Blinkit product page structure
            add_btn = page.locator(
                "button:has-text('Add'), button:has-text('ADD'), "
                "[class*='AddToCart'], [class*='add-to-cart']"
            )
            if await add_btn.count() > 0:
                first_btn = add_btn.first
                if await first_btn.is_visible(timeout=3000):
                    raw = await first_btn.text_content() or "Add"
                    scraped_fields["stock_status"] = STATUS_IN_STOCK
                    return StockResult(
                        status=STATUS_IN_STOCK,
                        raw_text=raw.strip(),
                        http_status=http_status,
                        page_title=page_title,
                        scraped_fields=scraped_fields,
                    )

            # Check for OOS indicators
            if any(kw in page_text_lower for kw in [
                "currently unavailable", "out of stock", "notify me",
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

            # Check for in-stock keywords
            if "add" in page_text_lower:
                scraped_fields["stock_status"] = STATUS_IN_STOCK
                return StockResult(
                    status=STATUS_IN_STOCK,
                    raw_text="Add keyword detected in page text",
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
            self.logger.info("Blinkit scraper cleaned up")
        except Exception as e:
            self.logger.warning(f"Cleanup error: {e}")
        finally:
            self._context = None
            self._browser = None
            self._playwright = None
            self._initialized = False
