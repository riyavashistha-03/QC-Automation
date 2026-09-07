"""
Database Connector — MySQL connection via SQLAlchemy + query builder.

Reads credentials from Streamlit secrets or environment variables.
"""

import os
import logging

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from dotenv import load_dotenv

from platform_config import PLATFORM_CONFIG, get_column_mapping

load_dotenv()
logger = logging.getLogger("osa_qc.db")


def _get_credentials() -> dict:
    """Get MySQL credentials from Streamlit secrets or environment variables."""
    try:
        # Try Streamlit secrets first
        return {
            "host": st.secrets["mysql"]["host"],
            "port": int(st.secrets["mysql"]["port"]),
            "user": st.secrets["mysql"]["user"],
            "password": st.secrets["mysql"]["password"],
            "database": st.secrets["mysql"]["database"],
        }
    except (KeyError, FileNotFoundError, AttributeError):
        # Fall back to environment variables
        return {
            "host": os.getenv("DB_HOST", "localhost"),
            "port": int(os.getenv("DB_PORT", "3306")),
            "user": os.getenv("DB_USER", "root"),
            "password": os.getenv("DB_PASSWORD", ""),
            "database": os.getenv("DB_NAME", ""),
        }


@st.cache_resource
def get_engine() -> Engine:
    """
    Create and cache a SQLAlchemy engine for MySQL.
    Uses PyMySQL as the driver.
    """
    creds = _get_credentials()
    if not creds["database"]:
        raise ValueError(
            "Database name not configured. Set it in .streamlit/secrets.toml "
            "or the DB_NAME environment variable."
        )

    url = (
        f"mysql+pymysql://{creds['user']}:{creds['password']}"
        f"@{creds['host']}:{creds['port']}/{creds['database']}"
    )
    engine = create_engine(url, pool_pre_ping=True, pool_recycle=3600)
    logger.info(f"Created MySQL engine for {creds['host']}:{creds['port']}/{creds['database']}")
    return engine


def test_connection() -> tuple[bool, str]:
    """
    Test the database connection.
    Returns (success: bool, message: str).
    """
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "Connected successfully"
    except Exception as e:
        return False, f"Connection failed: {str(e)}"


def get_all_tables() -> list[str]:
    """Get all table names from the database."""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            result = conn.execute(text("SHOW TABLES"))
            return [row[0] for row in result]
    except Exception as e:
        logger.error(f"Failed to fetch tables: {e}")
        return []


def get_platform_tables() -> list[str]:
    """
    Get platform keys that have corresponding tables in the database.
    Cross-references PLATFORM_CONFIG with actual DB tables.
    """
    db_tables = get_all_tables()
    if not db_tables:
        return []

    available = []
    for platform_key, config in PLATFORM_CONFIG.items():
        if config["table_name"] in db_tables:
            available.append(platform_key)

    return available


def get_locations_for_platform(platform: str) -> list[str]:
    """
    Get distinct location IDs from a platform's table.
    """
    config = PLATFORM_CONFIG.get(platform)
    if not config:
        return []

    table = config["table_name"]
    col_map = config["columns"]
    loc_col = col_map.get("location_id", "location_id")

    try:
        engine = get_engine()
        query = text(f"SELECT DISTINCT `{loc_col}` FROM `{table}` ORDER BY `{loc_col}`")
        with engine.connect() as conn:
            result = conn.execute(query)
            return [str(row[0]) for row in result if row[0] is not None]
    except Exception as e:
        logger.error(f"Failed to fetch locations for {platform}: {e}")
        return []


def fetch_data(
    platform: str,
    locations: list[str] | None = None,
) -> pd.DataFrame:
    """
    Fetch rows from a platform's table with optional location filter.

    Uses the column mapping from platform_config to select the right columns.
    Returns a DataFrame with standardized column names (sku_id, product_url, etc.)
    plus all other original columns.
    """
    config = PLATFORM_CONFIG.get(platform)
    if not config:
        raise ValueError(f"Platform '{platform}' not in config")

    table = config["table_name"]
    col_map = config["columns"]
    loc_col = col_map.get("location_id", "location_id")

    try:
        engine = get_engine()

        # Build query — select all columns from the table
        if locations and "All" not in locations:
            # Parameterized location filter
            placeholders = ", ".join([f":loc_{i}" for i in range(len(locations))])
            query_str = f"SELECT * FROM `{table}` WHERE `{loc_col}` IN ({placeholders})"
            params = {f"loc_{i}": loc for i, loc in enumerate(locations)}
            query = text(query_str)
        else:
            query = text(f"SELECT * FROM `{table}`")
            params = {}

        with engine.connect() as conn:
            df = pd.read_sql(query, conn, params=params)

        logger.info(f"Fetched {len(df)} rows from {table}" +
                    (f" (locations: {locations})" if locations else ""))

        # Add standardized column references as metadata
        # (the original columns are preserved; we just track the mapping)
        df.attrs["column_mapping"] = col_map
        df.attrs["platform"] = platform

        return df

    except Exception as e:
        logger.error(f"Failed to fetch data for {platform}: {e}")
        raise
