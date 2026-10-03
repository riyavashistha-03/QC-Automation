"""
Amazon Now Scraper — Checks product availability on Amazon's quick-commerce
(Amazon Now / instant delivery) section on amazon.in.

IMPORTANT: Amazon Now shares the SAME product URL as regular Amazon (amazon.in).
The difference is that this scraper specifically looks for the "Amazon Now"
/ "Deliver in 2 hours" / instant-delivery widget on the page, NOT the regular
#availability / Add-to-Cart section that the standard AmazonScraper checks.

Stock detection (Amazon Now specific):
  - "Now" widget / "Deliver in 2 hours" button visible and enabled  → In Stock
  - "Now" widget present but shows "Not available" / no slot        → Out of Stock
  - "Now" widget absent entirely (service not in pincode)           → Not Available
  - Regular Add-to-Cart only, no Now section                        → Not Available

Location setting: Same pincode mechanism as AmazonScraper.
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


class AmazonNowScraper(BaseScraper):
    """
    Scraper for Amazon Now (quick-commerce / instant delivery) availability on amazon.in.

    Uses the same product URLs as the regular Amazon platform, but checks the
    "Amazon Now" / "Deliver in 2 hours" section of the page separately from
    the standard delivery Add-to-Cart widget.
    """

    def __init__(self):
        super().__init__()
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._pincode: str | None = None
        self._location_set: bool = False

    async def initialize(self, location_value) -> None:
        """
        Launch browser and set delivery location via pincode.

        Args:
            location_value: Pincode string (e.g., "400001" for Mumbai)
        """
        self.logger.info(f"Initializing AmazonNow scraper with pincode: {location_value}")

        self._pincode = str(location_value) if location_value else None

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=get_stealth_browser_args(),
        )

        ua = get_rotating_user_agent()
        context_opts = get_stealth_context_options(ua)
        self._context = await self._browser.new_context(**context_opts)

        # Block heavy resources to speed up page loads
        await self._context.route(
            "**/*.{png,jpg,jpeg,gif,svg,webp,woff,woff2,ttf,eot}",
            lambda route: route.abort()
        )

        self._initialized = True

        # Set pincode on Amazon's homepage to establish delivery location
        if self._pincode:
            await self._set_pincode_on_homepage()

        self.logger.info("AmazonNow scraper initialized")

    async def _set_pincode_on_homepage(self) -> None:
        """
        Set the delivery pincode via Amazon's location popup on the homepage.
        This persists across subsequent page loads in the same browser context.
        Identical mechanism to AmazonScraper.
        """
        if not self._pincode or not self._context:
            return

        page = await self._context.new_page()
        try:
            ua = get_rotating_user_agent()
            await page.set_extra_http_headers({"User-Agent": ua})
            await apply_stealth_scripts(page)

            await page.goto("https://www.amazon.in", wait_until="domcontentloaded", timeout=30000)
            await random_delay(1.5, 3.0)

            # Click the delivery location link to open the pincode popup
            location_trigger = page.locator(
                "#nav-global-location-popover-link, "
                "#glow-ingress-block, "
                "#nav-packard-glow-loc-icon, "
                "a[data-nav-role='locationSlot']"
            ).first

            try:
                if await location_trigger.is_visible(timeout=5000):
                    await location_trigger.click()
                    await random_delay(1.0, 2.0)
            except Exception as e:
                self.logger.debug(f"Could not click location trigger: {e}")
                return

            # Enter pincode in the popup
            pincode_input = page.locator(
                "input#GLUXZipUpdateInput, "
                "input[data-action='GLUXPostalInputAction'], "
                "input[name='glowZipCode']"
            ).first

            try:
                if await pincode_input.is_visible(timeout=5000):
                    await pincode_input.clear()
                    await pincode_input.fill(self._pincode)
                    await random_delay(0.3, 0.8)

                    # Click Apply button
                    apply_btn = page.locator(
                        "input#GLUXZipUpdate, "
                        "span#GLUXZipUpdate input, "
                        "input[aria-labelledby='GLUXZipUpdate-announce'], "
                        "button:has-text('Apply'), "
                        "input[type='submit'][value='Apply']"
                    ).first

                    if await apply_btn.is_visible(timeout=3000):
                        await apply_btn.click()
                        await random_delay(2.0, 3.0)
                        self._location_set = True
                        self.logger.info(f"AmazonNow pincode set to {self._pincode}")
                    else:
                        # Try pressing Enter as fallback
                        await pincode_input.press("Enter")
                        await random_delay(2.0, 3.0)
                        self._location_set = True
                        self.logger.info(f"AmazonNow pincode set to {self._pincode} (via Enter key)")

                    # Close popup if a "Continue" or "Done" button appears
                    try:
                        done_btn = page.locator(
                            "button[name='glowDoneButton'], "
                            "button:has-text('Continue'), "
                            "button:has-text('Done'), "
                            "input[name='glowDoneButton']"
                        ).first
                        if await done_btn.is_visible(timeout=3000):
                            await done_btn.click()
                            await random_delay(0.5, 1.0)
                    except Exception:
                        pass

                else:
                    self.logger.warning("Pincode input not found in Amazon popup")
            except Exception as e:
                self.logger.warning(f"Could not enter pincode: {e}")

        except Exception as e:
            self.logger.warning(f"Failed to set pincode on Amazon homepage: {e}")
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def _extract_price(self, page: Page) -> str | None:
        """Extract the selling price from the Amazon product page."""
        try:
            price_selectors = [
                "span.a-price span.a-offscreen",
                "#priceblock_dealprice",
                "#priceblock_ourprice",
                "#priceblock_saleprice",
                "span[data-a-color='price'] span.a-offscreen",
                "span.priceToPay span.a-offscreen",
                "#corePrice_feature_div span.a-offscreen",
                "#corePriceDisplay_desktop_feature_div span.a-offscreen",
            ]
            for selector in price_selectors:
                el = page.locator(selector).first
                try:
                    if await el.is_visible(timeout=1500):
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
        """Extract the product title from the Amazon product page."""
        try:
            name_selectors = [
                "#productTitle",
                "#title span",
                "h1#title span",
                "span#productTitle",
            ]
            for selector in name_selectors:
                el = page.locator(selector).first
                try:
                    if await el.is_visible(timeout=2000):
                        text = await el.text_content()
                        if text and len(text.strip()) > 2:
                            return text.strip()
                except Exception:
                    continue
        except Exception as e:
            self.logger.debug(f"Could not extract product name: {e}")
        return None

    async def _check_amazon_now_availability(self, page: Page) -> tuple[str, str]:
        """
        Specifically check the Amazon Now / instant-delivery section of the page.

        Based on observed amazon.in UI (screenshot confirmed), the Amazon Now widget
        is a delivery-option radio-button box on the right side of the product page:
          - Heading : "Amazon Now"  (exact visible text, rendered by JS)
          - Badge   : "now +10 mins" / "now +15 mins"  (teal pill / badge)
          - Status  : "In Stock" in green text within that same box
          - Button  : "Add to cart" inside the Now box

        The widget is JS-rendered AFTER domcontentloaded — so check_stock() already
        calls wait_for_selector before calling this method.

        Detection strategy (layered, most-specific first):
          1. Text-locator — find element whose text IS "Amazon Now" (the heading)
             and inspect its ancestor container for in/out-stock signals
          2. ID / data-attribute search for known Now-section container elements
          3. "now +X mins" badge pattern in page text (very strong signal)
          4. Combined "amazon now" + "in stock" in full-page text (strong signal)
          5. Fallback page-text scan for other Now-related phrases

        Returns:
            (status, raw_text) where status is one of STATUS_IN_STOCK,
            STATUS_OUT_OF_STOCK, or STATUS_NOT_AVAILABLE.
        """
        try:
            # ----------------------------------------------------------------
            # 1. Primary: locate the "Amazon Now" heading and read its container
            # ----------------------------------------------------------------
            # The widget heading is visible text "Amazon Now" on the page.
            # :text-is() is an exact, case-sensitive match in Playwright.
            now_heading_locator = page.locator(
                "span:text-is('Amazon Now'), "
                "div:text-is('Amazon Now'), "
                "label:text-is('Amazon Now'), "
                "h3:text-is('Amazon Now'), "
                "h4:text-is('Amazon Now'), "
                "b:text-is('Amazon Now')"
            ).first

            try:
                if await now_heading_locator.count() > 0:
                    self.logger.info("Found 'Amazon Now' heading element on page")

                    # Walk up the DOM (up to 5 ancestor levels) to find the
                    # delivery-option container and read all its text.
                    container_text = ""
                    for ancestor_level in range(1, 6):
                        try:
                            ancestor = now_heading_locator
                            for _ in range(ancestor_level):
                                ancestor = ancestor.locator("xpath=..")
                            t = (await ancestor.text_content(timeout=2000) or "").lower()
                            # Stop at the first ancestor that has meaningful content
                            # (the delivery-option box will have price + status text)
                            if len(t) > 20:
                                container_text = t
                                break
                        except Exception:
                            continue

                    self.logger.debug(
                        f"Amazon Now container text (200 chars): {container_text[:200]}"
                    )

                    # In-stock phrases expected inside the Amazon Now box
                    in_stock_phrases = [
                        "in stock",
                        "add to cart",
                        "buy now",
                        "mins",           # "now +10 mins" badge
                        "minutes",
                        "free delivery in",
                        "deliver in",
                        "delivery in",
                        "get it in",
                        "available",
                        "qty",            # Qty: 1 dropdown appears when in stock
                    ]
                    for phrase in in_stock_phrases:
                        if phrase in container_text:
                            self.logger.info(
                                f"Amazon Now IN STOCK (heading + container phrase='{phrase}')"
                            )
                            return STATUS_IN_STOCK, f"Amazon Now heading + '{phrase}' in widget"

                    # Out-of-stock phrases inside the Now box
                    oos_phrases = [
                        "currently unavailable",
                        "out of stock",
                        "not available",
                        "temporarily unavailable",
                        "sold out",
                        "not serviceable",
                        "delivery not available",
                    ]
                    for phrase in oos_phrases:
                        if phrase in container_text:
                            self.logger.info(
                                f"Amazon Now OOS (heading + container phrase='{phrase}')"
                            )
                            return STATUS_OUT_OF_STOCK, f"Amazon Now widget: '{phrase}'"

                    # Heading is present but container text has no clear signal.
                    # The widget only renders when the Now service is active for this
                    # pincode, so presence of the heading itself implies In Stock.
                    self.logger.info(
                        "Amazon Now heading found, no explicit OOS signal → In Stock"
                    )
                    return STATUS_IN_STOCK, "Amazon Now widget present on page"

            except Exception as e:
                self.logger.debug(f"Amazon Now heading locator error: {e}")

            # ----------------------------------------------------------------
            # 2. ID / data-attribute search for known Now-section containers
            # ----------------------------------------------------------------
            now_container_selectors = [
                "#now-section",
                "#now-atf-section",
                "#now-delivery-section",
                "#fresh-checkout-atf",
                "[id*='now-section']",
                "[id*='now-delivery']",
                "[id*='amazon-now']",
                "div[data-feature-name='nowSection']",
                "div[data-feature-name='freshCheckoutAtf']",
                "div[data-csa-c-type='nowSection']",
                "#delivery-option-NOW",
                "div[data-csa-c-slot-id='NOW']",
                "[data-delivery-type='NOW']",
                "[data-delivery-type='INSTANT']",
                "#twoHourDelivery",
                "#two-hour-delivery",
                "[id*='two-hour']",
                "[id*='2hour']",
            ]
            now_widget = None
            matched_selector = None
            for selector in now_container_selectors:
                try:
                    el = page.locator(selector).first
                    if await el.count() > 0:
                        now_widget = el
                        matched_selector = selector
                        self.logger.debug(f"Found Now widget via selector: {selector}")
                        break
                except Exception:
                    continue

            if now_widget:
                try:
                    widget_text = (await now_widget.text_content() or "").lower()
                    self.logger.debug(f"Widget text: {widget_text[:150]}")

                    widget_in_stock = [
                        "add to cart", "buy now", "in stock", "deliver in",
                        "delivery in", "mins", "minutes", "get it in",
                        "available", "qty", "free delivery", "amazon now",
                    ]
                    for phrase in widget_in_stock:
                        if phrase in widget_text:
                            self.logger.info(
                                f"Amazon Now IN STOCK via container "
                                f"(selector={matched_selector}, phrase='{phrase}')"
                            )
                            return STATUS_IN_STOCK, f"Amazon Now widget: '{phrase}' found"

                    widget_oos = [
                        "currently unavailable", "not available", "out of stock",
                        "temporarily unavailable", "sold out", "not serviceable",
                        "delivery not available",
                    ]
                    for phrase in widget_oos:
                        if phrase in widget_text:
                            self.logger.info(
                                f"Amazon Now OOS via container "
                                f"(selector={matched_selector}, phrase='{phrase}')"
                            )
                            return STATUS_OUT_OF_STOCK, f"Amazon Now widget: '{phrase}'"
                except Exception:
                    pass

            # ----------------------------------------------------------------
            # 3. "now +X mins" badge pattern — very strong in-stock signal.
            #    The teal "now" pill only renders for products available on Now.
            # ----------------------------------------------------------------
            import re as _re

            # Read full page text once for steps 3-5
            page_text = await page.text_content("body") or ""
            page_text_lower = page_text.lower()

            self.logger.debug(
                f"Page text length: {len(page_text_lower)}. "
                f"'amazon now' present: {'amazon now' in page_text_lower}. "
                f"'in stock' present: {'in stock' in page_text_lower}."
            )

            # "now +10 mins", "now+10mins", "now delivery in 10"
            now_badge_match = _re.search(
                r'now\s*\+?\s*\d+\s*min|now\s*delivery\s*in\s*\d+',
                page_text_lower,
            )
            if now_badge_match:
                self.logger.info(
                    f"Amazon Now IN STOCK via badge pattern: '{now_badge_match.group()}'"
                )
                return STATUS_IN_STOCK, f"Amazon Now badge: '{now_badge_match.group()}'"

            # ----------------------------------------------------------------
            # 4. "amazon now" + "in stock" both present in page text.
            #    This is the key combined signal from the actual UI screenshot.
            # ----------------------------------------------------------------
            if "amazon now" in page_text_lower and "in stock" in page_text_lower:
                self.logger.info(
                    "Amazon Now IN STOCK: 'amazon now' + 'in stock' both in page"
                )
                return STATUS_IN_STOCK, "'Amazon Now' heading + 'In Stock' found on page"

            # ----------------------------------------------------------------
            # 5. "amazon now" present alone — check nearby text for OOS signals
            # ----------------------------------------------------------------
            if "amazon now" in page_text_lower:
                idx = page_text_lower.find("amazon now")
                nearby = page_text_lower[max(0, idx - 50): idx + 400]
                nearby_oos = [
                    "currently unavailable", "out of stock", "not available",
                    "temporarily unavailable", "sold out", "not serviceable",
                ]
                for phrase in nearby_oos:
                    if phrase in nearby:
                        self.logger.info(
                            f"Amazon Now OOS: 'amazon now' + nearby phrase '{phrase}'"
                        )
                        return STATUS_OUT_OF_STOCK, f"Amazon Now: '{phrase}' nearby"

                # "Amazon Now" text is present with no OOS signal → In Stock
                self.logger.info(
                    "Amazon Now IN STOCK: 'amazon now' found, no OOS signal"
                )
                return STATUS_IN_STOCK, "'Amazon Now' text found on page"

            # Explicit "Now not available" messages
            now_na_patterns = [
                "amazon now is not available",
                "now is not available in your area",
                "now delivery is not available",
                "2-hour delivery not available",
            ]
            for pattern in now_na_patterns:
                if pattern in page_text_lower:
                    self.logger.info(f"Amazon Now NOT AVAILABLE: '{pattern}'")
                    return STATUS_NOT_AVAILABLE, f"Page text: '{pattern}'"

            # ----------------------------------------------------------------
            # 6. No Amazon Now section detected anywhere
            # ----------------------------------------------------------------
            self.logger.info("No Amazon Now section found on page → Not Available")
            return STATUS_NOT_AVAILABLE, "Amazon Now section not found on page"

        except Exception as e:
            self.logger.warning(f"Error during Amazon Now availability check: {e}")
            return STATUS_NOT_AVAILABLE, "Error while checking Amazon Now section"

    async def check_stock(self, url: str) -> StockResult:
        """
        Visit an Amazon product page and determine Amazon Now stock availability.

        Uses the SAME URL as regular Amazon but reads a DIFFERENT section of the
        page — the Amazon Now / instant-delivery widget — rather than the
        standard #availability / Add-to-Cart block.
        """
        if not self._initialized or not self._context:
            return self._make_error_result("Scraper not initialized", url)

        page = await self._context.new_page()
        try:
            ua = get_rotating_user_agent()
            await page.set_extra_http_headers({"User-Agent": ua})
            await apply_stealth_scripts(page)

            response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Amazon Now widget is rendered by JavaScript AFTER domcontentloaded.
            # We wait for either the 'Amazon Now' heading text OR the 'now +X mins'
            # badge element to appear — with a generous timeout.
            # If neither appears in 8 s, we fall through to the full-page text scan.
            try:
                await page.wait_for_selector(
                    "span:text-is('Amazon Now'), "
                    "div:text-is('Amazon Now'), "
                    "label:text-is('Amazon Now'), "
                    "#now-section, "
                    "#now-atf-section, "
                    "[data-delivery-type='NOW']",
                    timeout=8000,
                )
                self.logger.debug("Amazon Now widget detected on page (wait_for_selector succeeded)")
            except Exception:
                # Widget didn't appear in 8 s — still run a full text scan below
                self.logger.debug("Amazon Now widget wait timed out — proceeding with text scan")

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

            if http_status in (403, 405, 429, 503):
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

            # Check for CAPTCHA / bot detection
            if page_title and ("sorry" in page_title.lower() or "robot" in page_title.lower()):
                return StockResult(
                    status=STATUS_ERROR,
                    raw_text=page_title,
                    http_status=http_status,
                    error="Amazon bot detection / CAPTCHA",
                    user_facing_error="Page unavailable",
                )

            # Check for Amazon's dog page (custom bot-detection error page)
            page_text = await page.text_content("body") or ""
            if ("To discuss automated access" in page_text
                    or "Sorry, we just need to make sure" in page_text):
                return StockResult(
                    status=STATUS_ERROR,
                    raw_text="Amazon CAPTCHA page",
                    http_status=http_status,
                    error="Amazon bot detection",
                    user_facing_error="Page unavailable",
                )

            # Extract common fields (price, product name)
            scraped_fields: dict = {}

            price = await self._extract_price(page)
            if price:
                scraped_fields["price"] = price

            product_name = await self._extract_product_name(page)
            if product_name:
                scraped_fields["product_name"] = product_name

            # --- Amazon Now specific availability check ---
            # NOTE: This reads a different section of the page than AmazonScraper.
            now_status, now_raw = await self._check_amazon_now_availability(page)
            scraped_fields["stock_status"] = now_status

            return StockResult(
                status=now_status,
                raw_text=now_raw,
                http_status=http_status,
                page_title=page_title,
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
            self.logger.info("AmazonNow scraper cleaned up")
        except Exception as e:
            self.logger.warning(f"Cleanup error: {e}")
        finally:
            self._context = None
            self._browser = None
            self._playwright = None
            self._initialized = False
