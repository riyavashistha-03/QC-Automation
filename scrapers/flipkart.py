"""
Flipkart Scraper — Checks product stock availability on flipkart.com.

Location setting: Pincode-based (entered via the delivery check input on the product page).
Stock detection: Looks for delivery availability text vs "Currently Unavailable".

This is a SKELETON implementation. The selectors need calibration with real Flipkart URLs.
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


class FlipkartScraper(BaseScraper):
    """Scraper for flipkart.com product pages."""

    def __init__(self):
        super().__init__()
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._pincode: str | None = None

    async def initialize(self, location_value) -> None:
        """
        Launch browser. Pincode will be entered per-page on Flipkart.

        Args:
            location_value: Pincode string (e.g., "110001")
        """
        self.logger.info(f"Initializing Flipkart scraper with pincode: {location_value}")

        self._pincode = str(location_value) if location_value else None

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
            "**/*.{png,jpg,jpeg,gif,svg,webp,woff,woff2}",
            lambda route: route.abort()
        )

        self._initialized = True
        self.logger.info("Flipkart scraper initialized")

    async def _enter_pincode(self, page: Page) -> None:
        """Enter pincode on the Flipkart product page delivery check."""
        if not self._pincode:
            return

        try:
            # Flipkart has a pincode input on product pages
            # TODO: Update selectors based on current Flipkart DOM
            pincode_input = page.locator(
                "input[id='pincodeInputRecipient'], "
                "input[placeholder*='pincode' i], "
                "input[placeholder*='Enter Delivery Pincode' i], "
                "input[class*='pincode' i]"
            ).first

            if await pincode_input.is_visible(timeout=5000):
                await pincode_input.clear()
                await pincode_input.fill(self._pincode)
                await random_delay(0.3, 0.8)

                # Click Check button
                check_btn = page.locator(
                    "span:has-text('Check'), button:has-text('Check'), "
                    "[class*='pincode'] span:has-text('Change')"
                ).first
                if await check_btn.is_visible(timeout=2000):
                    await check_btn.click()
                    await random_delay(1.0, 2.0)

                self.logger.debug(f"Pincode {self._pincode} entered on Flipkart page")
            else:
                self.logger.debug("Pincode input not found on page")

        except Exception as e:
            self.logger.debug(f"Could not enter pincode: {e}")

    async def check_stock(self, url: str) -> StockResult:
        """
        Visit a Flipkart product page and determine stock availability.

        SKELETON — selectors need calibration with real URLs.
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

            await random_delay(1.5, 3.0)

            # Close login popup if it appears
            try:
                close_btn = self._page.locator("button._2KpZ6l._2doB4z, button[class*='close']").first
                if await close_btn.is_visible(timeout=2000):
                    await close_btn.click()
                    await random_delay(0.5, 1.0)
            except Exception:
                pass

            # Enter pincode for delivery check
            await self._enter_pincode(self._page)

            page_title = await self._page.title()
            page_text = await self._page.text_content("body") or ""
            page_text_lower = page_text.lower()

            # Check for "Currently Unavailable" — clear OOS indicator on Flipkart
            # TODO: Update selectors
            unavailable = self._page.locator(
                ":text('Currently Unavailable'), :text('currently unavailable'), "
                ":text('Sold Out'), :text('Coming Soon'), "
                "[class*='not-available'], [class*='sold-out']"
            )
            if await unavailable.count() > 0:
                first_el = unavailable.first
                if await first_el.is_visible(timeout=2000):
                    raw = await first_el.text_content() or "Currently Unavailable"
                    return StockResult(
                        status=STATUS_OUT_OF_STOCK,
                        raw_text=raw.strip(),
                        http_status=http_status,
                        page_title=page_title,
                    )

            # Check for "Add to Cart" / "Buy Now" (in stock)
            add_to_cart = self._page.locator(
                "button:has-text('Add to Cart'), button:has-text('ADD TO CART'), "
                "button:has-text('Buy Now'), button:has-text('BUY NOW'), "
                "[class*='add-to-cart'], [class*='buy-now']"
            )
            if await add_to_cart.count() > 0:
                first_btn = add_to_cart.first
                if await first_btn.is_visible(timeout=2000):
                    raw = await first_btn.text_content() or "Add to Cart"
                    return StockResult(
                        status=STATUS_IN_STOCK,
                        raw_text=raw.strip(),
                        http_status=http_status,
                        page_title=page_title,
                    )

            # Fallback: text-based detection
            if "currently unavailable" in page_text_lower or "sold out" in page_text_lower:
                return StockResult(
                    status=STATUS_OUT_OF_STOCK,
                    raw_text="OOS keyword detected in page text",
                    http_status=http_status,
                    page_title=page_title,
                )

            if "add to cart" in page_text_lower or "buy now" in page_text_lower:
                return StockResult(
                    status=STATUS_IN_STOCK,
                    raw_text="Add to Cart keyword detected",
                    http_status=http_status,
                    page_title=page_title,
                )

            return StockResult(
                status=STATUS_ERROR,
                raw_text=page_text[:200].strip(),
                http_status=http_status,
                page_title=page_title,
                error="Could not determine stock status — selectors may need calibration",
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
            self.logger.info("Flipkart scraper cleaned up")
        except Exception as e:
            self.logger.warning(f"Cleanup error: {e}")
        finally:
            self._page = None
            self._context = None
            self._browser = None
            self._playwright = None
            self._initialized = False
