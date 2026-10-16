"""Viską sujungianti CLI komunalinių sąskaitų ciklui.

Paleidžia iš eilės: el. pašto sąskaitų atsisiuntimą, CHC šildymo portalo
scraperį, sumos paskaičiavimą - ir PARODO rezultatą peržiūrai. Laiškų
siuntimas (`send_summary.py`) paleidžiamas TIK su `--send`, ir tik po to,
kai naudotojas patvirtina matomą sumą (arba `--yes`, jei paleidžiama
nesaugiai/automatiškai) - tai sąmoningas pusiau automatinio režimo
kontrolinis taškas (žr. NOTES.md).

Naudojimas:
    python scripts/main.py run --year 2026 --month 9 --test
    python scripts/main.py run --year 2026 --month 9 --test --send
    python scripts/main.py run --year 2026 --month 9 --no-test --send   (TIKRAS ciklas)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "fetchers"))
from common import get_logger, load_config, target_folder  # noqa: E402

log = get_logger("main")


def run_step(description: str, args: list[str]) -> bool:
    log.info("=== %s ===", description)
    result = subprocess.run([sys.executable, *args])
    if result.returncode != 0:
        log.error("%s NEPAVYKO (exit code %d)", description, result.returncode)
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["run"])
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, required=True)
    parser.add_argument("--property", dest="property_id")
    parser.add_argument("--test", dest="test_mode", action="store_true", default=None)
    parser.add_argument("--no-test", dest="test_mode", action="store_false")
    parser.add_argument("--send", action="store_true", help="po skaičiavimo taip pat siųsti laiškus")
    parser.add_argument("--yes", action="store_true", help="nepaklausti patvirtinimo prieš siunčiant")
    args = parser.parse_args()

    config = load_config()
    test_mode = args.test_mode if args.test_mode is not None else config["test_mode"]["enabled"]
    test_flag = ["--test"] if test_mode else ["--no-test"]
    ym = ["--year", str(args.year), "--month", str(args.month)]
    prop_flag = ["--property", args.property_id] if args.property_id else []

    ok = run_step(
        "1/4 El. pašto sąskaitų atsisiuntimas",
        [str(ROOT / "fetchers" / "email_fetcher.py"), *ym, *test_flag],
    )
    if not ok:
        sys.exit(1)

    ok = run_step(
        "2/4 CHC šildymo portalo atsisiuntimas",
        [str(ROOT / "fetchers" / "portals" / "chc_heating.py"), *ym, *test_flag],
    )
    if not ok:
        sys.exit(1)

    ok = run_step(
        "3/4 Sumos paskaičiavimas",
        [str(ROOT / "processing" / "compute_total.py"), *ym, *test_flag, *prop_flag],
    )
    if not ok:
        sys.exit(1)

    log.info("=== Rezultatų peržiūra ===")
    prop_ids = [args.property_id] if args.property_id else list(config["properties"].keys())
    for prop_id in prop_ids:
        amount_path = target_folder(config["properties"][prop_id], args.year, args.month, test_mode) / "amount.txt"
        if amount_path.exists():
            print(f"\n----- {prop_id} -----")
            print(amount_path.read_text(encoding="utf-8"))
        else:
            log.warning("[%s] amount.txt nerastas", prop_id)

    if not args.send:
        log.info("4/4 Laiškų siuntimas PRALEISTA (paleisti su --send, kai suma patvirtinta)")
        return

    if not args.yes:
        confirm = input("\nAr suma teisinga ir siųsti laiškus? [y/N] ")
        if confirm.strip().lower() != "y":
            log.info("Siuntimas atšauktas naudotojo.")
            return

    run_step(
        "4/4 Laiškų siuntimas",
        [str(ROOT / "mailer" / "send_summary.py"), *ym, *test_flag, *prop_flag,
         *(["--yes"] if args.yes else [])],
    )


if __name__ == "__main__":
    main()
