"""Bendros pagalbinės funkcijos: secrets/config įkėlimas, aplankų keliai,
PDF teksto nuskaitymas butui atpažinti. Naudojama email_fetcher.py ir vėliau
portals/*.py scraperiuose."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[1]          # .../kom-mok-app
SECRETS_PATH = REPO_ROOT / "secrets.env"                  # .gitignore'inta, NIEKADA necommitinti
CONFIG_PATH = REPO_ROOT / "config" / "properties.yaml"


def load_secrets() -> dict:
    if not SECRETS_PATH.exists():
        raise FileNotFoundError(
            f"Nerastas {SECRETS_PATH}. Nukopijuokite config/secrets.env.example į "
            f"{SECRETS_PATH} ir įrašykite tikrus duomenis."
        )
    return dict(dotenv_values(SECRETS_PATH))


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    log_dir = REPO_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    fh = logging.FileHandler(log_dir / f"{name}.log", encoding="utf-8")
    sh = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    fh.setFormatter(fmt)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def target_folder(prop_cfg: dict, year: int, month: int, test_mode: bool) -> Path:
    pattern = prop_cfg["test_folder_pattern"] if test_mode else prop_cfg["folder_pattern"]
    folder_name = pattern.format(year=year, month=month)
    folder = Path(prop_cfg["invoices_root"]) / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def unsorted_folder(config: dict, year: int, month: int, test_mode: bool) -> Path:
    """Aplankas failams, kurių buto nepavyko vienareikšmiškai nustatyti."""
    any_prop = next(iter(config["properties"].values()))
    root = Path(any_prop["invoices_root"])
    suffix = "-TEST" if test_mode else ""
    folder = root / f"{year}-{month:02d} _NEPATVIRTINTA{suffix}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def already_have(folder: Path, filename: str) -> bool:
    return (folder / filename).exists()


def extract_pdf_text(pdf_bytes: bytes) -> str:
    import io

    import pdfplumber

    text_parts = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages[:2]:  # pakanka pirmų puslapių adresui/kodui rasti
            text = page.extract_text() or ""
            text_parts.append(text)
    return "\n".join(text_parts)


HEADER_SPLIT_MARKER = "PVM SĄSKAITA FAKTŪRA"


def relevant_text(text: str) -> str:
    """Kai kurių tiekėjų (pvz. Ignitis) PDF viršuje prieš šį žymeklį būna
    naudotojo korespondencijos (siuntimo) adresas, kuris GALI SKIRTIS nuo
    faktinio objekto/paslaugos adreso, nurodyto žemiau, sąskaitos
    detalizacijoje. Butui atpažinti reikia žiūrėti TIK į tekstą PO šio
    žymeklio - patvirtinta su naudotoju 2026-10-15 (žr. NOTES.md).
    Jei žymeklio nėra (pvz. vv.lt sąskaitose), grąžinamas visas tekstas."""
    idx = text.find(HEADER_SPLIT_MARKER)
    return text[idx:] if idx != -1 else text


def is_ignored_content(text: str, config: dict) -> bool:
    """Ar PDF turinys atitinka kurį nors žinomą NE šio projekto objektą
    (žr. config/properties.yaml -> ignore_markers)."""
    text_low = relevant_text(text).lower()
    return any(marker.lower() in text_low for marker in config.get("ignore_markers", []))


def vasa_durnynas_folder(config: dict) -> Path:
    """Bendras (NE per butą/mėnesį) aplankas VASA sąskaitoms - žr. NOTES.md.
    VASA siunčia nereguliariai ir nepriklausomai nuo konkretaus buto/mėnesio
    ciklo, tad jos sudedamos į vieną aplanką peržiūrai, o ne į <YYYY-MM> <Butas>."""
    any_prop = next(iter(config["properties"].values()))
    folder = Path(any_prop["invoices_root"]) / config.get("vasa_dirname", "VASA-durnynas")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def send_notification(secrets: dict, subject: str, body: str, attachments: list[Path] | None = None) -> None:
    import smtplib
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = secrets["GMAIL_ADDRESS"]
    msg["To"] = secrets["GMAIL_ADDRESS"]
    msg.set_content(body, charset="utf-8")
    for path in attachments or []:
        msg.add_attachment(path.read_bytes(), maintype="application", subtype="pdf", filename=path.name)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(secrets["GMAIL_ADDRESS"], secrets["GMAIL_APP_PASSWORD"])
        smtp.send_message(msg)


def match_property_by_content(text: str, config: dict) -> str | None:
    """Grąžina property_id, jei tekste (po antraštės, žr. relevant_text) randamas
    TIK VIENO buto žymeklis. Grąžina None, jei nerasta arba rasti keli (dviprasmiška)."""
    text_low = relevant_text(text).lower()
    matches = []
    for prop_id, prop_cfg in config["properties"].items():
        markers = prop_cfg.get("address_markers", [])
        if any(marker.lower() in text_low for marker in markers):
            matches.append(prop_id)
    if len(matches) == 1:
        return matches[0]
    return None
