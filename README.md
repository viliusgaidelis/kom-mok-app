# kom-mok-app

Automatizuoja asmeninio butų nuomos verslo komunalinių mokesčių (**kom**unaliniai
**mok**esčiai) ciklą: suranda ir atsisiunčia sąskaitas iš el. pašto ir tiekėjų
savitarnos portalų, surūšiuoja jas pagal butą, paskaičiuoja bendrą mokėtiną
sumą ir išsiunčia nuomininkui laišką su suma ir prisegtomis sąskaitomis.

Sukurta: Vilius Gaidelis, su [Claude Code](https://claude.com/claude-code)
pagalba. Šis repo yra nuvalyta (be realių asmens duomenų) versija realiai
veikiančios sistemos — pavyzdiniai duomenys `config/properties.yaml` faile
skirti parodyti struktūrą; norint naudoti, juos reikia pakeisti savais.

## Kaip tai veikia

```
fetchers/email_fetcher.py       - IMAP: suranda ir atsisiunčia PDF sąskaitas iš el. pašto
fetchers/portals/chc_heating.py - Playwright: atsisiunčia sąskaitas iš tiekėjo savitarnos portalo
processing/compute_total.py     - paskaičiuoja bendrą mokėtiną sumą iš atsisiųstų PDF
mailer/send_summary.py          - išsiunčia nuomininkui laišką su suma ir priedais
scripts/main.py                 - sujungia visus keturis žingsnius į vieną komandą
```

Pagrindinis principas — **pusiau automatinis** veikimas: sistema suranda,
surūšiuoja ir paskaičiuoja sumą, bet VISADA parodo rezultatą peržiūrai prieš
siunčiant bet kokį laišką. Laiškų siuntimas reikalauja aiškaus patvirtinimo
(`--send` + y/N patvirtinimas, arba `--yes` jau žinant, kad suma teisinga).

Realūs integracijų pavyzdžiai (`config/properties.yaml` → `email_providers`)
parodo, kaip atpažinti ir apdoroti kelių Lietuvos komunalinių paslaugų
tiekėjų (elektros, vandens, šildymo, atliekų, pastato administravimo)
sąskaitas, įskaitant tokius niuansus kaip:
- buto atpažinimas pagal PDF TURINĮ, kai keli butai turi bendrą tiekėją;
- tik einamojo mėnesio sumos naudojimas (ne "mokėti iš viso" su sena skola);
- nereguliarių (pvz. metinių) sąskaitų atskyrimas nuo mėnesinio ciklo.

## Paleidimas

```bash
pip install -r requirements.txt
python -m playwright install chromium   # tik jei naudosite portalo scraperį

cp secrets.env.example secrets.env      # ir užpildykite tikromis reikšmėmis
# config/properties.yaml - pakeiskite pavyzdinius adresus/kodus/el. paštus savais

python scripts/main.py run --year 2026 --month 9 --test           # peržiūra, nieko nesiunčia
python scripts/main.py run --year 2026 --month 9 --test --send    # + testinis laiškas sau
python scripts/main.py run --year 2026 --month 9 --no-test --send # TIKRAS laiškas nuomininkui
```

## Licencija

MIT — žr. [LICENSE](./LICENSE).
