"""
elenco.py — Official Farmácia Popular (PFPB) list of free medicines/supplies,
encoded as matching rules against free-text product names.

Source: https://www.gov.br/saude/pt-br/composicao/sectics/farmacia-popular/arquivos/elenco-de-medicamentos-e-insumos-pfpb.pdf
Version: "Atualizada em 31/08/2026" — 41 items, all free (gratuito) since 2025.

Each rule is matched against a *normalized* product name (see normalize.py):
  ingredients : every group must match (regex, accent-free, lowercase)
  doses       : every dose token must appear (normalized like "50mg", "0.03mg");
                "a|b" accepts either spelling (e.g. "5mg|0.5%")
  exclude     : product is rejected if any of these match
  form        : optional extra regex the product must match (e.g. nasal spray)
"""

from dataclasses import dataclass, field
from typing import List

ELENCO_VERSION = "2026-08-31"


@dataclass
class Item:
    id: str
    indicacao: str
    principio_ativo: str
    ingredients: List[str]
    doses: List[str] = field(default_factory=list)
    exclude: List[str] = field(default_factory=list)
    form: str = ""


# Release-modifier words that turn a plain tablet into a different product.
_XR = r"\b(xr|ap|lp|retard|prolongada|liberacao prolongada|acao prolongada|sr|cr)\b"
# Inhalation vs nasal for beclometasona/budesonida.
_NASAL = r"\b(nasal|rinite|aquoso)\b"

