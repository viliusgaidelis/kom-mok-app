"""Šildymo (CHC / AB "Miesto gijos") sąskaitų atsisiuntimas iš savitarna.chc.lt
(iš tikrųjų nukreipia į savitarna.miestogijos.lt).

Vienintelis tiekėjas šiame projekte, kurio sąskaitos NEATEINA el. paštu -
jas reikia atsisiųsti iš savitarnos portalo (žr. NOTES.md).

Portalo srautas (patikrinta Playwright žvalgymu 2026-10-15):
  1. GET  https://savitarna.chc.lt/saskaitos/  -> login forma (ASP.NET WebForms)
     laukai: #MainContent_txt1 (kodas), #MainContent_txt2 (slaptažodis),
     #MainContent_btn (submit)
  2. Po sėkmingo prisijungimo -> https://savitarna.miestogijos.lt/start
     Nesėkmės atveju -> .../noaccess
  3. https://savitarna.miestogijos.lt/cbills -> "Sąskaitų istorija" lentelė,
     kiekviena eilutė: "{year}m. {mėnuo nominatyvu}" + nuorodos su
     __doPostBack('ctl00$MainContent$gv', 'Saskaita$N') / 'Priedas$N'
     (N = eilutės indeksas, 0 = naujausias mėnuo)

Naudojimas:
    python fetchers/portals/chc_heating.py --year 2026 --month 9
    python fetchers/portals/chc_heating.py --year 2026 --month 9 --no-test
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # fetchers/
from common import already_have, get_logger, load_config, load_secrets, target_folder  # noqa: E402

log = get_logger("chc_heating")

MONTH_NAMES_NOMINATIVE = [
    "sausis", "vasaris", "kovas", "balandis", "gegužė", "birželis",
    "liepa", "rugpjūtis", "rugsėjis", "spalis", "lapkritis", "gruodis",
]

LOGIN_URL = "https://savitarna.chc.lt/saskaitos/"
BILLS_URL = "https://savitarna.miestogijos.lt/cbills"


def fetch_one(prop_id: str, prop_cfg: dict, secrets: dict, year: int, month: int, test_mode: bool) -> None:
    code_env = f"CHC_{prop_id.upper()}_CODE"
    pwd_env = f"CHC_{prop_id.upper()}_PASSWORD"
    code = secrets.get(code_env)
    password = secrets.get(pwd_env)
    if not code or not password:
        log.warning("[%s] trūksta %s/%s secrets.env faile, praleidžiama", prop_id, code_env, pwd_env)
        return

    folder = target_folder(prop_cfg, year, month, test_mode)
    filename = f"{code}_{year}_{month}.pdf"
    if already_have(folder, filename):
        log.info("[%s] jau yra %s, praleidžiama", prop_id, folder / filename)
        return

    month_label = f"{year}m. {MONTH_NAMES_NOMINATIVE[month - 1]}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(LOGIN_URL, wait_until="networkidle", timeout=30000)
            page.fill("#MainContent_txt1", code)
            page.fill("#MainContent_txt2", password)
            page.click("#MainContent_btn")
            page.wait_for_load_state("networkidle", timeout=30000)

            if "noaccess" in page.url:
                log.error("[%s] prisijungti nepavyko (neteisingas kodas/slaptažodis)", prop_id)
                return

            page.goto(BILLS_URL, wait_until="networkidle", timeout=30000)

            row = page.locator("tr", has_text=month_label)
            if row.count() == 0:
                log.warning("[%s] sąskaitų istorijoje nerasta eilutės '%s'", prop_id, month_label)
                return

            link = row.first.locator("a[href*='Saskaita']")
            with page.expect_download(timeout=30000) as dl_info:
                link.click()
            download = dl_info.value
            download.save_as(str(folder / filename))
            log.info("[%s] išsaugota %s (originalus pavadinimas: %s)",
                      prop_id, folder / filename, download.suggested_filename)
        finally:
            browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, required=True)
    parser.add_argument("--test", dest="test_mode", action="store_true", default=None)
    parser.add_argument("--no-test", dest="test_mode", action="store_false")
    args = parser.parse_args()

    config = load_config()
    secrets = load_secrets()
    test_mode = args.test_mode if args.test_mode is not None else config["test_mode"]["enabled"]

    for prop_id, prop_cfg in config["properties"].items():
        if "heating_chc" not in prop_cfg.get("providers", {}):
            continue
        fetch_one(prop_id, prop_cfg, secrets, args.year, args.month, test_mode)


if __name__ == "__main__":
    main()
