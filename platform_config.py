"""
Platform Configuration — Config-driven column mapping, field comparison mapping,
and location settings per platform.

To add a new platform:
1. Add an entry to PLATFORM_CONFIG below
2. Create a new scraper file in scrapers/ implementing BaseScraper
3. That's it — the engine and UI will pick it up automatically.

IMPORTANT: Replace placeholder column names with your actual MySQL column names.
"""

from utils import normalize_price as _normalize_price

# Canonical status values used internally
STATUS_IN_STOCK = "In Stock"
STATUS_OUT_OF_STOCK = "Out of Stock"
STATUS_NOT_AVAILABLE = "Not Available"
STATUS_ERROR = "Error"

# Maps various raw text values to canonical statuses
# Add platform-specific synonyms as needed
STATUS_SYNONYMS = {
    # In Stock variants
    "in stock": STATUS_IN_STOCK,
    "instock": STATUS_IN_STOCK,
    "in-stock": STATUS_IN_STOCK,
    "is": STATUS_IN_STOCK,
    "in_stock": STATUS_IN_STOCK,
    "available": STATUS_IN_STOCK,
    "add to cart": STATUS_IN_STOCK,
    "add": STATUS_IN_STOCK,
    "buy now": STATUS_IN_STOCK,
    # Out of Stock variants
    "out of stock": STATUS_OUT_OF_STOCK,
    "oos": STATUS_OUT_OF_STOCK,
    "out_of_stock": STATUS_OUT_OF_STOCK,
    "outofstock": STATUS_OUT_OF_STOCK,
    "out-of-stock": STATUS_OUT_OF_STOCK,
    "unavailable": STATUS_OUT_OF_STOCK,
    "currently unavailable": STATUS_OUT_OF_STOCK,
    "sold out": STATUS_OUT_OF_STOCK,
    "notify me": STATUS_OUT_OF_STOCK,
    "not available": STATUS_OUT_OF_STOCK,
    "not_available": STATUS_OUT_OF_STOCK,
}


