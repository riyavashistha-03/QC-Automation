"""
Amazon Scraper — Checks product stock availability on amazon.in.

Location setting: Pincode-based via Amazon's delivery location popup.
Stock detection:
  - "Add to Cart" / "Buy Now" → In Stock
  - "Currently unavailable" → Out of Stock
  - "Temporarily out of stock" → Out of Stock
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


class AmazonScraper(BaseScraper):
    """Scraper for amazon.in product pages with pincode-based delivery check."""

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
        self.logger.info(f"Initializing Amazon scraper with pincode: {location_value}")

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

        # Set pincode on Amazon's homepage to establish the delivery location
        if self._pincode:
            await self._set_pincode_on_homepage()

        self.logger.info("Amazon scraper initialized")

    async def _set_pincode_on_homepage(self) -> None:
        """
        Set the delivery pincode via Amazon's location popup on the homepage.
        This persists across subsequent page loads in the same browser context.
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
                        self.logger.info(f"Amazon pincode set to {self._pincode}")
                    else:
                        # Try pressing Enter as fallback
                        await pincode_input.press("Enter")
                        await random_delay(2.0, 3.0)
                        self._location_set = True
                        self.logger.info(f"Amazon pincode set to {self._pincode} (via Enter key)")

                    # Close the popup if a "Continue" or "Done" button appears
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
            # Amazon uses specific elements for pricing
            price_selectors = [
                # Whole price element
                "span.a-price span.a-offscreen",
                # Deal price
                "#priceblock_dealprice",
                "#priceblock_ourprice",
                "#priceblock_saleprice",
                # Price on newer layouts
                "span[data-a-color='price'] span.a-offscreen",
                "span.priceToPay span.a-offscreen",
                # Fallback
                "#corePrice_feature_div span.a-offscreen",
                "#corePriceDisplay_desktop_feature_div span.a-offscreen",
            ]

            for selector in price_selectors:
                el = page.locator(selector).first
                try:
                    if await el.is_visible(timeout=1500):
                        text = await el.text_content()
                        if text and re.search(r'\d', text):
                            # Clean up: "₹339.00" → "₹339"
                            return text.strip()
                except Exception:
                    continue

            # Fallback: regex in visible text
            page_text = await page.text_content("body") or ""
            # Look for the first ₹ price pattern
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

    async def check_stock(self, url: str) -> StockResult:
        """
        Visit an Amazon product page and determine stock availability.
        Extracts price and product name in addition to stock status.

        Uses the pre-configured pincode for delivery location.
        """
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
                # Amazon frequently returns HTTP 404 to headless/bot browsers
                # even for live in-stock products (bot-detection mechanism).
                # We treat this as a scraping error (not a definitive 'Not Available')
                # so it appears in the error log rather than as a false MISMATCH.
                return StockResult(
                    status=STATUS_ERROR,
                    raw_text="404 Not Found",
                    http_status=404,
                    error="HTTP 404 — bot-blocked or URL broken",
                    user_facing_error="Page unavailable",
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

            await random_delay(1.0, 2.0)

            # Amazon's key buy-box elements (Add to Cart, availability, price) are
            # rendered by JavaScript AFTER domcontentloaded. Wait for them before
            # reading page text to avoid false 'not available' results.
            try:
                await page.wait_for_selector(
                    "#add-to-cart-button, "
                    "#buy-now-button, "
                    "#availability, "
                    "#availabilityInsideBuyBox_feature_div, "
                    "#outOfStock",
                    timeout=8000,
                )
            except Exception:
                # Element didn't appear — page may be bot-blocked or slow;
                # continue and let the keyword scan below determine status.
                self.logger.debug("Buy-box wait timed out — continuing with text scan")

            await random_delay(1.0, 2.0)

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

            # Check for dog page (Amazon's custom error page)
            page_text = await page.text_content("body") or ""
            if "To discuss automated access" in page_text or "Sorry, we just need to make sure" in page_text:
                return StockResult(
                    status=STATUS_ERROR,
                    raw_text="Amazon CAPTCHA page",
                    http_status=http_status,
                    error="Amazon bot detection",
                    user_facing_error="Page unavailable",
                )

            # Detect Amazon's soft-404 / 'Page Not Found' content page
            # (returns HTTP 200 but shows a 'Sorry, page not found' message)
            soft_404_phrases = [
                "sorry! we couldn't find that page",
                "the page you were looking for doesn't exist",
                "we can't seem to find the page you're looking for",
                "looks like this page is missing",
            ]
            page_text_lower_check = page_text.lower()
            if any(p in page_text_lower_check for p in soft_404_phrases):
                return StockResult(
                    status=STATUS_ERROR,
                    raw_text="Amazon soft-404 page",
                    http_status=http_status,
                    error="Amazon page not found (soft 404)",
                    user_facing_error="Page unavailable",
                )

            page_text_lower = page_text.lower()

            # Extract all scrapeable fields
            scraped_fields = {}

            price = await self._extract_price(page)
            if price:
                scraped_fields["price"] = price

            product_name = await self._extract_product_name(page)
            if product_name:
                scraped_fields["product_name"] = product_name

            # --- Stock status detection (Amazon-specific) ---

            # 1. Check for "Currently unavailable" (strongest OOS signal)
            currently_unavailable = page.locator(
                "#availabilityInsideBuyBox_feature_div:has-text('Currently unavailable'), "
                "#availability span:has-text('Currently unavailable'), "
                "#availability:has-text('Currently unavailable'), "
                "#outOfStock span"
            ).first
            try:
                if await currently_unavailable.is_visible(timeout=2000):
                    text = await currently_unavailable.text_content() or "Currently unavailable"
                    scraped_fields["stock_status"] = STATUS_OUT_OF_STOCK
                    return StockResult(
                        status=STATUS_OUT_OF_STOCK,
                        raw_text=text.strip(),
                        http_status=http_status,
                        page_title=page_title,
                        scraped_fields=scraped_fields,
                    )
            except Exception:
                pass

            # 2. Check for "Temporarily out of stock"
            temp_oos = page.locator(
                "#availability span:has-text('Temporarily out of stock'), "
                "#availability:has-text('Temporarily out of stock')"
            ).first
            try:
                if await temp_oos.is_visible(timeout=1500):
                    text = await temp_oos.text_content() or "Temporarily out of stock"
                    scraped_fields["stock_status"] = STATUS_OUT_OF_STOCK
                    return StockResult(
                        status=STATUS_OUT_OF_STOCK,
                        raw_text=text.strip(),
                        http_status=http_status,
                        page_title=page_title,
                        scraped_fields=scraped_fields,
                    )
            except Exception:
                pass

            # 3. Check for "Add to Cart" button (strongest in-stock signal)
            add_to_cart = page.locator(
                "#add-to-cart-button, "
                "input#add-to-cart-button, "
                "#addToCart input[type='submit'], "
                "span#submit\\.add-to-cart-announce"
            ).first
            try:
                if await add_to_cart.is_visible(timeout=3000):
                    scraped_fields["stock_status"] = STATUS_IN_STOCK
                    return StockResult(
                        status=STATUS_IN_STOCK,
                        raw_text="Add to Cart button found",
                        http_status=http_status,
                        page_title=page_title,
                        scraped_fields=scraped_fields,
                    )
            except Exception:
                pass

            # 4. Check for "Buy Now" button
            buy_now = page.locator(
                "#buy-now-button, "
                "input#buy-now-button, "
                "span#submit\\.buy-now-announce"
            ).first
            try:
                if await buy_now.is_visible(timeout=1500):
                    scraped_fields["stock_status"] = STATUS_IN_STOCK
                    return StockResult(
                        status=STATUS_IN_STOCK,
                        raw_text="Buy Now button found",
                        http_status=http_status,
                        page_title=page_title,
                        scraped_fields=scraped_fields,
                    )
            except Exception:
                pass

            # 5. Check availability text in the #availability div
            availability_el = page.locator("#availability span").first
            try:
                if await availability_el.is_visible(timeout=2000):
                    avail_text = (await availability_el.text_content() or "").strip().lower()
                    if "in stock" in avail_text:
                        scraped_fields["stock_status"] = STATUS_IN_STOCK
                        return StockResult(
                            status=STATUS_IN_STOCK,
                            raw_text=avail_text,
                            http_status=http_status,
                            page_title=page_title,
                            scraped_fields=scraped_fields,
                        )
                    elif "unavailable" in avail_text or "out of stock" in avail_text:
                        scraped_fields["stock_status"] = STATUS_OUT_OF_STOCK
                        return StockResult(
                            status=STATUS_OUT_OF_STOCK,
                            raw_text=avail_text,
                            http_status=http_status,
                            page_title=page_title,
                            scraped_fields=scraped_fields,
                        )
            except Exception:
                pass

            # 6. Fallback: keyword scan in full page text.
            # IMPORTANT: Check IN-STOCK keywords FIRST — "not available" text can appear
            # elsewhere on the page (reviews, related products, Q&A) causing false negatives.
            in_stock_keywords = ["add to cart", "buy now", "in stock"]
            for kw in in_stock_keywords:
                if kw in page_text_lower:
                    scraped_fields["stock_status"] = STATUS_IN_STOCK
                    return StockResult(
                        status=STATUS_IN_STOCK,
                        raw_text=f"Keyword match: '{kw}'",
                        http_status=http_status,
                        page_title=page_title,
                        scraped_fields=scraped_fields,
                    )

            oos_keywords = [
                "currently unavailable", "temporarily out of stock",
                "out of stock",
            ]
            for kw in oos_keywords:
                if kw in page_text_lower:
                    scraped_fields["stock_status"] = STATUS_OUT_OF_STOCK
                    return StockResult(
                        status=STATUS_OUT_OF_STOCK,
                        raw_text=f"Keyword match: '{kw}'",
                        http_status=http_status,
                        page_title=page_title,
                        scraped_fields=scraped_fields,
                    )

            # Could not determine stock status
            return StockResult(
                status=STATUS_ERROR,
                raw_text=page_text[:200].strip(),
                http_status=http_status,
                page_title=page_title,
                error="Could not determine stock status on Amazon page",
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
            self.logger.info("Amazon scraper cleaned up")
        except Exception as e:
            self.logger.warning(f"Cleanup error: {e}")
        finally:
            self._context = None
            self._browser = None
            self._playwright = None
            self._initialized = False
