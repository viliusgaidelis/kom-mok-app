"""Laiško nuomininkui siuntimas su bendra komunalinių mokesčių suma ir
prisegtomis sąskaitomis.

Skaito JAU PARUOŠTĄ `amount.txt` (žr. `processing/compute_total.py`) - NEperskaičiuoja
sumos iš naujo. Taip naudotojas gali peržiūrėti/pataisyti `amount.txt` PRIEŠ
siunčiant (sąmoningas pusiau automatinis režimas - žmogus visada peržiūri
sumą prieš bet kokį realų laiško siuntimą).

Testavimo metu (`--test`, numatytasis) laiškas siunčiamas naudotojui pačiam
(žr. `config/properties.yaml` -> `test_mode`), NE nuomininkui.

Naudojimas:
    python mailer/send_summary.py --year 2026 --month 9 --test
    python mailer/send_summary.py --year 2026 --month 9 --property butas1 --test
    python mailer/send_summary.py --year 2026 --month 9 --no-test   (TIKRAS siuntimas nuomininkui)
"""

from __future__ import annotations

import argparse
import re
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fetchers"))
from common import get_logger, load_config, load_secrets, target_folder  # noqa: E402

log = get_logger("send_summary")

MONTH_NAMES = {
    "lt": ["sausio", "vasario", "kovo", "balandžio", "gegužės", "birželio",
           "liepos", "rugpjūčio", "rugsėjo", "spalio", "lapkričio", "gruodžio"],
    "en": ["January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"],
}

# Vidinio žymėjimo (compute_total.py) -> nuomininkui rodomas pavadinimas, pagal
# kalbą. Pirmas elementas poroje - dalis teksto, ieškoma `label` viduje
# (todėl veikia su bet kokiu tiekėjo/bendrijos pavadinimu); pridėkite savo
# bendrijos pavadinimo dalį analogiškai "Adminsta" eilutei.
FRIENDLY_LABELS = {
    "lt": [
        ("Ignitis", "Elektra"),
        ("vandenys", "Vanduo"),
        ("Miesto gijos", "Šildymas"),
        ("CHC", "Šildymas"),
        ("Adminsta", "Pastato priežiūra / kitos paslaugos"),
        ("Šiukšlių", "Atliekų išvežimas"),
    ],
    "en": [
        ("Ignitis", "Electricity"),
        ("vandenys", "Water"),
        ("Miesto gijos", "Heating"),
        ("CHC", "Heating"),
        ("Adminsta", "Building maintenance / other services"),
        ("Šiukšlių", "Waste collection"),
    ],
}

TEXT = {
    "lt": {
        "greeting": "Laba diena,",
        "intro": "Siunčiame {month} mėn. komunalinių paslaugų sąskaitas už {address}.",
        "summary_label": "Suvestinė:",
        "total_label": "Iš viso mokėtina suma:",
        "attached": "Sąskaitų kopijos pridėtos prie šio laiško.",
        "closing": "Ačiū,\nVilius",
        "disclaimer": (
            "Šį laišką, sąskaitų atranką ir sumos paskaičiavimą automatiškai "
            "atliko dirbtinio intelekto asistentas (Anthropic Claude). Jei "
            "pastebėjote klaidą ar kažkas neaišku, prašome kreiptis tiesiai į "
            "Vilių."
        ),
    },
    "en": {
        "greeting": "Hello,",
        "intro": "Please find attached the utility invoices for {month} for {address}.",
        "summary_label": "Summary:",
        "total_label": "Total amount due:",
        "attached": "Copies of the invoices are attached to this email.",
        "closing": "Thank you,\nVilius",
        "disclaimer": (
            "This email, the selection of invoices, and the calculation of "
            "the amount were generated automatically by an AI assistant "
            "(Anthropic Claude). If you notice anything incorrect or unclear, "
            "please contact Vilius directly."
        ),
    },
}


def to_address_string(value: str | list[str] | None) -> str | None:
    """tenant_email config/properties.yaml gali būti vienas adresas arba sąrašas
    (pvz. keli bendranuomininkiai) - sujungia į vieną To: eilutę."""
    if isinstance(value, list):
        return ", ".join(value)
    return value


def friendly_label(raw_label: str, language: str) -> str:
    for needle, friendly in FRIENDLY_LABELS.get(language, FRIENDLY_LABELS["lt"]):
        if needle.lower() in raw_label.lower():
            return friendly
    return raw_label


def parse_amount_file(path: Path) -> tuple[list[tuple[str, float]], float]:
    items: list[tuple[str, float]] = []
    total: float | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("="):
            continue
        m_item = re.match(r"^(.+?)\s*\[.*?\]\s*:\s*([\d,]+)$", line) or re.match(r"^(.+?):\s*([\d,]+)$", line)
        if m_item:
            label, amount = m_item.group(1), m_item.group(2)
            items.append((label, float(amount.replace(",", "."))))
            continue
        m_total = re.match(r"^([\d,]+)\s*€$", line)
        if m_total:
            total = float(m_total.group(1).replace(",", "."))
    if total is None:
        raise ValueError(f"{path}: nepavyko rasti galutinės sumos (eilutė 'X,XX €')")
    return items, total


