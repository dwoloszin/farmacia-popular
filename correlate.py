"""
correlate.py — Build the PFPB (Farmácia Popular) ↔ catalog correlation table.

Reads products from every store DB in .env (DATABASE_URL_<STORE>, the
markets_db_farm Neon databases) — or from a CSV export — tags every product
on the free PFPB list, and writes to output/:
  pfpb_elenco.csv      — the official list (41 items)
  pfpb_correlacao.csv  — one row per matched store offer   (→ table pfpb_produtos)
  pfpb_por_ean.csv     — one row per EAN: stores, min/median price
  pfpb_resumo.csv      — per list item: products, EANs, stores, price range

Nothing is written to Neon unless --save-db is given.

Usage:
  python correlate.py                    # all store DBs -> CSVs (review)
  python correlate.py --save-db          # ... and save into DATABASE_URL_PFPB
  python correlate.py --csv export.csv   # offline, from markets_db_farm export-all-together
"""

import argparse
import os
import sys

import pandas as pd

from fp.elenco import ELENCO, ELENCO_VERSION
from fp.matcher import match_product

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

COLS = ["barcode", "product_name", "store_name", "brand", "is_generic", "is_available",
        "price", "product_url", "scraped_at"]


def load_env(path: str = ".env") -> None:
    """Minimal .env loader — sets os.environ for KEY=VALUE lines (no overwrite)."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def elenco_df() -> pd.DataFrame:
    return pd.DataFrame([{
        "pfpb_id": it.id, "indicacao": it.indicacao, "principio_ativo": it.principio_ativo,
        "gratuito": True, "elenco_versao": ELENCO_VERSION,
    } for it in ELENCO])


def read_products(args) -> pd.DataFrame:
    if args.csv:
        df = pd.read_csv(args.csv, dtype={"barcode": str}, encoding="utf-8-sig",
                         usecols=lambda c: c in COLS, low_memory=False)
    else:
        from fp.sources import read_all_stores
        df = read_all_stores()
    for c in COLS:
        if c not in df.columns:
            df[c] = None
    return df[COLS]


def correlate(df: pd.DataFrame) -> pd.DataFrame:
    # Match each distinct name once — the catalog repeats names across stores.
    names = df["product_name"].dropna().unique()
    hits = {}
    for n in names:
        m = match_product(n)
        if m:
            hits[n] = (m[0].id, m[1])
    print(f"{len(names):,} distinct names -> {len(hits):,} on the PFPB list")

    out = df[df["product_name"].isin(hits.keys())].copy()
    out["pfpb_id"] = out["product_name"].map(lambda n: hits[n][0])
    out["match_via"] = out["product_name"].map(lambda n: hits[n][1])
    out = out.merge(elenco_df()[["pfpb_id", "indicacao", "principio_ativo"]], on="pfpb_id")
    return out[["pfpb_id", "indicacao", "principio_ativo", "match_via", *COLS]] \
        .sort_values(["pfpb_id", "product_name", "store_name"])


def by_ean(corr: pd.DataFrame) -> pd.DataFrame:
    g = corr.dropna(subset=["barcode"]).groupby(["pfpb_id", "principio_ativo", "barcode"])
    return g.agg(
        product_name=("product_name", "first"),
        match_via=("match_via", "first"),
        lojas=("store_name", "nunique"),
        lista_lojas=("store_name", lambda s: ", ".join(sorted(set(s)))),
        preco_min=("price", "min"),
        preco_med=("price", "median"),
    ).reset_index().sort_values(["pfpb_id", "lojas"], ascending=[True, False])


def summarize(corr: pd.DataFrame) -> pd.DataFrame:
    g = corr.groupby("pfpb_id").agg(
        produtos=("product_name", "nunique"),
        eans=("barcode", "nunique"),
        lojas=("store_name", "nunique"),
        preco_min=("price", "min"),
        preco_med=("price", "median"),
        preco_max=("price", "max"),
    ).reset_index()
    return elenco_df().merge(g, on="pfpb_id", how="left").fillna({"produtos": 0, "eans": 0, "lojas": 0})


def main():
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", help="read a markets_db_farm combined export instead of the store DBs")
    ap.add_argument("--out", default="output")
    ap.add_argument("--save-db", action="store_true", help="save results into DATABASE_URL_PFPB")
    args = ap.parse_args()

    df = read_products(args)
    print(f"{len(df):,} catalog rows loaded")
    corr = correlate(df)
    summ = summarize(corr)

    os.makedirs(args.out, exist_ok=True)
    elenco_df().to_csv(f"{args.out}/pfpb_elenco.csv", index=False, encoding="utf-8-sig")
    corr.to_csv(f"{args.out}/pfpb_correlacao.csv", index=False, encoding="utf-8-sig")
    by_ean(corr).to_csv(f"{args.out}/pfpb_por_ean.csv", index=False, encoding="utf-8-sig")
    summ.to_csv(f"{args.out}/pfpb_resumo.csv", index=False, encoding="utf-8-sig")
    print(f"{len(corr):,} catalog rows correlated -> {args.out}/")
    print(summ[["pfpb_id", "principio_ativo", "produtos", "eans", "lojas", "preco_min", "preco_max"]]
          .to_string(index=False))

    if args.save_db:
        from db.save import save_all
        save_all(elenco_df(), corr)


if __name__ == "__main__":
    main()
