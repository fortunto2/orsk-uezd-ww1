#!/usr/bin/env python3
"""Добор записей, упирающихся в потолок выдачи 10 000.

Портал отдаёт максимум 10 000 записей на запрос (from+size). Госпитальных
карточек по Орскому уезду больше, поэтому запрос дробится по причине выбытия:
каждая часть в потолок помещается. Проверка честности дробления — сумма частей
против общего числа; если она меньше, это сказано вслух, а не замолчано.

    python3 scripts/gwar-uezd-dobor.py      # → /tmp/gwar_dobor_<...>.json
"""
import json
import subprocess
import sys
import time

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 Chrome/152")
GUB, UEZD = "Оренбургская", "Орский"
PRICHINY = ["ранен/контужен", "болен", "пропал без вести", "убит",
            "оставлен на поле боя", "попал в плен", "умер от ран", "умер"]
ENTITY = "chelovek_gospital"
LIMIT, PAGE = 10000, 500


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


def sweep(**extra):
    qf = {"birth_place_gubernia": GUB, "birth_place_uezd": UEZD}
    qf.update(extra)
    rows, frm, total = [], 0, None
    while True:
        d = post({"indices": ["gwar"], "entities": [ENTITY], "queryFields": qf,
                  "filterFields": {}, "from": frm, "size": str(PAGE),
                  "builderType": "Heroes"})
        if d is None:
            time.sleep(5)
            d = post({"indices": ["gwar"], "entities": [ENTITY], "queryFields": qf,
                      "filterFields": {}, "from": frm, "size": str(PAGE),
                      "builderType": "Heroes"})
            if d is None:
                break
        h = d.get("hits", {})
        total = h.get("total", total)
        got = h.get("hits", [])
        rows += [x["_source"] for x in got]
        frm += len(got)
        if not got or frm >= (total or 0) or frm >= LIMIT:
            break
        time.sleep(0.4)
    return total, rows


def main():
    whole, _ = sweep()
    print(f"госпитальных по уезду всего: {whole}")
    # ⚠️ Поле запроса называется vibitie_prichina. Первая версия дробила по
    # data_vibitiya — оно принимает значение молча и возвращает 11 215 на
    # любое, то есть не фильтрует вовсе. Все восемь частей вышли одинаковыми,
    # и добор дал ровно те же 10 000. Фильтр, который «работает» и всегда
    # возвращает одно и то же, — это отсутствующий фильтр.
    seen, out = set(), []
    for pr in PRICHINY:
        total, rows = sweep(vibitie_prichina=pr)
        fresh = [r for r in rows if str(r.get("id")) not in seen]
        for r in fresh:
            seen.add(str(r.get("id")))
        cap = "  ⚠️ и эта часть в потолке" if total and total > LIMIT else ""
        print(f"  {pr:22} всего {total or 0:>6} · собрано {len(rows):>5} · новых {len(fresh):>5}{cap}")
        out += fresh
        time.sleep(0.5)
    json.dump(out, open("/tmp/gwar_dobor_gospital.json", "w", encoding="utf-8"),
              ensure_ascii=False)
    print(f"\nсобрано дроблением: {len(out)} из {whole}")
    if whole and len(out) < whole:
        print(f"⚠️ НЕ ДОБРАНО {whole - len(out)} записей — дробления по причине "
              f"выбытия не хватает, нужен второй признак")


if __name__ == "__main__":
    sys.exit(main())