ELENCO: List[Item] = [
    # ── ASMA ──────────────────────────────────────────────────────────────
    Item("ASM01", "ASMA", "brometo de ipratrópio 0,02mg", [r"ipratropio"], ["0.02mg|20mcg"]),
    Item("ASM02", "ASMA", "brometo de ipratrópio 0,25mg", [r"ipratropio"], ["0.25mg|0.025%"]),
    Item("ASM03", "ASMA", "dipropionato de beclometasona 200mcg", [r"beclometasona"], ["200mcg"], [_NASAL]),
    Item("ASM04", "ASMA", "dipropionato de beclometasona 250mcg", [r"beclometasona"], ["250mcg"], [_NASAL]),
    Item("ASM05", "ASMA", "dipropionato de beclometasona 50mcg", [r"beclometasona"], ["50mcg"], [_NASAL]),
    Item("ASM06", "ASMA", "sulfato de salbutamol 100mcg", [r"salbutamol"], ["100mcg"]),
    Item("ASM07", "ASMA", "sulfato de salbutamol 5mg", [r"salbutamol"], ["5mg"]),
    # ── DIABETES ──────────────────────────────────────────────────────────
    Item("DIA01", "DIABETES", "cloridrato de metformina 500mg", [r"metformina"], ["500mg"], [_XR]),
    Item("DIA02", "DIABETES", "cloridrato de metformina 500mg - ação prolongada", [r"metformina"], ["500mg"], form=_XR),
    Item("DIA03", "DIABETES", "cloridrato de metformina 850mg", [r"metformina"], ["850mg"], [_XR]),
    Item("DIA04", "DIABETES", "glibenclamida 5mg", [r"glibenclamida"], ["5mg"]),
    Item("DIA05", "DIABETES", "insulina humana regular 100ui/ml", [r"insulina", r"\bregular\b"], ["100ui/ml"],
         [r"\b(glargina|lispro|asparte|aspart|detemir|degludeca|glulisina|nph|isofana)\b"]),
    Item("DIA06", "DIABETES", "insulina humana 100ui/ml (NPH)", [r"insulina", r"\b(nph|isofana)\b"], ["100ui/ml"],
         [r"\b(glargina|lispro|asparte|aspart|detemir|degludeca|glulisina|regular)\b"]),
    # ── HIPERTENSÃO ───────────────────────────────────────────────────────
    Item("HIP01", "HIPERTENSÃO", "atenolol 25mg", [r"atenolol"], ["25mg"]),
    Item("HIP02", "HIPERTENSÃO", "besilato de anlodipino 5mg", [r"anlodipino"], ["5mg"]),
    Item("HIP03", "HIPERTENSÃO", "captopril 25mg", [r"captopril"], ["25mg"]),
    Item("HIP04", "HIPERTENSÃO", "cloridrato de propranolol 40mg", [r"propranolol"], ["40mg"]),
    Item("HIP05", "HIPERTENSÃO", "hidroclorotiazida 25mg", [r"hidroclorotiazida"], ["25mg"]),
    Item("HIP06", "HIPERTENSÃO", "losartana potássica 50mg", [r"losartana"], ["50mg"]),
    Item("HIP07", "HIPERTENSÃO", "maleato de enalapril 10mg", [r"enalapril"], ["10mg"]),
    Item("HIP08", "HIPERTENSÃO", "espironolactona 25mg", [r"espironolactona"], ["25mg"]),
    Item("HIP09", "HIPERTENSÃO", "furosemida 40mg", [r"furosemida"], ["40mg"]),
    Item("HIP10", "HIPERTENSÃO", "succinato de metoprolol 25mg", [r"metoprolol", r"succinato"], ["25mg"], [r"tartarato"]),
    # ── ANTICONCEPÇÃO ─────────────────────────────────────────────────────
    Item("ANT01", "ANTICONCEPÇÃO", "acetato de medroxiprogesterona 150mg", [r"medroxiprogesterona"], ["150mg"]),
    Item("ANT02", "ANTICONCEPÇÃO", "etinilestradiol 0,03mg + levonorgestrel 0,15mg",
         [r"etinilestradiol", r"levonorgestrel"], ["0.03mg", "0.15mg"]),
    Item("ANT03", "ANTICONCEPÇÃO", "noretisterona 0,35mg", [r"noretisterona"], ["0.35mg"], [r"enantato|estradiol"]),
    Item("ANT04", "ANTICONCEPÇÃO", "valerato de estradiol 5mg + enantato de noretisterona 50mg",
         [r"estradiol", r"noretisterona"], ["5mg", "50mg"]),
    # ── OSTEOPOROSE ───────────────────────────────────────────────────────
    Item("OST01", "OSTEOPOROSE", "alendronato de sódio 70mg", [r"alendronato"], ["70mg"], [r"\d+ui\b", r"\bfosamax d\b"]),
    # ── DISLIPIDEMIA ──────────────────────────────────────────────────────
    Item("DIS01", "DISLIPIDEMIA", "sinvastatina 10mg", [r"sinvastatina"], ["10mg"]),
    Item("DIS02", "DISLIPIDEMIA", "sinvastatina 20mg", [r"sinvastatina"], ["20mg"]),
    Item("DIS03", "DISLIPIDEMIA", "sinvastatina 40mg", [r"sinvastatina"], ["40mg"]),
    # ── DOENÇA DE PARKINSON ───────────────────────────────────────────────
    Item("PAR01", "DOENÇA DE PARKINSON", "carbidopa 25mg + levodopa 250mg", [r"carbidopa", r"levodopa"], ["25mg", "250mg"]),
    Item("PAR02", "DOENÇA DE PARKINSON", "cloridrato de benserazida 25mg + levodopa 100mg",
         [r"benserazida", r"levodopa"], ["25mg", "100mg"]),
    # ── GLAUCOMA ──────────────────────────────────────────────────────────
    Item("GLA01", "GLAUCOMA", "maleato de timolol 2,5mg", [r"timolol"], ["2.5mg|0.25%"]),
    Item("GLA02", "GLAUCOMA", "maleato de timolol 5mg", [r"timolol"], ["5mg|0.5%"]),
    # ── RINITE ────────────────────────────────────────────────────────────
    Item("RIN01", "RINITE", "budesonida 32mcg", [r"budesonida"], ["32mcg"]),
    Item("RIN02", "RINITE", "budesonida 50mcg", [r"budesonida"], ["50mcg"]),
    Item("RIN03", "RINITE", "dipropionato de beclometasona 50mcg/dose (nasal)", [r"beclometasona"], ["50mcg"], form=_NASAL),
    # ── DIABETES + DOENÇA CARDIOVASCULAR ──────────────────────────────────
    Item("DCV01", "DIABETES MELLITUS + DOENÇA CARDIOVASCULAR", "dapagliflozina 10mg", [r"dapagliflozina"], ["10mg"]),
    # ── INSUMOS ───────────────────────────────────────────────────────────
    Item("DMS01", "DIGNIDADE MENSTRUAL", "absorvente higiênico", [r"\babsorventes?\b"],
         exclude=[r"\b(seio|seios|mamas?|amamentacao|lactante|leite|axila|suor|odor|calcinha)\b",
                  r"\b(interno|internos|protetor diario|protetores diarios|geriatric\w*|incontinencia|plenitud|masculino|unissex)\b"]),
    Item("INC01", "INCONTINÊNCIA URINÁRIA", "fralda geriátrica",
         [r"\bfraldas?\b", r"(geriatric|adult|incontinencia|bigfral|\btena\b|plenitud|geriatex|biofral)"],
         exclude=[r"\b(alfinete|lenco|creme|pomada|trocador|lixeira)\b",
                  r"\b(infantil|bebe|baby|rn|recem|kids|crianca|turma|pampers|huggies|mamypoko|babysec|pom pom)\b"]),
]

