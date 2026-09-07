# OSA Quality Check Automation Web App

**OSA QC Automation** is a web application built with Python and Streamlit designed to automate On-Shelf Availability (OSA) verification for e-commerce and quick-commerce platforms. 

The application compares claimed stock statuses stored in a MySQL database (or an uploaded Excel file) against **live stock status scraped directly from platform product pages** in real-time. It highlights stock discrepancies (e.g., claimed Out-of-Stock but actually In-Stock) and outputs a styled, downloadable Excel mismatch report.

---

## Key Features

- **Dual Input Modes**: Connect directly to MySQL or upload Excel files for ad-hoc verification.
- **Config-Driven Column Mapping**: Easily adapt to different platform schema structures without changing core engine code.
- **Live Streaming Inspection Feed**: Displays real-time row-by-row status updates, live summary counters, and a progress bar during QC runs.
- **Robust Anti-Bot Evasions**: Employs Playwright with `playwright-stealth` evasions and rate-limit retry backoffs to bypass Cloudflare protection.
- **Parallel Workers / Concurrency**: Configurable parallel worker tabs (1 to 5) for fast batch processing.
- **Formatted Excel Mismatch Reports**: Automatically generates openpyxl-formatted reports with color-coded mismatch status, auto-fitted column widths, and individual platform sheets + summary tab.

---

## Tech Stack

- **UI & Dashboard**: Streamlit
- **Scraping Engine**: Playwright + `playwright-stealth`
- **Database Connectivity**: SQLAlchemy + PyMySQL
- **Data & Excel Processing**: pandas + openpyxl

---

## Project Structure

```
qc_automation/
├── app.py                  # Streamlit Web UI (inputs, live feed, results, download)
├── db_connector.py         # MySQL connection layer & query builder
├── platform_config.py      # Config-driven column mappings & location settings
├── qc_engine.py             # QC pipeline orchestrator (parallel workers & live callbacks)
├── excel_handler.py         # Upload parser & openpyxl formatted report generator
├── utils.py                # User-agent helpers, stealth configs, & logging
├── scrapers/
│   ├── base.py              # Abstract BaseScraper interface & StockResult dataclass
│   ├── onemg.py             # 1mg (Tata) scraper adapter with Cloudflare evasion
│   ├── blinkit.py           # Blinkit scraper adapter (lat/long location)
│   ├── flipkart.py          # Flipkart scraper adapter (pincode check)
│   └── generic.py           # Fallback keyword-based scraper
├── .streamlit/
│   └── secrets.toml          # Database credentials (gitignored)
├── .env.example              # Environment variables template
├── requirements.txt         # Project Python dependencies
└── README.md                # Project documentation
```

---

## Setup & Installation

### 1. Prerequisites
Ensure you have Python 3.10+ installed.

### 2. Install Dependencies
Clone or navigate to the project directory and install the required packages:

```powershell
pip install -r requirements.txt
```

### 3. Install Playwright Browsers
Install Chromium binaries for Playwright:

```powershell
playwright install chromium
```

### 4. Database Configuration (Optional)
To use the **From Database** input mode:
1. Copy `.env.example` to `.env` or edit `.streamlit/secrets.toml`.
2. Enter your MySQL database credentials:

```toml
[mysql]
host = "localhost"
port = 3306
user = "root"
password = "your_password"
database = "your_database_name"
```

*(Note: If no database is configured, you can still use **Upload Excel** mode.)*

---

## Running the Application

Launch the Streamlit app by executing:

```powershell
streamlit run app.py
```

The web application will open in your default browser at `http://localhost:8501`.

---

## Adding New Platforms

Adding support for a new e-commerce platform requires two steps:

1. **Add a Config Entry** in `platform_config.py`:
   ```python
   "new_platform": {
       "table_name": "new_platform_table",
       "display_name": "New Platform",
       "columns": {
           "sku_id": "product_sku",
           "product_url": "pdp_url",
           "location_id": "location_code",
           "osa_remark": "stock_status",
       },
       "scraper_module": "scrapers.new_platform",
       "scraper_class": "NewPlatformScraper",
       "location_type": "pincode",
   }
   ```

2. **Create a Scraper Adapter** in `scrapers/new_platform.py` extending `BaseScraper`.
