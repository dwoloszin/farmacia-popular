# FARMACIA_POPULAR

Finds which products in the `markets_db_farm` catalog are on the free
**Farmácia Popular do Brasil (PFPB)** list, and where they can be collected.

## Sources

| Data | Source |
|------|--------|
| Free medicines/supplies (41 items, list of 31/08/2026) | [Elenco PFPB (PDF)](https://www.gov.br/saude/pt-br/composicao/sectics/farmacia-popular/arquivos/elenco-de-medicamentos-e-insumos-pfpb.pdf) → encoded in `fp/elenco.py` |
| Accredited pharmacies | List files in `input/` (exports from the [Ministry dashboard](https://infoms.saude.gov.br/extensions/SEIDIGI_DEMAS_PFPB_ENDERECOS/index.html)) → `pharmacies.py` |
| Addresses and coordinates | CNPJ → BrasilAPI; CEP → AwesomeAPI; address → Google Geocoding (`GEOCODE_API`, at most 1,000 requests per week) |

All 41 items are free. To collect them you need a photo ID with CPF and a
valid prescription (SUS or private). Diapers also need a report or certificate
(age 60+ or person with disability, with the CID code). Pads need the Dignidade
Menstrual authorization from Meu SUS Digital.

## How matching works

Each list item is a rule in `fp/elenco.py`: the active ingredient(s), the exact
dose(s), and exclusions. `fp/matcher.py` normalizes product names (accents,
`0,5 mg` → `0.5mg`, `µg` → `mcg`) and accepts a product only when:

- every ingredient and every dose is present (`5mg` never matches `25mg`)
- no other active ingredient appears and no combo brand appears
  (Micardis HCT, Xalacom…); a single-drug item never matches `80 + 25mg`
- the release form fits (metformina XR is a separate item; beclometasona nasal
  counts as rinite, inhaled counts as asma)

`match_via = brand` means the name had no ingredient and matched through a
reference brand (Glifage, Selozok, Forxiga…). Review those first.

## Usage

```bash
pip install -r requirements.txt
cp .env.template .env        # every DATABASE_URL_<STORE> + DATABASE_URL_PFPB

python correlate.py              # read all store DBs -> output/*.csv (review, nothing saved)
python correlate.py --save-db    # same, and save into DATABASE_URL_PFPB
python correlate.py --csv <export.csv>   # offline, from markets_db_farm export-all-together

python pharmacies.py [--save-db]  # enrich the pharmacy lists in input/
python pharmacies.py --input list.csv --full-list --save-db  # national list: also drop removed pharmacies
```

Outputs (`output/`):

| File | Content |
|------|---------|
| `pfpb_elenco.csv` | the 41 free items |
| `pfpb_correlacao.csv` | one row per matched store offer (saved as `pfpb_produtos`) |
| `pfpb_por_ean.csv` | one row per EAN: stores carrying it, min/median price |
| `pfpb_resumo.csv` | per item: products, EANs, stores, price range |
| `pfpb_farmacias.csv` | accredited pharmacies |

## Weekly update

`.github/workflows/weekly.yml` refreshes the medicine correlation every Monday
09:00 UTC (plus manual dispatch). It needs one repo secret, `ENV_FILE`, holding
the full `.env` contents. Result CSVs are kept as a workflow artifact for 30 days.

`.github/workflows/pharmacies.yml` runs Mondays 11:00 UTC on the list already in
`pfpb_farmacias`: CNPJ lookups and Google coordinates. Progress is in Neon
(`pfpb_cache`), so a run stopped by the job limit restarts itself and continues.
CEP coordinates only work from a normal connection (AwesomeAPI answers 429 to
Actions runners): run `python pharmacies.py --from-db --save-db --geocode-limit 0`
locally. To get the CSV, run it with `--max-lookup-minutes 0` and without `--save-db`.

## Layout

```
correlate.py        catalog -> PFPB correlation (main entry)
pharmacies.py       pharmacy lists -> full address, CEP, phone, coordinates
fp/elenco.py        the official list as matching rules  <- update when the PDF changes
fp/matcher.py       name normalization + rule matching
fp/sources.py       reads `offers` from every store DB in parallel
db/save.py          Neon schema + writes (DATABASE_URL_PFPB)
```

## Neon schema (`db/save.py`)

- `pfpb_elenco`: the free list
- `pfpb_produtos`: catalog products → `pfpb_id`
- `pfpb_farmacias`: accredited pharmacies with a `rede` column that maps to
  catalog `store_name`
- `pfpb_onde_retirar` (view): free product × accredited branch of the same chain

Any accredited pharmacy dispenses the whole list, so the chain join is a
convenience. To see every option in a city, filter `pfpb_farmacias` by
`uf, municipio`.

A pharmacy gets a chain tag only if its name matches `CHAINS` in `pharmacies.py`
and its company (CNPJ root) has at least 5 branches on the list, because different
independent pharmacies can share a name. Companies in `NOT_CHAIN_ROOTS` never get
a tag: they pass that test but are not confirmed as the catalog's chain.