# Active ingredients that, if present alongside a single-ingredient item,
# mean the product is a fixed-dose combination — NOT on the list.
COMBO_INGREDIENTS = [
    "hidroclorotiazida", "clortalidona", "indapamida", "amilorida", "anlodipino", "losartana",
    "valsartana", "olmesartana", "telmisartana", "candesartana", "irbesartana", "enalapril",
    "ramipril", "perindopril", "atenolol", "bisoprolol", "nebivolol", "metoprolol", "espironolactona",
    "furosemida", "ezetimiba", "sitagliptina", "vildagliptina", "linagliptina", "saxagliptina",
    "alogliptina", "empagliflozina", "dapagliflozina", "glibenclamida", "glimepirida", "gliclazida",
    "metformina", "pioglitazona", "fenoterol", "formoterol", "salmeterol", "vilanterol",
    "beclometasona", "budesonida", "fluticasona", "mometasona", "ipratropio", "salbutamol",
    "azelastina", "dorzolamida", "brimonidina", "latanoprosta", "travoprosta", "bimatoprosta",
    "brinzolamida", "entacapona", "colecalciferol", "vitamina d", "carbidopa", "benserazida",
    "levodopa", "levonorgestrel", "etinilestradiol", "ciproterona", "drospirenona", "gestodeno",
    "desogestrel", "estradiol", "noretisterona", "medroxiprogesterona", "dienogeste",
    "benazepril", "latanoprost", "brimondina", "levanlodipino",
]

# Brand names of fixed-dose combinations whose titles often show only one of
# the ingredients (e.g. "Micardis Hidroclorotiazida 80 + 25mg").
COMBO_BRANDS = (
    r"\b(micardis|diovan|concor|benicar|brasart|atacand|aprovel|olmetec|press plus|lotar|"
    r"xalacom|latonan|cosopt|combigan|exforge|janumet|xigduo|galvus met|jardiance duo|"
    r"tenoretic|caduet|vytorin|zetsim|seretide|symbicort|alenia|foraseq|duovent|combivent|fostair)\b"
)
# "80 + 25mg", "160/25mg", "2+5 mg/ml": two strengths in one product.
TWO_STRENGTHS = r"\d(?:\.\d+)?\s*(?:mg|mcg)?\s*[+/]\s*\d"

# Brand / reference names that usually omit the active ingredient in the
# product title. Matching via alias is reported with lower confidence.
# (brand regex, ingredient injected, regex that disqualifies the alias)
BRAND_ALIASES = [
    (r"\bglifage\b", "metformina", r""),
    (r"\bdaonil\b", "glibenclamida", r""),
    (r"\baerolin\b", "salbutamol", r""),
    (r"\batrovent\b", "ipratropio", r""),
    (r"\bclenil\b", "beclometasona", r"\b(compositum|hfa? ?a?)\b(?!.*\bmcg)"),
    (r"\bnorvasc\b", "anlodipino", r""),
    (r"\bcapoten\b", "captopril", r""),
    (r"\b(cozaar|aradois|corus|losartec)\b", "losartana", r"\b(h|hct|plus|duo)\b"),
    (r"\brenitec\b", "enalapril", r""),
    (r"\baldactone\b", "espironolactona", r"\b(a)\b"),
    (r"\blasix\b", "furosemida", r""),
    (r"\bselozok\b", "metoprolol succinato", r"\b(hct|amlo)\b"),
    (r"\batenol\b", "atenolol", r""),
    (r"\bclorana\b", "hidroclorotiazida", r""),
    (r"\b(depo.?provera|contracep)\b", "medroxiprogesterona", r""),
    (r"\b(ciclo 21|microvlar|nordette)\b", "etinilestradiol levonorgestrel", r""),
    (r"\b(micronor|norestin)\b", "noretisterona", r""),
    (r"\bmesigyna\b", "estradiol noretisterona", r""),
    (r"\bfosamax\b", "alendronato", r"\bplus\b"),
    (r"\bzocor\b", "sinvastatina", r""),
    (r"\bsinemet\b", "carbidopa levodopa", r""),
    (r"\bprolopa\b", "benserazida levodopa", r"\b(hbs|dispersivel|bd)\b"),
    (r"\btimoptol\b", "timolol", r"\bxe\b"),
    (r"\b(busonid|noex)\b", "budesonida", r""),
    (r"\bforxiga\b", "dapagliflozina", r""),
    (r"\b(humulin|novolin)\s*r\b", "insulina regular", r""),
    (r"\b(humulin|novolin)\s*n\b", "insulina nph", r""),
]
