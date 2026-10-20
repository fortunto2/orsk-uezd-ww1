#!/usr/bin/env python3
"""Сборка свода из выгрузок портала: версия 2 — уезд целиком.

Первая версия собиралась по названию волости и покрывала 7 % уезда: волость
писари перевирали сильнее деревни (Салиховская → Самоковская, Самохинская,
Саликатская, Шинетуеы-Серганская), а у части записей её нет вовсе. Здесь
фильтр — губерния и уезд, как и в выгрузке.

Сохраняются поля, которые первая версия выбрасывала:
  birth_place_id   собственный идентификатор места у портала — внешняя замена
                   нашим фонетическим гнёздам;
  event_place, vibitie_mesto   где ранен и куда доставлен;
  box              ящик картотеки РГВИА, по нему заказывают в архиве;
  document_name, document_num, deal_id, page_id  →  ссылка на скан.

    python3 scripts/build-dataset.py /tmp/gwar_uezd_*.json
"""
import csv
import glob
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "pmv-orsk-uezd-1914-1918-v2.csv"

COLS = [
    ("фамилия", "last_name"), ("имя", "first_name"), ("отчество", "middle_name"),
    ("чин", "rank"), ("часть", "military_unit_name"),
    ("причина_выбытия", "vibitie_prichina"),
    ("дата_события", None), ("губерния", "birth_place_gubernia"),
    ("уезд", "birth_place_uezd"), ("волость", "birth_place_volost"),
    ("населённый_пункт", None), ("место_рождения_полностью", "birth_place"),
    ("id_места", "birth_place_id"),
    ("место_события", "event_place"), ("куда_доставлен", "vibitie_mesto"),
    ("архив", "archive_short"), ("фонд", "fund_num"), ("опись", "inventory_num"),
    ("дело", "deal_num"), ("ящик_картотеки", "box"),
    ("тип_документа", "doc_type"), ("название_документа", "document_name"),
    ("номер_документа", "document_num"),
    ("id_записи", "id"), ("тип_записи", "__type"),
    ("ссылка_на_карточку", None), ("ссылка_на_документ", None),
]


PREFIXES = ("д. ", "с. ", "дер. ", "н.п. ", "х. ", "пос. ", "село ", "деревня ")


def place_parts(src):
    """Губерния, уезд, волость и деревня из места рождения.

    ⚠️ Портал отдаёт место ДВУМЯ несовместимыми способами: либо строкой
    birth_place «губ., уезд, вол., д. Имя», либо разложенным по полям
    birth_place_gubernia / _uezd / _volost. Когда заполнено одно, другое
    пусто. Первая версия сборки брала деревню из строки, а волость из поля —
    и получила 10 784 записи с деревней, из которых **ни одна** не имела
    волости. Разбирать надо строку, а поля использовать как запасные.
    """
    bp = (src.get("birth_place") or "").strip()
    gub = src.get("birth_place_gubernia") or ""
    uezd = src.get("birth_place_uezd") or ""
    vol = src.get("birth_place_volost") or ""
    vill = ""
    if bp:
        for seg in [x.strip() for x in bp.split(",")]:
            low = seg.lower()
            if "губ." in low:
                gub = gub or seg
            elif "уезд" in low:
                uezd = uezd or seg
            elif "вол." in low or "волост" in low:
                vol = vol or seg
            elif seg.startswith(PREFIXES) or (seg and not any(
                    w in low for w in ("губ", "уезд", "вол", "обл"))):
                vill = seg
    return gub, uezd, vol, village_name(vill)


def village_name(tail):
    if not tail:
        return ""
    for pref in PREFIXES:
        if tail.startswith(pref):
            return tail[len(pref):].strip()
    if any(w in tail.lower() for w in ("губ.", "уезд", "вол.")):
        return ""
    return tail


def main(patterns):
    files = [f for p in patterns for f in glob.glob(p)]
    if not files:
        sys.exit("UNKNOWN: не найдено ни одного файла выгрузки — собирать нечего")
    rows, seen = [], set()
    for f in files:
        for src in json.load(io.open(f, encoding="utf-8")):
            rid = str(src.get("id"))
            if rid in seen:
                continue
            seen.add(rid)
            rows.append(src)
    print(f"{len(files)} файлов · {len(rows)} уникальных записей")

    out = []
    for s in rows:
        dp = s.get("documents_pages") or {}
        pages = (dp.get("pages_id") or [None])[0]
        deal_id = s.get("deal_id")
        card = f"https://gwar.mil.ru/heroes/{s.get('__type','')}{s.get('id','')}/"
        doc = (f"https://gwar.mil.ru/heroes/document/{deal_id}/?page_id={pages}"
               if deal_id and pages else "")
        r = {}
        for name, key in COLS:
            if key:
                v = s.get(key)
                r[name] = "" if v is None else str(v)
        r["дата_события"] = (s.get("event_date_from") or s.get("vibitie_date_from") or "")[:10]
        gub, uezd, vol, vill = place_parts(s)
        r["губерния"], r["уезд"], r["волость"], r["населённый_пункт"] = gub, uezd, vol, vill
        r["ссылка_на_карточку"] = card
        r["ссылка_на_документ"] = doc
        out.append(r)

    OUT.parent.mkdir(exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[c[0] for c in COLS], lineterminator="\n")
        w.writeheader()
        w.writerows(out)
    withvill = sum(1 for r in out if r["населённый_пункт"])
    withdoc = sum(1 for r in out if r["ссылка_на_документ"])
    withid = sum(1 for r in out if r["id_места"])
    print(f"  {OUT.relative_to(ROOT)}")
    print(f"  с населённым пунктом: {withvill} ({withvill*100//len(out)} %)")
    print(f"  со ссылкой на скан:   {withdoc} ({withdoc*100//len(out)} %)")
    print(f"  с id места у портала: {withid} ({withid*100//len(out)} %)")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["/tmp/gwar_uezd_*.json"]))
