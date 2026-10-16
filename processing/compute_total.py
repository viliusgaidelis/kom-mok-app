"""Bendros mėnesio komunalinių mokesčių sumos paskaičiavimas iš atsisiųstų PDF.

Svarbiausios taisyklės:
  1. Visada naudojamas TIK einamojo mėnesio PRISKAIČIUOTAS mokestis, NE
     "mokėti iš viso" suma - pastaroji gali apimti praeito mėnesio neapmokėtą
     skolą, o tai nuomininko neliečia (tai nuomotojo pavėlavimas apmokėti
     tiekėjui, ne nuomininko įsiskolinimas).
  2. Kai kurių bendrijų/administratorių sąskaitoje būna KELI "Objektas:"
     skyriai - pvz. butas IR parkingo vieta, jei abu priklauso tam pačiam
     savininkui. Skaičiuoti TIK SAVO buto skyriaus eilutes (žr.
     `own_object_marker` konfigūracijoje - įrašykite dalį SAVO buto adreso).
  3. Nereguliarūs mokesčiai (pvz. metinė šiukšlių sąskaita / 12) laikomi
     atskirame tekstiniame faile (`waste_fee_filename` konfigūracijoje). Jis
     keičiasi retai, tad jei naujo mėnesio aplanke failo dar nėra, jis
     automatiškai nukopijuojamas iš praeito mėnesio aplanko.

Naudojimas:
    python processing/compute_total.py --year 2026 --month 9 --property butas1 --test
    python processing/compute_total.py --year 2026 --month 9 --property butas2 --test
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pdfplumber

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fetchers"))
from common import get_logger, load_config, target_folder  # noqa: E402

log = get_logger("compute_total")


def pdf_text(path: Path) -> str:
    with pdfplumber.open(path) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def to_float(s: str) -> float:
    return float(s.replace(".", "").replace(",", ".")) if "," in s else float(s)


# ---------------------------------------------------------------- parsers --

def parse_ignitis(text: str, year: int, month: int) -> float | None:
    m = re.search(rf"Už {year}-{month:02d} laikotarpį priskaičiuota\s+([\d,]+)\s*€", text)
    return to_float(m.group(1)) if m else None


def parse_water_vv(text: str) -> float | None:
    m = re.search(r"Suteikta paslaugų per laikotarpį\s+([\d,]+)", text)
    return to_float(m.group(1)) if m else None


def parse_chc_heating(text: str) -> float | None:
    m = re.search(r"Apskaičiuotapermėnesį,Eur\s*([\d,]+)", text)
    return to_float(m.group(1)) if m else None


def parse_adminsta(text: str) -> float | None:
    m = re.search(r"Iš viso suma su PVM\s*([\d.]+)", text)
    return float(m.group(1)) if m else None


def parse_manobustas_dual_object(text: str, own_object_marker: str) -> float | None:
    """Kai sąskaitoje gali būti KELI 'Objektas:' skyriai (pvz. butas + parkingo
    vieta) - grąžina sumą TIK to skyriaus, kurio antraštėje yra own_object_marker."""
    parts = re.split(r"(Objektas:[^\n]+)", text)
    own_body = None
    for i in range(1, len(parts), 2):
        header = parts[i]
        body = parts[i + 1] if i + 1 < len(parts) else ""
        if own_object_marker in header:
            own_body = body
            break
    if own_body is None:
        return None
    own_body = re.split(r"Suma,\s*€", own_body)[0]  # neįtraukti bendros sumos eilutės
    total = 0.0
    found_any = False
    for line in own_body.splitlines():
        nums = re.findall(r"\d+,\d{2}", line)
        if nums:
            total += to_float(nums[-1])
            found_any = True
    return total if found_any else None


# ------------------------------------------------------------ classifier --

WATER_RE = re.compile(r"^SFR\d+\.pdf$", re.IGNORECASE)
IGNITIS_RE = re.compile(r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\.pdf$", re.IGNORECASE)
MANOBUSTAS_RE = re.compile(r"^TRI-\d+\.pdf$", re.IGNORECASE)


def classify(filename: str, heating_code: str | None, adminsta_code: str | None, year: int, month: int) -> str | None:
    if WATER_RE.match(filename):
        return "water_vv"
    if IGNITIS_RE.match(filename):
        return "electricity_ignitis"
    if MANOBUSTAS_RE.match(filename):
        return "other_manobustas_butas1"
    if heating_code and filename == f"{heating_code}_{year}_{month}.pdf":
        return "heating_chc"
    if adminsta_code and re.match(rf"^K?{re.escape(adminsta_code)}\d{{6}}\.pdf$", filename):
        return "other_adminsta"
    return None


# ------------------------------------------------------------- waste fee --

def resolve_waste_fee(prop_cfg: dict, year: int, month: int, test_mode: bool) -> tuple[float | None, str]:
    """Grąžina (suma, paaiškinimas). Jei einamojo mėnesio aplanke failo nėra,
    bando nukopijuoti iš praeito mėnesio aplanko."""
    filename = prop_cfg.get("waste_fee_filename")
    if not filename:
        return None, "šiam butui šiukšlių mokestis netaikomas atskirai"

    folder = target_folder(prop_cfg, year, month, test_mode)
    current_path = folder / filename

    if not current_path.exists():
        prev_year, prev_month = (year, month - 1) if month > 1 else (year - 1, 12)
        prev_folder = target_folder(prop_cfg, prev_year, prev_month, test_mode)
        prev_path = prev_folder / filename
        if prev_path.exists():
            current_path.write_text(prev_path.read_text(encoding="utf-8"), encoding="utf-8")
            log.info("nukopijuotas %s -> %s", prev_path, current_path)
        else:
            return None, f"'{filename}' nerastas nei šiame, nei praeito mėnesio aplanke"

    content = current_path.read_text(encoding="utf-8")
    matches = re.findall(r"([\d,.]+)\s*EUR", content)
    if not matches:
        return None, f"'{filename}' rastas, bet jame nepavyko rasti sumos (ieškoma formato 'X,XX EUR')"
    return to_float(matches[-1]), f"iš '{filename}'"


# ------------------------------------------------------------------ main --

def compute(prop_id: str, prop_cfg: dict, year: int, month: int, test_mode: bool) -> None:
    folder = target_folder(prop_cfg, year, month, test_mode)
    heating_code = prop_cfg.get("providers", {}).get("heating_chc", {}).get("code")
    adminsta_code = prop_cfg.get("providers", {}).get("other_adminsta", {}).get("code")
    own_object_marker = prop_cfg.get("own_object_marker")

    items: list[tuple[str, float]] = []

    for pdf_path in sorted(folder.glob("*.pdf")):
        kind = classify(pdf_path.name, heating_code, adminsta_code, year, month)
        if kind is None:
            log.warning("[%s] neatpažintas PDF %s, praleidžiamas", prop_id, pdf_path.name)
            continue
        text = pdf_text(pdf_path)
        amount: float | None
        if kind == "water_vv":
            amount = parse_water_vv(text)
            label = "Vilniaus vandenys (vanduo)"
        elif kind == "electricity_ignitis":
            amount = parse_ignitis(text, year, month)
            label = "Ignitis (elektra)"
        elif kind == "heating_chc":
            amount = parse_chc_heating(text)
            label = "CHC / Miesto gijos (šildymas)"
        elif kind == "other_adminsta":
            amount = parse_adminsta(text)
            label = "UAB Adminsta"
        elif kind == "other_manobustas_butas1":
            amount = parse_manobustas_dual_object(text, own_object_marker)
            label = "Bendrijos/administratoriaus sąskaita (tik buto dalis, be kito objekto ir skolos)"
        else:
            amount = None
            label = kind

        if amount is None:
            log.warning("[%s] nepavyko ištraukti sumos iš %s (%s)", prop_id, pdf_path.name, kind)
            continue
        items.append((f"{label} [{pdf_path.name}]", amount))
        log.info("[%s] %s = %.2f", prop_id, label, amount)

    waste_amount, waste_note = resolve_waste_fee(prop_cfg, year, month, test_mode)
    if waste_amount is not None:
        items.append((f"Šiukšlių išvežimas ({waste_note})", waste_amount))
        log.info("[%s] Šiukšlių išvežimas = %.2f (%s)", prop_id, waste_amount, waste_note)
    else:
        log.warning("[%s] šiukšlių mokestis NEĮTRAUKTAS: %s", prop_id, waste_note)

    total = sum(amount for _, amount in items)

    lines = [f"# {prop_cfg['display_name']} {year}-{month:02d} suvestinė (automatiškai sugeneruota)", ""]
    for label, amount in items:
        lines.append(f"{label}: {f'{amount:.2f}'.replace('.', ',')}")
    lines.append("")
    formula = "=" + "+".join(f"{amount:.2f}".replace(".", ",") for _, amount in items)
    lines.append(formula)
    lines.append("")
    lines.append(f"{total:.2f}".replace(".", ",") + " €")

    out_path = folder / "amount.txt"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("[%s] IŠ VISO = %.2f € -> %s", prop_id, total, out_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, required=True)
    parser.add_argument("--property", dest="property_id", choices=None)
    parser.add_argument("--test", dest="test_mode", action="store_true", default=None)
    parser.add_argument("--no-test", dest="test_mode", action="store_false")
    args = parser.parse_args()

    config = load_config()
    test_mode = args.test_mode if args.test_mode is not None else config["test_mode"]["enabled"]

    prop_ids = [args.property_id] if args.property_id else list(config["properties"].keys())
    for prop_id in prop_ids:
        compute(prop_id, config["properties"][prop_id], args.year, args.month, test_mode)


if __name__ == "__main__":
    main()
