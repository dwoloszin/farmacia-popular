"""
pharmacies.py — Accredited Farmácia Popular pharmacies with full address.

Input: a pharmacy list file, or a folder of them (.xlsx/.csv with a CNPJ column):
  dashboard exports, or a full national list.

Enrichment (public APIs, cached in Neon so any later run only fetches new keys):
  CNPJ → BrasilAPI /cnpj/v1  : street type, number, complement, CEP, phone,
                                trade name, Receita registration status
  CEP  → AwesomeAPI /json    : street-level latitude / longitude
         (BrasilAPI cep/v2 only returns the city centroid for most CEPs)
  Address → Google Geocoding (GEOCODE_API): building-level point, at most 1,000
         requests per week. The whole list is covered over successive weeks;
         until then a pharmacy keeps its CEP point (see geo_fonte, geo_precisao).

Writes output/pfpb_farmacias.csv.

Usage:
  python pharmacies.py                               # read input/, CSV only
  python pharmacies.py --input lista.csv --full-list --save-db
      # national list: sync Neon, removing pharmacies no longer on the list
  python pharmacies.py --max-lookup-minutes 240      # stop in time; next run continues
  python pharmacies.py --from-db --save-db           # the GitHub Actions run: no files needed
"""

import argparse
import glob
import os
import re
import sys
import time
import unicodedata
from datetime import date, timedelta

import pandas as pd
import psycopg2.extras
import requests

from db.save import connect

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

COLUMNS = ["cnpj", "razao_social", "logradouro_painel", "bairro_painel", "municipio", "uf",
           "cod_municipio_ibge", "regiao", "data_credenciamento", "fonte"]
MIN_PHARMACIES = 15_000  # a "full list" below this is a broken file, not the real list

API = "https://brasilapi.com.br/api"
GEO_API = "https://cep.awesomeapi.com.br/json"
UA = {"User-Agent": "FARMACIA_POPULAR/1.0 (pharmacy address enrichment)"}
DELAY = 0.35  # seconds between API calls — be polite to free public APIs
CACHE_DAYS = 90  # refresh cached addresses after this long

# Only these fields are cached: the CNPJ response also lists partners' names.
CNPJ_KEEP = ["razao_social", "nome_fantasia", "descricao_tipo_de_logradouro", "logradouro", "numero",
             "complemento", "bairro", "municipio", "uf", "cep", "ddd_telefone_1",
             "descricao_situacao_cadastral"]
GEO_KEEP = ["lat", "lng"]

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
GEOCODE_WEEKLY_LIMIT = 1000  # hard cap on Google requests per ISO week, kept across runs
GEOCODE_REFRESH_DAYS = 30    # once all are done, the budget re-checks points older than this
GEOCODE_KEEP = ["lat", "lng", "precisao", "parcial", "endereco_google", "consulta", "status"]
MAX_FAILURES = 10  # an API failing this many times in a row is down or blocking us
MIN_CHAIN_BRANCHES = 5  # a company with fewer branches on the list gets no chain tag

CACHE_DDL = """
CREATE TABLE IF NOT EXISTS pfpb_cache (
    tipo   TEXT NOT NULL,   -- cnpj, cep, geocode, geocode_usage
    chave  TEXT NOT NULL,
    dados  JSONB NOT NULL,
    PRIMARY KEY (tipo, chave)
);
"""

# Catalog store_name (markets_db_farm) ← regex on nome fantasia + razão social.
# Order matters: first hit wins.
CHAINS = [
    ("drogaraia", r"^(droga raia|drogaraia)\b"),
    ("drogasil", r"^drogasil\b"),
    # RD branches with no trade name: brand unknown, linked to both stores.
    ("raiadrogasil", r"\b(raia drogasil|drogasil s\.?a|droga raia)\b"),
    ("paguemenos", r"\bpague menos\b"),
    ("drogariasaopaulo", r"\bdrogaria sao paulo\b"),
    ("drogaleste", r"\bdroga ?leste\b"),
    ("farmaconde", r"\bfarma ?conde\b"),
    ("panvel", r"\bpanvel\b"), ("ultrafarma", r"\bultrafarma\b"), ("farmais", r"\bfarmais\b"),
    ("araujo", r"\bdrogaria araujo\b"), ("venancio", r"\bvenancio\b"),
    ("indiana", r"\bdrogaria indiana\b"), ("saojoao", r"\bsao joao\b"),
    ("santalucia", r"\bsanta lucia\b"), ("pacheco", r"\bdrogarias? pacheco\b"),
    ("nissei", r"\bnissei\b"), ("drogal", r"\bdrogal\b"), ("rosario", r"\bdrogaria rosario\b"),
    ("drogasmil", r"\bdrogas ?mil\b"), ("precopopular", r"\bpreco popular\b"),
    ("catarinense", r"\bcatarinense\b"), ("minasbrasil", r"\bminas brasil\b"),
    ("callfarma", r"\bcall ?farma\b"), ("santoremedio", r"\bsanto remedio\b"),
    ("veracruz", r"\bvera cruz\b"), ("redesuperpopular", r"\bsuper popular\b"),
    ("pensefarma", r"\bpense ?farma\b"), ("promofarma", r"\bpromofarma\b"),
]


