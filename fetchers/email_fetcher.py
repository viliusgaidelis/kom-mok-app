"""IMAP sąskaitų atsisiuntimas iš Gmail pašto.

Tiekėjai aprašyti `config/properties.yaml` -> `email_providers` (žr. ten
komentarus dėl kiekvieno realaus Lietuvos tiekėjo pavyzdžio - vv.lt, ignitis.lt,
adminsta.lt, manobustas.lt, vasa.lt). Du tiekėjų tipai turi specialią logiką:

  - `property_rule: content` (pvz. vandens/elektros tiekėjai, kai siuntėjas
    bendras keliems butams) - butas atpažįstamas pagal PDF TURINĮ
    (žr. `common.match_property_by_content`), ne pagal siuntėją.
  - `flat_target: "vasa_durnynas"` - nereguliariai (pvz. kartą per metus)
    ateinančioms sąskaitoms, kurios NEPRIKLAUSO konkrečiam mėnesiui/butui.
    Kiekvienas rastas PDF dedamas į bendrą aplanką (NE į <YYYY-MM> <Butas>),
    ir apie radinį IŠ KARTO išsiunčiamas priminimo laiškas naudotojui su
    PDF priedu, nes tokią sumą dažnai reikia rankomis padalinti per mėnesius.

Šildymas (daugelyje Lietuvos savitarnos sistemų) laiškais NEATEINA — tam
skirtas atskiras Playwright scraperis, žr. `fetchers/portals/chc_heating.py`.

Naudojimas:
    python fetchers/email_fetcher.py --year 2026 --month 9
    python fetchers/email_fetcher.py --year 2026 --month 9 --no-test   (produkciniai aplankai)
"""

from __future__ import annotations

import argparse
import email
import imaplib
from email.header import decode_header
from email.utils import parseaddr

from common import (
    already_have,
    extract_pdf_text,
    get_logger,
    is_ignored_content,
    load_config,
    load_secrets,
    match_property_by_content,
    send_notification,
    target_folder,
    unsorted_folder,
    vasa_durnynas_folder,
)

log = get_logger("email_fetcher")


def decode_str(s: str | None) -> str:
    if not s:
        return ""
    parts = decode_header(s)
    out = ""
    for text, enc in parts:
        out += text.decode(enc or "utf-8", errors="replace") if isinstance(text, bytes) else text
    return out


def month_add(year: int, month: int, delta: int) -> tuple[int, int]:
    total = (year * 12 + (month - 1)) + delta
    return total // 12, total % 12 + 1


def imap_date(year: int, month: int, day: int = 1) -> str:
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{day:02d}-{months[month - 1]}-{year}"


def connect(secrets: dict) -> imaplib.IMAP4_SSL:
    addr = secrets["GMAIL_ADDRESS"]
    password = secrets["GMAIL_APP_PASSWORD"]
    M = imaplib.IMAP4_SSL("imap.gmail.com")
    M.login(addr, password)
    M.select("INBOX", readonly=True)
    return M


def iter_pdf_attachments(msg: email.message.Message):
    for part in msg.walk():
        filename = part.get_filename()
        if not filename:
            continue
        filename = decode_str(filename)
        if not filename.lower().endswith(".pdf"):
            continue
        data = part.get_payload(decode=True)
        if not data:
            continue
        yield filename, data


def resolve_property(rule: str, pdf_text: str, config: dict) -> str | None:
    if rule.startswith("fixed:"):
        return rule.split(":", 1)[1]
    if rule == "content":
        return match_property_by_content(pdf_text, config)
    raise ValueError(f"Nežinoma property_rule: {rule}")


