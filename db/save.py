"""
save.py — Persist PFPB results to Neon (DATABASE_URL_PFPB).

Tables:
  pfpb_elenco     — official free list (41 items), keyed by pfpb_id
  pfpb_produtos   — catalog products correlated to a pfpb_id (fully reloaded each run)
  pfpb_farmacias  — accredited pharmacies, CNPJ-enriched (filled by pharmacies.py)

View:
  pfpb_onde_retirar — every free product × accredited pharmacy of the same chain
                      (catalog store_name ↔ pharmacy rede), for "where to grab it"
"""

import os

import pandas as pd
import psycopg2
import psycopg2.extras

DDL = """
CREATE TABLE IF NOT EXISTS pfpb_elenco (
    pfpb_id          TEXT PRIMARY KEY,
    indicacao        TEXT NOT NULL,
    principio_ativo  TEXT NOT NULL,
    gratuito         BOOLEAN NOT NULL DEFAULT TRUE,
    elenco_versao    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pfpb_produtos (
    id            BIGSERIAL PRIMARY KEY,
    pfpb_id       TEXT NOT NULL REFERENCES pfpb_elenco(pfpb_id),
    match_via     TEXT NOT NULL,
    barcode       TEXT,
    product_name  TEXT NOT NULL,
    store_name    TEXT,
    brand         TEXT,
    is_generic    BOOLEAN,
    is_available  BOOLEAN,
    price         NUMERIC(10, 2),
    product_url   TEXT,
    scraped_at    TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pfpb_prod_ean  ON pfpb_produtos(barcode);
CREATE INDEX IF NOT EXISTS idx_pfpb_prod_item ON pfpb_produtos(pfpb_id);

CREATE TABLE IF NOT EXISTS pfpb_farmacias (
    cnpj          TEXT PRIMARY KEY,
    razao_social  TEXT,
    nome_fantasia TEXT,
    rede          TEXT,          -- normalized chain, same vocabulary as store_name
    endereco      TEXT,
    bairro        TEXT,
    municipio     TEXT,
    uf            TEXT,
    cod_municipio_ibge TEXT,
    regiao        TEXT,
    cep           TEXT,
    telefone      TEXT,
    latitude      DOUBLE PRECISION,
    longitude     DOUBLE PRECISION,
    geo_fonte     TEXT,          -- google (address) or cep (approximate)
    geo_precisao  TEXT,          -- Google location_type, e.g. ROOFTOP
    geo_parcial   BOOLEAN,       -- Google matched only part of the address
    geo_endereco_google TEXT,    -- address Google resolved, to compare with endereco
    data_credenciamento DATE,
    situacao_cadastral TEXT,     -- Receita Federal status (ATIVA, BAIXADA, ...)
    fonte         TEXT,          -- list file the row came from
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pfpb_farm_mun  ON pfpb_farmacias(uf, municipio);
CREATE INDEX IF NOT EXISTS idx_pfpb_farm_rede ON pfpb_farmacias(rede);

CREATE OR REPLACE VIEW pfpb_onde_retirar AS
SELECT e.pfpb_id, e.indicacao, e.principio_ativo,
       p.barcode, p.product_name, p.store_name, p.price,
       f.cnpj, f.nome_fantasia, f.endereco, f.bairro, f.municipio, f.uf, f.cep, f.telefone
FROM pfpb_produtos p
JOIN pfpb_elenco e    ON e.pfpb_id = p.pfpb_id
JOIN pfpb_farmacias f ON f.rede = p.store_name
                      OR (f.rede = 'raiadrogasil' AND p.store_name IN ('drogasil', 'drogaraia'));
"""


MIN_KEEP_RATIO = 0.9  # a full list shrinking more than 10% in one run is treated as broken


def connect():
    url = os.environ.get("DATABASE_URL_PFPB")
    if not url:
        raise SystemExit("DATABASE_URL_PFPB not set (put it in .env)")
    return psycopg2.connect(url)


def _rows(df: pd.DataFrame, cols):
    return [tuple(None if pd.isna(v) else v for v in r) for r in df[cols].itertuples(index=False)]


def save_all(elenco: pd.DataFrame, corr: pd.DataFrame) -> None:
    cols_e = ["pfpb_id", "indicacao", "principio_ativo", "gratuito", "elenco_versao"]
    cols_p = ["pfpb_id", "match_via", "barcode", "product_name", "store_name", "brand",
              "is_generic", "is_available", "price", "product_url", "scraped_at"]
    with connect() as conn, conn.cursor() as cur:
        cur.execute(DDL)
        psycopg2.extras.execute_values(cur, f"""
            INSERT INTO pfpb_elenco ({', '.join(cols_e)}) VALUES %s
            ON CONFLICT (pfpb_id) DO UPDATE SET
              indicacao = EXCLUDED.indicacao, principio_ativo = EXCLUDED.principio_ativo,
              gratuito = EXCLUDED.gratuito, elenco_versao = EXCLUDED.elenco_versao
        """, _rows(elenco, cols_e))
        cur.execute("TRUNCATE pfpb_produtos")
        psycopg2.extras.execute_values(cur,
            f"INSERT INTO pfpb_produtos ({', '.join(cols_p)}) VALUES %s",
            _rows(corr, cols_p), page_size=5000)
    print(f"Neon: {len(elenco)} elenco items, {len(corr):,} products saved")


def save_farmacias(df: pd.DataFrame, full_list: bool = False) -> None:
    """Upsert pharmacies. full_list=True also removes pharmacies missing from df."""
    cols = [c for c in ["cnpj", "razao_social", "nome_fantasia", "rede", "endereco", "bairro",
                        "municipio", "uf", "cod_municipio_ibge", "regiao", "cep", "telefone",
                        "latitude", "longitude", "geo_fonte", "geo_precisao", "geo_parcial",
                        "geo_endereco_google", "data_credenciamento", "situacao_cadastral",
                        "fonte"] if c in df.columns]
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "cnpj")
    df = df.drop_duplicates("cnpj")
    with connect() as conn, conn.cursor() as cur:
        cur.execute(DDL)
        removed = 0
        if full_list:
            cur.execute("SELECT COUNT(*) FROM pfpb_farmacias")
            previous = cur.fetchone()[0]
            if len(df) < previous * MIN_KEEP_RATIO:
                raise SystemExit(f"list has {len(df):,} pharmacies but Neon has {previous:,}; nothing changed")
            cur.execute("DELETE FROM pfpb_farmacias WHERE cnpj <> ALL(%s)", (df["cnpj"].tolist(),))
            removed = cur.rowcount
        psycopg2.extras.execute_values(cur, f"""
            INSERT INTO pfpb_farmacias ({', '.join(cols)}) VALUES %s
            ON CONFLICT (cnpj) DO UPDATE SET {updates}, updated_at = NOW()
        """, _rows(df, cols), page_size=5000)
    print(f"Neon: {len(df):,} pharmacies saved, {removed:,} removed")