def _norm(s) -> str:
    s = unicodedata.normalize("NFKD", "" if s is None or pd.isna(s) else str(s))
    return re.sub(r"\s+", " ", "".join(c for c in s if not unicodedata.combining(c)).lower()).strip()


# ── Pharmacy list ────────────────────────────────────────────────────────────

def read_inputs(source: str) -> pd.DataFrame:
    paths = [source] if os.path.isfile(source) else \
        sorted(glob.glob(os.path.join(source, "*.xlsx")) + glob.glob(os.path.join(source, "*.csv")))
    frames = []
    for path in paths:
        df = pd.read_excel(path, dtype=str) if path.endswith(".xlsx") else \
             pd.read_csv(path, dtype=str, sep=None, engine="python", encoding="utf-8-sig")
        df.columns = [_norm(c) for c in df.columns]
        df = df.rename(columns={"farmacia": "razao_social", "endereco": "logradouro_painel",
                                "bairro": "bairro_painel", "co_ibge": "cod_municipio_ibge",
                                "dt_credenciamento": "data_credenciamento"})
        if "cnpj" not in df.columns:
            print(f"  skip {os.path.basename(path)}: no CNPJ column")
            continue
        df["fonte"] = os.path.basename(path)
        frames.append(df)
        print(f"  {os.path.basename(path)}: {len(df):,} rows")
    if not frames:
        raise SystemExit(f"no .xlsx/.csv files with a CNPJ column in {source}")
    return pd.concat(frames, ignore_index=True).replace({"-": None, "": None})


def read_neon() -> pd.DataFrame:
    """The list already in pfpb_farmacias; its address is only the fallback to Receita's."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT cnpj, razao_social, endereco, bairro, municipio, uf, cod_municipio_ibge,
                   regiao, data_credenciamento, fonte
            FROM pfpb_farmacias
        """)
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=["cnpj", "razao_social", "logradouro_painel", "bairro_painel", "municipio", "uf",
                                     "cod_municipio_ibge", "regiao", "data_credenciamento", "fonte"])
    df["data_credenciamento"] = df["data_credenciamento"].map(lambda d: d.strftime("%d/%m/%Y") if d else None)
    return df


def tidy(df: pd.DataFrame) -> pd.DataFrame:
    # Municipalities with no pharmacy come as a row with an empty CNPJ ("-").
    df = df.reindex(columns=COLUMNS).dropna(subset=["cnpj"]).copy()
    df["cnpj"] = df["cnpj"].str.replace(r"\D", "", regex=True).str.zfill(14)
    df = df[df["cnpj"].str.fullmatch(r"\d{14}") & (df["cnpj"] != "0" * 14)]
    # The source has typos such as year 2090: an unusable date becomes empty.
    dates = pd.to_datetime(df["data_credenciamento"], dayfirst=True, errors="coerce")
    df["data_credenciamento"] = dates.where(dates <= pd.Timestamp.today()).dt.date
    # When files overlap, keep the row that has the municipality columns.
    return df.sort_values("cod_municipio_ibge", na_position="last").drop_duplicates("cnpj")


# ── Cached API lookups ───────────────────────────────────────────────────────