def process_provider(M, key: str, provider_cfg: dict, config: dict, year: int, month: int,
                      test_mode: bool, window_since: str, window_before: str) -> None:
    criteria = []
    if "sender_domain" in provider_cfg:
        criteria.append(f'FROM "{provider_cfg["sender_domain"]}"')
    elif "sender_address" in provider_cfg:
        criteria.append(f'FROM "{provider_cfg["sender_address"]}"')
    criteria.append(f'SINCE "{window_since}"')
    criteria.append(f'BEFORE "{window_before}"')
    query = "(" + " ".join(criteria) + ")"

    typ, data = M.search(None, query)
    if typ != "OK":
        log.warning("[%s] IMAP paieška nepavyko: %s", key, query)
        return
    ids = data[0].split()
    log.info("[%s] rasta %d laiškų lange %s..%s", key, len(ids), window_since, window_before)

    subject_filter = provider_cfg.get("subject_contains", "").lower()

    for msg_id in ids:
        typ, msg_data = M.fetch(msg_id, "(RFC822)")
        if typ != "OK" or not msg_data or msg_data[0] is None:
            continue
        msg = email.message_from_bytes(msg_data[0][1])
        subject = decode_str(msg.get("Subject"))
        if subject_filter and subject_filter not in subject.lower():
            continue

        _, from_addr = parseaddr(msg.get("From", ""))
        found_any = False
        for filename, pdf_bytes in iter_pdf_attachments(msg):
            found_any = True
            try:
                pdf_text = extract_pdf_text(pdf_bytes)
            except Exception as exc:  # PDF gali būti užrakintas slaptažodžiu ir pan.
                pdf_text = ""
                log.warning("[%s] nepavyko nuskaityti PDF teksto (%s): %s", key, filename, exc)

            if pdf_text and is_ignored_content(pdf_text, config):
                log.info(
                    "[%s] %s atitinka žinomą KITĄ objektą (ignore_markers) - "
                    "praleidžiama, nekopijuojama", key, filename,
                )
                continue

            prop_id = resolve_property(provider_cfg["property_rule"], pdf_text, config)

            if prop_id is None:
                folder = unsorted_folder(config, year, month, test_mode)
                log.warning(
                    "[%s] NEPAVYKO nustatyti buto failui %s (laiškas nuo %s, tema: %s) "
                    "-> dedama į %s peržiūrai",
                    key, filename, from_addr, subject, folder,
                )
            else:
                folder = target_folder(config["properties"][prop_id], year, month, test_mode)

            if already_have(folder, filename):
                log.info("[%s] jau yra %s, praleidžiama", key, folder / filename)
                continue

            (folder / filename).write_bytes(pdf_bytes)
            log.info("[%s] išsaugota %s", key, folder / filename)

        if not found_any:
            log.info("[%s] laiškas be PDF priedo (tema: %s), praleista", key, subject)


def process_vasa(M, provider_cfg: dict, config: dict, window_since: str) -> list:
    """VASA - žr. modulio docstring. Grąžina sąrašą NAUJAI išsaugotų failų.

    Naudoja PLATŲ (ne mėnesio) langą, nes VASA siunčia nereguliariai ir jos
    data nesutampa su jokiu konkrečiu --month ciklu - apsauga nuo dubliavimo
    yra already_have() patikra pagal failo vardą, ne laiko langas."""
    key = "waste_vasa"
    criteria = [f'FROM "{provider_cfg["sender_domain"]}"', f'SINCE "{window_since}"']
    typ, data = M.search(None, "(" + " ".join(criteria) + ")")
    if typ != "OK":
        log.warning("[%s] IMAP paieška nepavyko", key)
        return []
    ids = data[0].split()
    log.info("[%s] rasta %d laiškų nuo %s", key, len(ids), window_since)

    folder = vasa_durnynas_folder(config)
    new_files = []
    for msg_id in ids:
        typ, msg_data = M.fetch(msg_id, "(RFC822)")
        if typ != "OK" or not msg_data or msg_data[0] is None:
            continue
        msg = email.message_from_bytes(msg_data[0][1])
        subject = decode_str(msg.get("Subject"))
        for filename, pdf_bytes in iter_pdf_attachments(msg):
            if already_have(folder, filename):
                log.info("[%s] jau yra %s, praleidžiama", key, folder / filename)
                continue
            (folder / filename).write_bytes(pdf_bytes)
            log.warning("[%s] NAUJA VASA SĄSKAITA rasta ir išsaugota: %s (tema: %s)",
                        key, folder / filename, subject)
            new_files.append(folder / filename)
    return new_files


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

    since_year, since_month = month_add(args.year, args.month, 1)
    before_year, before_month = month_add(args.year, args.month, 3)
    window_since = imap_date(since_year, since_month)
    window_before = imap_date(before_year, before_month)
    vasa_window_since = imap_date(args.year - 1, 1)  # VASA - platus langas, žr. process_vasa()

    M = connect(secrets)
    try:
        new_vasa_files: list = []
        for key, provider_cfg in config["email_providers"].items():
            if provider_cfg.get("flat_target") == "vasa_durnynas":
                new_vasa_files.extend(process_vasa(M, provider_cfg, config, vasa_window_since))
                continue
            process_provider(M, key, provider_cfg, config, args.year, args.month,
                              test_mode, window_since, window_before)
    finally:
        M.logout()

    if new_vasa_files:
        log.warning("Rasta %d NAUJŲ VASA sąskaitų - siunčiamas priminimo laiškas", len(new_vasa_files))
        body = (
            "Rasta nauja(-os) VASA (šiukšlių išvežimo) sąskaita(-os), pridėta(-os) prie šio laiško:\n\n"
            + "\n".join(f"  - {p.name}" for p in new_vasa_files)
            + "\n\nFailai taip pat įdėti į:\n  " + str(vasa_durnynas_folder(config))
            + "\n\nPRIMINIMAS: sumą reikia PADALINTI IŠ 12 ir atnaujinti abiejų butų "
              "'Waste collection fee.txt' / 'atliekų tvarkymas.txt' failus kitam ciklui."
        )
        send_notification(
            secrets,
            subject="⚠ Rasta nauja VASA sąskaita - reikia rankinio atnaujinimo",
            body=body,
            attachments=new_vasa_files,
        )


if __name__ == "__main__":
    main()