PLATFORM_CONFIG = {
    "blinkit": {
        "table_name": "blinkit",                # MySQL table name
        "display_name": "Blinkit",
        "columns": {
            "sku_id": "web_pid",                # PLACEHOLDER — replace with real column
            "product_url": "pdp_page_url",      # PLACEHOLDER
            "location_id": "location_id",       # PLACEHOLDER
            "osa_remark": "osa_remark",          # PLACEHOLDER
            "product_name": "product_name",      # PLACEHOLDER (optional)
        },
        # Maps input column names → scraper field keys for multi-field comparison.
        # Only columns present in BOTH the input data AND this mapping get compared.
        "field_mapping": {
            "osa_remark": "stock_status",
            "mrp": "price",
            "product_name": "product_name",
        },
        "scraper_module": "scrapers.blinkit",
        "scraper_class": "BlinkitScraper",
        "location_type": "lat_long",            # "pincode", "lat_long", or "cookie"
        "location_mapping": {
            # location_id → platform-specific location data
            # For Blinkit, we need lat/long for the nearest dark store
            "Delhi": {"lat": 28.6139, "long": 77.2090},
            "Mumbai": {"lat": 19.0760, "long": 72.8777},
            "Bangalore": {"lat": 12.9716, "long": 77.5946},
            "Hyderabad": {"lat": 17.3850, "long": 78.4867},
        },
        "base_url": "https://blinkit.com",
    },

    "flipkart": {
        "table_name": "flipkart",
        "display_name": "Flipkart",
        "columns": {
            "sku_id": "fsn",                    # PLACEHOLDER
            "product_url": "pdp_page_url",
            "location_id": "location_id",
            "osa_remark": "osa_remark",
        },
        "field_mapping": {
            "osa_remark": "stock_status",
            "mrp": "price",
            "product_name": "product_name",
        },
        "scraper_module": "scrapers.flipkart",
        "scraper_class": "FlipkartScraper",
        "location_type": "pincode",
        "location_mapping": {
            "Delhi": "110001",
            "Mumbai": "400001",
            "Bangalore": "560001",
            "Hyderabad": "500001",
        },
        "base_url": "https://www.flipkart.com",
    },

    "onemg": {
        "table_name": "img",                    # Your DB table is called "img"
        "display_name": "1mg (Tata)",
        "columns": {
            "sku_id": "sku_id",
            "product_url": "pdp_page_url",
            "location_id": "location_id",
            "osa_remark": "osa_remark",
            "product_name": "product_name",      # PLACEHOLDER
        },
        "field_mapping": {
            "osa_remark": "stock_status",
            "mrp": "price",
            "product_name": "product_name",
        },
        "scraper_module": "scrapers.onemg",
        "scraper_class": "OneMgScraper",
        "location_type": "pincode",
        "location_mapping": {
            "Delhi": "110001",
            "Mumbai": "400001",
            "Bangalore": "560001",
        },
        "base_url": "https://www.1mg.com",
    },

    "amazon": {
        "table_name": "amazon",
        "display_name": "Amazon",
        "columns": {
            "sku_id": "web_pid",
            "product_url": "pdp_page_url",
            "location_id": "location_id",
            "osa_remark": "osa_remark",
            "product_name": "pdp_title_value",
        },
        "field_mapping": {
            "osa_remark": "stock_status",
            "price_sp": "price",
            "pdp_title_value": "product_name",
        },
        "scraper_module": "scrapers.amazon",
        "scraper_class": "AmazonScraper",
        "location_type": "pincode",
        "location_mapping": {
            "Delhi": "110001",
            "Mumbai": "400001",
            "Bangalore": "560001",
            "Hyderabad": "500001",
            "Chennai": "600001",
            "Kolkata": "700001",
        },
        "base_url": "https://www.amazon.in",
    },

    "amazon_now": {
        # Amazon Now uses the SAME amazon.in product URLs as regular Amazon,
        # but the scraper reads the quick-commerce / instant-delivery section
        # of the page (NOT the standard Add-to-Cart / #availability block).
        "table_name": "amazon_now",
        "display_name": "Amazon Now",
        "columns": {
            "sku_id": "web_pid",
            "product_url": "pdp_page_url",     # Same URL column as regular Amazon
            "location_id": "location_id",
            "osa_remark": "osa_remark",
            "product_name": "pdp_title_value",
        },
        "field_mapping": {
            "osa_remark": "stock_status",
            "price_sp": "price",
            "pdp_title_value": "product_name",
        },
        "scraper_module": "scrapers.amazon_now",
        "scraper_class": "AmazonNowScraper",
        "location_type": "pincode",
        "location_mapping": {
            "Delhi": "110001",
            "Mumbai": "400001",
            "Bangalore": "560001",
            "Hyderabad": "500001",
            "Chennai": "600001",
            "Kolkata": "700001",
        },
        "base_url": "https://www.amazon.in",   # Same base URL — different page section
    },

    "zepto": {
        "table_name": "zepto",
        "display_name": "Zepto",
        "columns": {
            "sku_id": "web_pid",
            "product_url": "pdp_page_url",
            "location_id": "location_id",
            "osa_remark": "osa_remark",
            "product_name": "product_name",
        },
        "field_mapping": {
            "osa_remark": "stock_status",
            "mrp": "price",
            "product_name": "product_name",
        },
        "scraper_module": "scrapers.zepto",
        "scraper_class": "ZeptoScraper",
        "location_type": "pincode",
        "location_mapping": {
            "Delhi": "110001",
            "Mumbai": "400001",
            "Bangalore": "560001",
            "Hyderabad": "500001",
            "Chennai": "600001",
            "Kolkata": "700001",
        },
        "base_url": "https://www.zeptonow.com",
    },

    "instamart": {
        "table_name": "instamart",
        "display_name": "Instamart",
        "columns": {
            "sku_id": "web_pid",
            "product_url": "pdp_page_url",
            "location_id": "location_id",
            "osa_remark": "osa_remark",
            "product_name": "product_name",
        },
        "field_mapping": {
            "osa_remark": "stock_status",
            "mrp": "price",
            "product_name": "product_name",
        },
        "scraper_module": "scrapers.instamart",
        "scraper_class": "InstamartScraper",
        "location_type": "pincode",
        "location_mapping": {
            "Delhi": "110001",
            "Mumbai": "400001",
            "Bangalore": "560001",
            "Hyderabad": "500001",
            "Chennai": "600001",
            "Kolkata": "700001",
        },
        "base_url": "https://www.swiggy.com/instamart",
    },

    "pharmeasy": {
        "table_name": "pharmeasy",
        "display_name": "PharmEasy",
        "columns": {
            "sku_id": "web_pid",
            "product_url": "pdp_page_url",
            "location_id": "location_id",
            "osa_remark": "osa_remark",
            "product_name": "product_name",
        },
        "field_mapping": {
            "osa_remark": "stock_status",
            "mrp": "price",
            "product_name": "product_name",
        },
        "scraper_module": "scrapers.pharmeasy",
        "scraper_class": "PharmeasyScraper",
        "location_type": "pincode",
        "location_mapping": {
            "Delhi": "110001",
            "Mumbai": "400001",
            "Bangalore": "560001",
            "Hyderabad": "500001",
            "Chennai": "600001",
            "Kolkata": "700001",
        },
        "base_url": "https://pharmeasy.in",
    },

    "apollo247": {
        "table_name": "apollo247",
        "display_name": "Apollo 247",
        "columns": {
            "sku_id": "web_pid",
            "product_url": "pdp_page_url",
            "location_id": "location_id",
            "osa_remark": "osa_remark",
            "product_name": "product_name",
        },
        "field_mapping": {
            "osa_remark": "stock_status",
            "mrp": "price",
            "product_name": "product_name",
        },
        "scraper_module": "scrapers.apollo247",
        "scraper_class": "Apollo247Scraper",
        "location_type": "pincode",
        "location_mapping": {
            "Delhi": "110001",
            "Mumbai": "400001",
            "Bangalore": "560001",
            "Hyderabad": "500001",
            "Chennai": "600001",
            "Kolkata": "700001",
        },
        "base_url": "https://www.apollo247.com",
    },

    "bigbasket": {
        "table_name": "bigbasket",
        "display_name": "BigBasket",
        "columns": {
            "sku_id": "web_pid",
            "product_url": "pdp_page_url",
            "location_id": "location_id",
            "osa_remark": "osa_remark",
            "product_name": "product_name",
        },
        "field_mapping": {
            "osa_remark": "stock_status",
            "mrp": "price",
            "product_name": "product_name",
        },
        "scraper_module": "scrapers.bigbasket",
        "scraper_class": "BigbasketScraper",
        "location_type": "pincode",
        "location_mapping": {
            "Delhi": "110001",
            "Mumbai": "400001",
            "Bangalore": "560001",
            "Hyderabad": "500001",
            "Chennai": "600001",
            "Kolkata": "700001",
        },
        "base_url": "https://www.bigbasket.com",
    },

    "flipkart_minutes": {
        "table_name": "flipkart_minutes",
        "display_name": "Flipkart Minutes",
        "columns": {
            "sku_id": "web_pid",
            "product_url": "pdp_page_url",
            "location_id": "location_id",
            "osa_remark": "osa_remark",
            "product_name": "product_name",
        },
        "field_mapping": {
            "osa_remark": "stock_status",
            "mrp": "price",
            "product_name": "product_name",
        },
        "scraper_module": "scrapers.flipkart_minutes",
        "scraper_class": "FlipkartMinutesScraper",
        "location_type": "pincode",
        "location_mapping": {
            "Delhi": "110001",
            "Mumbai": "400001",
            "Bangalore": "560001",
            "Hyderabad": "500001",
            "Chennai": "600001",
            "Kolkata": "700001",
        },
        "base_url": "https://www.flipkart.com",
    },

    "firstcry": {
        "table_name": "firstcry",
        "display_name": "First Cry",
        "columns": {
            "sku_id": "web_pid",
            "product_url": "pdp_page_url",
            "location_id": "location_id",
            "osa_remark": "osa_remark",
            "product_name": "product_name",
        },
        "field_mapping": {
            "osa_remark": "stock_status",
            "mrp": "price",
            "product_name": "product_name",
        },
        "scraper_module": "scrapers.firstcry",
        "scraper_class": "FirstcryScraper",
        "location_type": "pincode",
        "location_mapping": {
            "Delhi": "110001",
            "Mumbai": "400001",
            "Bangalore": "560001",
            "Hyderabad": "500001",
            "Chennai": "600001",
            "Kolkata": "700001",
        },
        "base_url": "https://www.firstcry.com",
    },

    "netmeds": {
        "table_name": "netmeds",
        "display_name": "Netmeds",
        "columns": {
            "sku_id": "web_pid",
            "product_url": "pdp_page_url",
            "location_id": "location_id",
            "osa_remark": "osa_remark",
            "product_name": "product_name",
        },
        "field_mapping": {
            "osa_remark": "stock_status",
            "mrp": "price",
            "product_name": "product_name",
        },
        "scraper_module": "scrapers.netmeds",
        "scraper_class": "NetmedsScraper",
        "location_type": "pincode",
        "location_mapping": {
            "Delhi": "110001",
            "Mumbai": "400001",
            "Bangalore": "560001",
            "Hyderabad": "500001",
            "Chennai": "600001",
            "Kolkata": "700001",
        },
        "base_url": "https://www.netmeds.com",
    },
}