class Cache:
    """Lookup results in Neon (pfpb_cache), shared by every machine that runs this."""

    def __init__(self, tipo: str, keep: list):
        self.tipo, self.keep, self.dirty = tipo, keep, set()
        with connect() as conn, conn.cursor() as cur:
            cur.execute(CACHE_DDL)
            cur.execute("SELECT chave, dados FROM pfpb_cache WHERE tipo = %s", (tipo,))
            self.data = dict(cur.fetchall())
        limit = (date.today() - timedelta(days=CACHE_DAYS)).isoformat()
        self.stale = {k for k, v in self.data.items() if v["_at"] < limit}

    def put(self, key: str, res: dict):
        self.data[key] = {**{k: res[k] for k in self.keep if k in res}, "_at": date.today().isoformat()}
        self.stale.discard(key)
        self.dirty.add(key)

    def save(self):
        if not self.dirty:
            return
        with connect() as conn, conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, """
                INSERT INTO pfpb_cache (tipo, chave, dados) VALUES %s
                ON CONFLICT (tipo, chave) DO UPDATE SET dados = EXCLUDED.dados
            """, [(self.tipo, k, psycopg2.extras.Json(self.data[k])) for k in self.dirty])
        self.dirty.clear()


def _usage_get(week: str) -> int:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT (dados->>'used')::int FROM pfpb_cache WHERE tipo = 'geocode_usage' AND chave = %s", (week,))
        row = cur.fetchone()
    return row[0] if row else 0


