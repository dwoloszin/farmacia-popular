"""
sources.py — Load catalog products from the markets_db_farm store databases.

Every DATABASE_URL_<STORE> in the environment (except DATABASE_URL_PFPB) is one
store's Neon DB with an `offers` table. All stores are read in parallel and
concatenated; store_name is the lowercased <STORE> suffix.
"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import psycopg2

PREFIX = "DATABASE_URL_"
TARGET_ENV = "DATABASE_URL_PFPB"
PRICE_SENTINEL = 9_999_000  # same "price not available" placeholder as markets_db_farm

# Effective price = promo when it is a real, lower price; else regular
# (mirrors effective_price_sql in markets_db_farm/db/db_manager.py).
_SQL = f"""
SELECT ean AS barcode, product_name, brand, is_generic, is_available,
       CASE WHEN promo_price > 0
             AND (regular_price IS NULL OR regular_price >= {PRICE_SENTINEL} OR promo_price < regular_price)
            THEN promo_price ELSE regular_price END AS price,
       product_url, scraped_at
FROM offers
WHERE product_name IS NOT NULL
"""


def store_urls() -> dict:
    return {k[len(PREFIX):].lower(): v for k, v in os.environ.items()
            if k.startswith(PREFIX) and k != TARGET_ENV and v.startswith("postgres")}


def _read(store: str, url: str) -> pd.DataFrame:
    conn = psycopg2.connect(url, connect_timeout=30)
    try:
        with conn.cursor() as cur:
            cur.execute(_SQL)
            cols = [d[0] for d in cur.description]
            df = pd.DataFrame(cur.fetchall(), columns=cols)
    finally:
        conn.close()
    df.insert(2, "store_name", store)
    return df


def read_all_stores(workers: int = 12) -> pd.DataFrame:
    urls = store_urls()
    if not urls:
        raise SystemExit(f"no {PREFIX}<STORE> variables found in .env")
    frames, failed = [], []
    with ThreadPoolExecutor(workers) as pool:
        futs = {pool.submit(_read, s, u): s for s, u in urls.items()}
        for f in as_completed(futs):
            store = futs[f]
            try:
                df = f.result()
                frames.append(df)
                print(f"  {store:<24} {len(df):>8,} offers")
            except Exception as e:
                failed.append(store)
                print(f"  {store:<24} FAILED: {str(e).strip().splitlines()[0][:120]}")
    print(f"{len(frames)}/{len(urls)} stores read" + (f" — failed: {', '.join(sorted(failed))}" if failed else ""))
    df = pd.concat(frames, ignore_index=True)
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    return df