def get_platform_names() -> list[str]:
    """Return list of all configured platform keys."""
    return list(PLATFORM_CONFIG.keys())


def get_platform_config(platform: str) -> dict:
    """Get the full config dict for a platform. Raises KeyError if not found."""
    if platform not in PLATFORM_CONFIG:
        raise KeyError(f"Platform '{platform}' not found in config. "
                       f"Available: {get_platform_names()}")
    return PLATFORM_CONFIG[platform]


def get_column_mapping(platform: str) -> dict[str, str]:
    """Get the column name mapping for a platform."""
    return get_platform_config(platform)["columns"]


def get_field_mapping(platform: str) -> dict[str, str]:
    """
    Get the field comparison mapping for a platform.

    Returns a dict mapping input column names to scraper field keys.
    Example: {"osa_remark": "stock_status", "mrp": "price", "product_name": "product_name"}

    Only columns that exist in BOTH the input data AND this mapping will be compared.
    """
    config = get_platform_config(platform)
    return config.get("field_mapping", {"osa_remark": "stock_status"})


def get_location_value(platform: str, location_id: str):
    """
    Get the platform-specific location value for a location_id.
    Returns the mapped value (pincode string, lat/long dict, etc.)
    or None if no mapping exists.
    """
    config = get_platform_config(platform)
    return config.get("location_mapping", {}).get(location_id)


def normalize_osa_status(raw_text: str) -> str:
    """
    Normalize a raw OSA status string to a canonical value.
    Falls back to the original text (title-cased) if no synonym match.
    """
    if not raw_text:
        return STATUS_NOT_AVAILABLE
    cleaned = raw_text.strip().lower()
    return STATUS_SYNONYMS.get(cleaned, raw_text.strip().title())


def normalize_price(raw: str) -> float | None:
    """
    Normalize a price string to a float for comparison.
    Delegates to utils.normalize_price.
    """
    return _normalize_price(raw)