def build_message(prop_cfg: dict, month_name: str, items: list[tuple[str, float]],
                   total: float, pdf_files: list[Path], recipient: str, subject: str,
                   cc: str | None) -> EmailMessage:
    language = prop_cfg.get("language", "lt")
    t = TEXT[language]
    amount_fmt = (lambda a: f"{a:.2f} €") if language != "lt" else (lambda a: f"{a:.2f}".replace(".", ",") + " €")

    grouped: dict[str, float] = {}
    for label, amount in items:
        grouped[friendly_label(label, language)] = grouped.get(friendly_label(label, language), 0.0) + amount

    lines = [
        t["greeting"],
        "",
        t["intro"].format(month=month_name, address=prop_cfg["address"]),
        "",
        t["summary_label"],
    ]
    for label, amount in grouped.items():
        lines.append(f"  {label}: {amount_fmt(amount)}")
    lines.append("")
    lines.append(f"{t['total_label']} {amount_fmt(total)}")
    lines.append("")
    lines.append(t["attached"])
    lines.append("")
    lines.append(t["closing"])
    lines.append("")
    lines.append("---")
    lines.append(t["disclaimer"])

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["To"] = recipient
    if cc and cc != recipient:
        msg["Cc"] = cc
    msg.set_content("\n".join(lines), charset="utf-8")

    for pdf_path in pdf_files:
        msg.add_attachment(
            pdf_path.read_bytes(),
            maintype="application",
            subtype="pdf",
            filename=pdf_path.name,
        )
    return msg


def send(prop_id: str, prop_cfg: dict, config: dict, secrets: dict,
         year: int, month: int, test_mode: bool) -> None:
    folder = target_folder(prop_cfg, year, month, test_mode)
    amount_path = folder / "amount.txt"
    if not amount_path.exists():
        log.error("[%s] nerastas %s - pirma paleisti compute_total.py", prop_id, amount_path)
        return

    items, total = parse_amount_file(amount_path)
    pdf_files = sorted(folder.glob("*.pdf"))
    language = prop_cfg.get("language", "lt")

    # Laiško TEKSTAS visada atitinkamo buto kalba (net ir testuojant, kad naudotojas
    # galėtų peržiūrėti tiksliai tokį laišką, koks bus siunčiamas produkcijoje).
    body_month_name = MONTH_NAMES[language][month - 1]

    if test_mode:
        # Testinio laiško TEMA (ne tekstas) visada LT, nes ją mato pats naudotojas.
        subject_month_name = MONTH_NAMES["lt"][month - 1]
        subject = config["test_mode"]["subject_template"].format(
            subject_name=prop_cfg["subject_name"], month_name=subject_month_name)
        recipient = config["test_mode"]["recipient"]
        cc = None
    else:
        recipient = to_address_string(prop_cfg.get("tenant_email"))
        if not recipient:
            log.error("[%s] tenant_email dar nenustatytas config/properties.yaml - "
                      "negalima siųsti PRODUKCINIO laiško", prop_id)
            return
        if language == "en":
            subject = f"Utility bills for {body_month_name} - {prop_cfg['address']}"
        else:
            subject = f"Komunaliniai mokesčiai už {body_month_name} mėn. - {prop_cfg['address']}"
        cc = secrets["GMAIL_ADDRESS"]

    msg = build_message(prop_cfg, body_month_name, items, total, pdf_files, recipient, subject, cc)
    msg["From"] = secrets["GMAIL_ADDRESS"]

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(secrets["GMAIL_ADDRESS"], secrets["GMAIL_APP_PASSWORD"])
        smtp.send_message(msg)

    log.info("[%s] laiškas išsiųstas %s (tema: %s, %d priedai, suma %.2f €)",
              prop_id, recipient, subject, len(pdf_files), total)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--month", type=int, required=True)
    parser.add_argument("--property", dest="property_id")
    parser.add_argument("--test", dest="test_mode", action="store_true", default=None)
    parser.add_argument("--no-test", dest="test_mode", action="store_false")
    parser.add_argument("--yes", action="store_true", help="nepaklausti patvirtinimo prieš siunčiant produkciniu režimu")
    args = parser.parse_args()

    config = load_config()
    secrets = load_secrets()
    test_mode = args.test_mode if args.test_mode is not None else config["test_mode"]["enabled"]

    if not test_mode and not args.yes:
        confirm = input("DĖMESIO: siunčiama PRODUKCINIU režimu tikram nuomininkui. Tęsti? [y/N] ")
        if confirm.strip().lower() != "y":
            log.info("Atšaukta naudotojo.")
            return

    prop_ids = [args.property_id] if args.property_id else list(config["properties"].keys())
    for prop_id in prop_ids:
        send(prop_id, config["properties"][prop_id], config, secrets, args.year, args.month, test_mode)


if __name__ == "__main__":
    main()
