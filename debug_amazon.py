"""
Quick diagnostic script to see what the Amazon scraper actually sees.
Run: python debug_amazon.py
"""
import asyncio
from playwright.async_api import async_playwright
from utils import get_rotating_user_agent, get_stealth_browser_args, get_stealth_context_options, apply_stealth_scripts


async def debug_amazon():
    test_url = "https://www.amazon.in/dp/B006QQRC80"  # Horlicks product
    pincode = "400001"  # Mumbai

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True,
        args=get_stealth_browser_args(),
    )

    ua = get_rotating_user_agent()
    context = await browser.new_context(**get_stealth_context_options(ua))

    # Don't block images - we want to see the full page
    page = await context.new_page()
    await apply_stealth_scripts(page)

    # Step 1: Set pincode on homepage
    print("=" * 60)
    print(f"Step 1: Setting pincode {pincode} on Amazon homepage...")
    print("=" * 60)
    await page.goto("https://www.amazon.in", wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(3)

    title = await page.title()
    print(f"Homepage title: {title}")

    # Check if CAPTCHA
    body_text = await page.text_content("body") or ""
    if "sorry" in title.lower() or "robot" in title.lower() or "automated access" in body_text.lower():
        print("!!! CAPTCHA / BOT DETECTION on homepage !!!")
        print(f"Page text (first 500 chars): {body_text[:500]}")
        # Save screenshot
        await page.screenshot(path="debug_amazon_captcha.png")
        print("Screenshot saved: debug_amazon_captcha.png")
        await browser.close()
        await pw.stop()
        return

    print(f"Homepage loaded OK. Body text length: {len(body_text)}")

    # Try clicking location
    try:
        loc = page.locator("#nav-global-location-popover-link, #glow-ingress-block").first
        if await loc.is_visible(timeout=5000):
            await loc.click()
            await asyncio.sleep(2)
            print("Clicked location trigger")

            pin_input = page.locator("input#GLUXZipUpdateInput").first
            if await pin_input.is_visible(timeout=5000):
                await pin_input.clear()
                await pin_input.fill(pincode)
                await asyncio.sleep(1)

                apply_btn = page.locator("input#GLUXZipUpdate, span#GLUXZipUpdate input").first
                if await apply_btn.is_visible(timeout=3000):
                    await apply_btn.click()
                    await asyncio.sleep(3)
                    print(f"Pincode {pincode} applied!")
                else:
                    await pin_input.press("Enter")
                    await asyncio.sleep(3)
                    print(f"Pincode {pincode} applied (via Enter)")

                # Close any continuation dialog
                try:
                    done = page.locator("button[name='glowDoneButton'], button:has-text('Done'), button:has-text('Continue')").first
                    if await done.is_visible(timeout=3000):
                        await done.click()
                        await asyncio.sleep(1)
                        print("Closed done/continue dialog")
                except Exception:
                    pass
            else:
                print("Pincode input NOT visible")
        else:
            print("Location trigger NOT visible")
    except Exception as e:
        print(f"Error setting pincode: {e}")

    await page.close()

    # Step 2: Visit product page
    print()
    print("=" * 60)
    print(f"Step 2: Visiting product page: {test_url}")
    print("=" * 60)
    page2 = await context.new_page()
    await apply_stealth_scripts(page2)

    response = await page2.goto(test_url, wait_until="domcontentloaded", timeout=30000)
    print(f"HTTP status: {response.status if response else 'None'}")

    # Wait for page to fully load
    await asyncio.sleep(5)

    title2 = await page2.title()
    print(f"Page title: {title2}")

    body2 = await page2.text_content("body") or ""
    print(f"Body text length: {len(body2)}")

    # Check for CAPTCHA
    if "sorry" in title2.lower() or "robot" in title2.lower() or "automated access" in body2.lower():
        print("!!! CAPTCHA / BOT DETECTION on product page !!!")
        print(f"First 500 chars: {body2[:500]}")
        await page2.screenshot(path="debug_amazon_product_captcha.png")
        print("Screenshot saved: debug_amazon_product_captcha.png")
    else:
        print("No CAPTCHA detected")

    # Check key elements
    print()
    print("--- Element Visibility Checks ---")

    checks = [
        ("#productTitle", "Product Title"),
        ("#add-to-cart-button", "Add to Cart Button"),
        ("input#add-to-cart-button", "Add to Cart (input)"),
        ("#buy-now-button", "Buy Now Button"),
        ("#availability", "Availability Div"),
        ("#availability span", "Availability Span"),
        ("#outOfStock", "Out of Stock Div"),
        ("#price", "Price Div"),
        ("span.a-price span.a-offscreen", "Price Offscreen"),
    ]

    for selector, label in checks:
        el = page2.locator(selector).first
        try:
            visible = await el.is_visible(timeout=2000)
            if visible:
                text = await el.text_content() or ""
                print(f"  ✅ {label} ({selector}): VISIBLE — text='{text[:100].strip()}'")
            else:
                print(f"  ❌ {label} ({selector}): NOT visible")
        except Exception as e:
            print(f"  ❌ {label} ({selector}): Error — {e}")

    # Check OOS keywords in page text
    print()
    print("--- Keyword Scan ---")
    body_lower = body2.lower()
    keywords = [
        "currently unavailable", "temporarily out of stock",
        "out of stock", "not available", "unavailable",
        "add to cart", "buy now", "in stock",
    ]
    for kw in keywords:
        if kw in body_lower:
            # Find context around the keyword
            idx = body_lower.find(kw)
            context_text = body2[max(0, idx - 50):idx + len(kw) + 50].replace("\n", " ").strip()
            print(f"  FOUND '{kw}' — context: '...{context_text}...'")
        else:
            print(f"  NOT found: '{kw}'")

    # Save screenshot
    await page2.screenshot(path="debug_amazon_product.png", full_page=False)
    print()
    print("Screenshot saved: debug_amazon_product.png")

    # Save page HTML for inspection
    html = await page2.content()
    with open("debug_amazon_page.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("HTML saved: debug_amazon_page.html")

    await page2.close()
    await browser.close()
    await pw.stop()
    print()
    print("Done!")


if __name__ == "__main__":
    asyncio.run(debug_amazon())