def _usage_add(week: str) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO pfpb_cache (tipo, chave, dados) VALUES ('geocode_usage', %s, '{"used": 1}')
            ON CONFLICT (tipo, chave) DO UPDATE SET
              dados = jsonb_build_object('used', (pfpb_cache.dados->>'used')::int + 1)
        """, (week,))


def _get(url: str):
    last = None
    for attempt in range(5):
        try:
            r = requests.get(url, headers=UA, timeout=30)
            last = f"HTTP {r.status_code}"
            if r.status_code == 200:
                return r.json()
            if r.status_code in (400, 404):
                return {}
        except (requests.RequestException, ValueError) as e:
            last = type(e).__name__
        time.sleep(2 ** attempt * 2)  # 429 / 5xx / network → back off
    print(f"  gave up on {url.split('/')[2]}: {last}")
    return None  # give up for now; retried next run


def _pending(keys, cache: Cache) -> list:
    return [k for k in keys if k and (k not in cache.data or k in cache.stale)]


def lookup(keys, cache: Cache, base_url: str, label: str, deadline: float) -> int:
    """Fetch what is missing or stale until the deadline. Returns how many were fetched."""
    todo = _pending(keys, cache)
    print(f"{label}: {len(keys) - len(todo):,} cached, {len(todo):,} to fetch")
    done = fetched = failures = 0
    for k in todo:
        if time.time() > deadline:
            print(f"{label}: time budget reached, {len(todo) - done:,} left for the next run")
            break
        res = _get(f"{base_url}/{k}")
        if res is None:
            failures += 1
            if failures >= MAX_FAILURES:
                print(f"{label}: {failures} failures in a row, stopping; {len(todo) - done:,} left for the next run")
                break
        else:
            failures = 0
            fetched += 1
            cache.put(k, res)
        done += 1
        if done % 50 == 0:
            cache.save()
            print(f"  {done:,}/{len(todo):,}", end="\r")
        time.sleep(DELAY)
    cache.save()
    return fetched


# ── Assemble ─────────────────────────────────────────────────────────────────

def _cep(c: dict):
    return re.sub(r"\D", "", str(c.get("cep") or "")) or None


def _address(c: dict) -> str:
    street = " ".join(x for x in [c.get("descricao_tipo_de_logradouro"), c.get("logradouro")] if x)
    if not street.strip():
        return ""
    parts = [street, c.get("numero"), c.get("complemento")]
    return ", ".join(p.strip() for p in parts if p and p.strip())


def _first(*values):
    return next((v for v in values if v is not None and not pd.isna(v) and v != ""), None)


def _geocode_query(c: dict):
    # Street and number only: complements like "LOJA 01" confuse the geocoder.
    street = " ".join(x for x in [c.get("descricao_tipo_de_logradouro"), c.get("logradouro")] if x).strip()
    if not street:
        return None
    numero = str(c.get("numero") or "").strip()
    city = " - ".join(x for x in [c.get("municipio"), c.get("uf")] if x)
    parts = [f"{street}, {numero}" if numero else street, c.get("bairro"), city, _cep(c), "Brasil"]
    return ", ".join(p for p in parts if p)


def _geocode_queue(df: pd.DataFrame, cnpj_cache: Cache, cache: Cache) -> list:
    """New or changed addresses first (catalog chains before the rest), then the oldest points."""
    limit = (date.today() - timedelta(days=GEOCODE_REFRESH_DAYS)).isoformat()
    new, known, refresh = [], [], []
    chain = df["razao_social"].map(_norm).map(lambda n: any(re.search(rx, n) for _, rx in CHAINS))
    for cnpj, is_chain in zip(df["cnpj"], chain):
        query = _geocode_query(cnpj_cache.data.get(cnpj) or {})
        if not query:
            continue
        entry = cache.data.get(cnpj)
        if not entry or entry.get("consulta") != query:
            (known if is_chain else new).append((cnpj, query))
        elif entry["_at"] < limit:
            refresh.append((entry["_at"], cnpj, query))
    return known + new + [(cnpj, query) for _, cnpj, query in sorted(refresh)]


def geocode(df: pd.DataFrame, cnpj_cache: Cache, limit: int) -> Cache:
    """Google geocoding, at most GEOCODE_WEEKLY_LIMIT requests per week across all runs."""
    cache = Cache("geocode", GEOCODE_KEEP)
    key = os.environ.get("GEOCODE_API")
    if not key or limit <= 0:
        print("Geocode: skipped")
        return cache

    year, week, _ = date.today().isocalendar()
    this_week = f"{year}-W{week:02d}"
    used = _usage_get(this_week)
    budget = max(0, min(limit, GEOCODE_WEEKLY_LIMIT - used))
    queue = _geocode_queue(df, cnpj_cache, cache)
    print(f"Geocode: {len(queue):,} queued | {used:,} used this week | {budget:,} available now")

    errors = done = 0
    for cnpj, query in queue[:budget]:
        # Counted before the request, so a crash can never under-report usage.
        _usage_add(this_week)
        try:
            r = requests.get(GEOCODE_URL, params={"address": query, "components": "country:BR",
                                                  "region": "br", "key": key}, timeout=30).json()
        except (requests.RequestException, ValueError) as e:
            errors += 1
            print(f"  geocode error: {type(e).__name__}")  # the message would include the key
            if errors >= 3:
                break
            continue
        status = r.get("status")
        if status == "OK":
            errors = 0
            hit = r["results"][0]
            cache.put(cnpj, {"lat": hit["geometry"]["location"]["lat"], "lng": hit["geometry"]["location"]["lng"],
                             "precisao": hit["geometry"]["location_type"], "parcial": bool(hit.get("partial_match")),
                             "endereco_google": hit.get("formatted_address"), "consulta": query, "status": status})
        elif status == "ZERO_RESULTS":
            errors = 0
            cache.put(cnpj, {"consulta": query, "status": status})
        else:
            print(f"  geocode stopped: {status} {r.get('error_message', '')}")
            break
        done += 1
        if done % 50 == 0:
            cache.save()
        time.sleep(0.1)
    cache.save()
    print(f"Geocode: {done:,} answered, {max(0, len(queue) - done):,} left for later runs")
    return cache


def enrich(df: pd.DataFrame, cnpj_cache: Cache, cep_cache: Cache, geo_cache: Cache,
           branches: pd.Series) -> pd.DataFrame:
    rows = []
    for r in df.itertuples(index=False):
        c = cnpj_cache.data.get(r.cnpj) or {}
        cep = _cep(c)
        geo = cep_cache.data.get(cep) or {}
        google = geo_cache.data.get(r.cnpj) or {}
        # APPROXIMATE is usually a whole area; the CEP point is at least as good.
        # A point in another city means Google matched a same-named street there.
        city = _norm(_first(r.municipio, c.get("municipio")))
        use_google = ("lat" in google and google.get("precisao") != "APPROXIMATE"
                      and city in _norm(google.get("endereco_google")))
        if use_google:
            geo = google
        phone = re.sub(r"\D", "", str(c.get("ddd_telefone_1") or "")).strip("0") and c.get("ddd_telefone_1")
        rows.append({
            "cnpj": r.cnpj,
            "razao_social": _first(r.razao_social, c.get("razao_social")),
            "nome_fantasia": c.get("nome_fantasia") or None,
            "endereco": _first(_address(c), r.logradouro_painel),
            "bairro": _first(c.get("bairro"), r.bairro_painel),
            "municipio": _first(r.municipio, c.get("municipio")),
            "uf": _first(r.uf, c.get("uf")),
            "cod_municipio_ibge": r.cod_municipio_ibge,
            "regiao": r.regiao,
            "cep": cep,
            "telefone": phone or None,
            "latitude": pd.to_numeric(geo.get("lat"), errors="coerce"),
            "longitude": pd.to_numeric(geo.get("lng"), errors="coerce"),
            "geo_fonte": "google" if use_google else ("cep" if "lat" in geo else None),
            "geo_precisao": google.get("precisao") if use_google else None,
            "geo_parcial": google.get("parcial") if use_google else None,
            "geo_endereco_google": google.get("endereco_google") if use_google else None,
            "data_credenciamento": r.data_credenciamento,
            "situacao_cadastral": c.get("descricao_situacao_cadastral"),
            "fonte": r.fonte,
        })
    out = pd.DataFrame(rows)
    name = out["nome_fantasia"].map(_norm) + " " + out["razao_social"].map(_norm)
    out["rede"] = name.str.strip().map(lambda n: next((k for k, rx in CHAINS if re.search(rx, n)), None))
    # Same name is not same chain: independents like "Drogaria São João" are
    # separate companies, while a real chain has many branches under one CNPJ root.
    out.loc[out["cnpj"].str[:8].map(branches).fillna(0) < MIN_CHAIN_BRANCHES, "rede"] = None
    return out.sort_values(["uf", "municipio", "bairro", "razao_social"], na_position="last")


def main():
    from correlate import load_env
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="input", help="pharmacy list file, or a folder of them")
    ap.add_argument("--full-list", action="store_true",
                    help="input is the complete national list: Neon drops pharmacies missing from it")
    ap.add_argument("--out", default="output")
    ap.add_argument("--max-lookup-minutes", type=float, default=600,
                    help="stop API lookups after this long; the next run continues from the cache")
    ap.add_argument("--geocode-limit", type=int, default=GEOCODE_WEEKLY_LIMIT,
                    help=f"Google requests for this run (0 = none); never above {GEOCODE_WEEKLY_LIMIT} per week")
    ap.add_argument("--from-db", action="store_true", help="use the list in pfpb_farmacias instead of files")
    ap.add_argument("--limit", type=int, help="process only this many pharmacies, not yet looked up first (tests)")
    ap.add_argument("--no-cep", action="store_true",
                    help="skip CEP coordinates (AwesomeAPI answers 429 to GitHub Actions runners)")
    ap.add_argument("--save-db", action="store_true")
    args = ap.parse_args()

    df = tidy(read_neon() if args.from_db else read_inputs(args.input))
    print(f"{len(df):,} pharmacies")
    if args.full_list and len(df) < MIN_PHARMACIES:
        raise SystemExit(f"only {len(df):,} pharmacies (expected at least {MIN_PHARMACIES:,}); nothing saved")

    branches = df["cnpj"].str[:8].value_counts()  # before --limit: counts the whole list
    deadline = time.time() + args.max_lookup_minutes * 60
    cnpj_cache, cep_cache = Cache("cnpj", CNPJ_KEEP), Cache("cep", GEO_KEEP)
    if args.limit:
        done = df["cnpj"].isin(cnpj_cache.data.keys())
        df = df.assign(_done=done).sort_values("_done", kind="stable").head(args.limit).drop(columns="_done")
    fetched = lookup(df["cnpj"].tolist(), cnpj_cache, f"{API}/cnpj/v1", "CNPJ", deadline)
    ceps = sorted({_cep(cnpj_cache.data.get(c) or {}) for c in df["cnpj"]} - {None})
    pending = len(_pending(df["cnpj"].tolist(), cnpj_cache))
    if not args.no_cep:
        fetched += lookup(ceps, cep_cache, GEO_API, "CEP", deadline)
        pending += len(_pending(ceps, cep_cache))
    print(f"Lookups: {fetched:,} fetched, {pending:,} pending")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"fetched={fetched}\npending={pending}\n")
    geo_cache = geocode(df, cnpj_cache, args.geocode_limit)
    out = enrich(df, cnpj_cache, cep_cache, geo_cache, branches)

    os.makedirs(args.out, exist_ok=True)
    out.to_csv(f"{args.out}/pfpb_farmacias.csv", index=False, encoding="utf-8-sig")
    print(f"{len(out):,} pharmacies -> {args.out}/pfpb_farmacias.csv")
    print(f"  CEP: {out['cep'].notna().sum():,} | phone: {out['telefone'].notna().sum():,} | "
          f"coords: {out['latitude'].notna().sum():,} | "
          f"not ATIVA: {out['situacao_cadastral'].notna().sum() - (out['situacao_cadastral'] == 'ATIVA').sum():,}")
    print(out["geo_precisao"].fillna(out["geo_fonte"]).value_counts(dropna=False).to_string())
    print(out["rede"].value_counts().to_string())
    if args.save_db:
        from db.save import save_farmacias
        save_farmacias(out, full_list=args.full_list)


if __name__ == "__main__":
    main()
