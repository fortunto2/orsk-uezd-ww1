#!/usr/bin/env python3
"""Полная выгрузка по уезду, а не по волостям.

Первая версия свода собиралась по названию ВОЛОСТИ — и это оказалось ошибкой,
измеренной задним числом. Волость писари перевирали сильнее, чем деревню: наша
Салиховская встречается как «Самоковская», «Самохинская», «Саликатская»,
«Шинетуеы-Серганская», а у части записей её нет вовсе. Выгрузка по двум
волостям дала 1368 записей; в уезде их 20 744.

Отсюда правило, стоившее нам полной переделки: **фильтровать по губернии и
уезду, название волости не использовать вообще**.

Порог выдачи — 10 000 записей на запрос (from+size), поэтому выгрузка идёт по
одному типу документа за раз: каждый тип помещается в лимит.

    python3 scripts/gwar-uezd.py            # → /tmp/gwar_uezd_<тип>.json
"""
import json
import subprocess
import sys
import time
from pathlib import Path

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 Chrome/152")
ENT = ["chelovek_donesenie", "chelovek_gospital", "chelovek_zahoronenie",
       "chelovek_plen", "chelovek_nagrazhdenie", "chelovek_predstavlenie",
       "chelovek_nagradnaya_kartochka", "commander", "person",
       "chelovek_posluzhnoi_spisok", "chelovek_uchetnaya_kartochka"]
OUT = Path("/tmp")
GUB, UEZD = "Оренбургская", "Орский"
PAGE = 500
LIMIT = 10000        # жёсткий потолок портала на from+size


def post(body):
    p = subprocess.run(
        ["curl", "-sSk", "--max-time", "180", "-A", UA,
         "-H", "Content-Type: application/json",
         "-H", "Referer: https://gwar.mil.ru/heroes/", "-X", "POST",
         "https://gwar.mil.ru/gt_data/?builder=Heroes",
         "--data-binary", json.dumps(body, ensure_ascii=False)],
        capture_output=True, check=False)
    try:
        return json.loads(p.stdout.decode())
    except Exception:
        return None


def sweep(entity):
    rows, frm, total = [], 0, None
    while True:
        body = {"indices": ["gwar"], "entities": [entity],
                "queryFields": {"birth_place_gubernia": GUB, "birth_place_uezd": UEZD},
                "filterFields": {}, "from": frm, "size": str(PAGE),
                "builderType": "Heroes"}
        d = post(body)
        if d is None:
            print(f"    ! ответ не разобран на from={frm}, пауза и повтор")
            time.sleep(5)
            d = post(body)
            if d is None:
                break
        hits = d.get("hits", {})
        total = hits.get("total", total)
        got = hits.get("hits", [])
        rows += [h["_source"] for h in got]
        frm += len(got)
        if not got or frm >= (total or 0) or frm >= LIMIT:
            break
        time.sleep(0.4)
    return total, rows


def main():
    grand = 0
    for ent in ENT:
        total, rows = sweep(ent)
        grand += len(rows)
        flag = "  ⚠️ УПЁРЛИСЬ В ПОТОЛОК" if total and total > LIMIT else ""
        print(f"  {ent:32} всего {total or 0:>6} · собрано {len(rows):>6}{flag}")
        if rows:
            path = OUT / f"gwar_uezd_{ent}.json"
            json.dump(rows, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"\nитого собрано {grand} записей")


if __name__ == "__main__":
    sys.exit(main())
