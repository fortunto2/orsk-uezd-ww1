#!/usr/bin/env python3
"""Привязка написаний из свода к современным населённым пунктам (OSM).

Гнёзда отвечают на вопрос «похожи ли две строки». Здесь вопрос сильнее:
**какому реальному селу соответствует запись**. Ответ даёт не сравнение
написаний между собой, а внешний справочник — reference/osm-places.csv.

Два уровня доверия, и это принципиально:

  строгое  — нормализованные формы совпали точно (регистр, ё, дефис, приставка,
             окончание). Такая привязка не зависит ни от какого порога и потому
             годится как независимый арбитр при проверке качества гнёзд.
  нечёткое — совпадение по фонетическому скелету с мерой сходства. Это
             гипотеза; мера выписана, слабые видны.

    python3 scripts/reconcile.py       # → derived/places-geo.csv

⚠️ reference/osm-places.csv — производное от OpenStreetMap, лицензия ODbL.
"""
import csv
import io
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze import SRC, block_key, skeleton, strip_prefix, cluster  # noqa: E402

if len(sys.argv) > 1 and sys.argv[1].endswith(".csv"):
    SRC = Path(sys.argv[1])

import math

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "reference" / "gazetteer.csv"
OUT = ROOT / "derived" / ("v2" if "-v2" in SRC.name else "") / "places-geo.csv"
FUZZY_MIN = 0.84
# Радиус, в котором одноимённое село считается тем же самым. Волость — это
# два-три десятка вёрст; всё, что дальше, скорее тёзка. Проверено на своих:
# «Бикбердина-Идяш» привязывалась к Идяшу в 60 км, «Куватова» — к Куватово в
# 75 км, хотя оба выселка стоят вплотную к Бикбердино.
SAME_PLACE_KM = 35


# Приставка — часть имени, а не украшение: «Ново-Михайловский» и «Михайловка»
# разные сёла. Поэтому она не отбрасывается, а приводится к метке и должна
# совпасть с обеих сторон. Первая версия её отрезала и дала ровно эту ложную
# привязку.
PREFIX_TAG = {"верхне": "в", "верхний": "в", "верхняя": "в", "верхн": "в",
              "нижне": "н", "нижний": "н", "нижняя": "н", "нижн": "н",
              "ново": "нов", "новый": "нов", "новая": "нов",
              "ст": "стар", "старо": "стар", "старый": "стар",
              "мало": "мал", "малый": "мал", "малая": "мал",
              "больше": "бол", "большой": "бол", "большая": "бол"}


def norm_strict(name):
    """(метка приставки, основа) без фонетики — только вольности орфографии.

    Регистр, ё/е, мягкий знак, дефисы, точки и падежное окончание
    («Бикбердина» и «Бикбердино» → «бикберд»). Никаких свёрток согласных:
    строгий уровень не должен зависеть от догадок о звучании.
    """
    pref, rest = strip_prefix(name)
    s = rest.lower().replace("ё", "е")
    for ch in "ьъ-. ":
        s = s.replace(ch, "")
    for suf in ("ская", "ский", "ское", "ова", "ово", "ева", "ево", "ина",
                "ино", "ыно", "ка", "о", "а", "ы", "и", "е"):
        if s.endswith(suf) and len(s) - len(suf) >= 4:
            return PREFIX_TAG.get(pref, pref), s[: -len(suf)]
    return PREFIX_TAG.get(pref, pref), s


def load_reference():
    rows = list(csv.DictReader(io.open(REF, encoding="utf-8")))
    strict, fuzzy = defaultdict(list), defaultdict(list)
    for r in rows:
        for field in ("название", "старое_название", "альт_название"):
            nm = r[field].strip()
            if not nm:
                continue
            strict[norm_strict(nm)].append((r, nm, field))
            fuzzy[block_key(skeleton(strip_prefix(nm)[1]))].append((r, nm, field))
    return rows, strict, fuzzy


def km(lat1, lon1, lat2, lon2):
    return 111 * math.hypot(lat1 - lat2, (lon1 - lon2) * math.cos(math.radians(lat1)))


def anchor(rows, volost):
    """Где примерно стоит эта волость — по уже привязанным её же селениям.

    Одно название носят десятки деревень губернии, и сходство строк их не
    различает в принципе. Различает география: если волость уже привязана
    к нескольким точкам, новое селение той же волости должно лежать рядом.
    """
    pts = [(float(r["широта"]), float(r["долгота"])) for r in rows
           if r.get("волость") == volost and r.get("широта")]
    if len(pts) < 3:
        return None
    lat = sorted(p[0] for p in pts)[len(pts) // 2]
    lon = sorted(p[1] for p in pts)[len(pts) // 2]
    return lat, lon


def resolve(variant, strict, fuzzy, anchors, vol_of):
    """Привязать одно написание. Возвращает (статус, мера, объект, поле)."""
    rank = {"city": 0, "town": 1, "village": 2, "hamlet": 3,
            "isolated_dwelling": 4, "locality": 5}
    a = anchors.get(vol_of.get(variant))
    hit = strict.get(norm_strict(variant))
    if hit:
        if a and len(hit) > 1:
            # Сначала география, потом размер: одноимённых сёл в губернии
            # десятки, и ближайшее к своей волости почти всегда нужное.
            r, nm, field = min(hit, key=lambda h: (
                km(a[0], a[1], float(h[0]["широта"]), float(h[0]["долгота"])),
                rank.get(h[0]["тип"], 9)))
        else:
            r, nm, field = min(hit, key=lambda h: rank.get(h[0]["тип"], 9))
        if a:
            d = km(a[0], a[1], float(r["широта"]), float(r["долгота"]))
            if d > SAME_PLACE_KM:
                return "далеко", f"{d:.0f} км", r, field
        return "строгое", "1.00", r, field
    sk = skeleton(strip_prefix(variant)[1])
    best, best_r, best_f = None, 0.0, "имя"
    for r, nm, field in fuzzy.get(block_key(sk), []):
        ratio = SequenceMatcher(None, sk, skeleton(strip_prefix(nm)[1])).ratio()
        if ratio < FUZZY_MIN:
            continue
        if a:
            d = km(a[0], a[1], float(r["широта"]), float(r["долгота"]))
            if d > SAME_PLACE_KM:
                continue
        if ratio > best_r:
            best, best_r, best_f = r, ratio, field
    if best:
        return "нечёткое", f"{best_r:.2f}", best, best_f
    return "нет", "", None, ""


def main():
    if not REF.exists():
        sys.exit("Нет reference/gazetteer.csv — сначала scripts/gazetteer.py")
    data = list(csv.DictReader(io.open(SRC, encoding="utf-8")))
    places = [r["населённый_пункт"].strip() for r in data if r["населённый_пункт"].strip()]
    pcount = Counter(places)
    nests = cluster(set(places), pcount)
    ref_rows, strict, fuzzy = load_reference()
    print(f"{len(ref_rows)} объектов справочника · {len(nests)} гнёзд свода\n")

    vol_count = defaultdict(Counter)
    for r in data:
        p = r["населённый_пункт"].strip()
        if p and r.get("волость"):
            vol_count[p][r["волость"]] += 1
    vol_of = {p: c.most_common(1)[0][0] for p, c in vol_count.items()}

    order = [(head, v, r) for head, members in
             sorted(nests.items(), key=lambda kv: -sum(pcount[x] for x, _ in kv[1]))
             for v, _ in sorted(members, key=lambda m: -pcount[m[0]])
             for r in [sum(pcount[x] for x, _ in members)]]

    # Первый проход — без географии: он и даёт якоря.
    anchors = {}
    first = {v: resolve(v, strict, fuzzy, anchors, vol_of) for _, v, _ in order}
    by_vol = defaultdict(list)
    for v, (st, _, r, _) in first.items():
        if st == "строгое" and vol_of.get(v):
            by_vol[vol_of[v]].append((float(r["широта"]), float(r["долгота"])))
    for vol, pts in by_vol.items():
        if len(pts) >= 3:
            lat = sorted(p[0] for p in pts)[len(pts) // 2]
            lon = sorted(p[1] for p in pts)[len(pts) // 2]
            anchors[vol] = (lat, lon)
    print(f"  якорей волостей: {len(anchors)} (из {len(by_vol)} волостей со строгими привязками)")

    # Второй проход — с географией: тёзки за пределами волости отбраковываются.
    out, stat = [], Counter()
    for head, variant, recs in order:
        st, meas, r, field = resolve(variant, strict, fuzzy, anchors, vol_of)
        stat[st] += 1
        if r is None:
            out.append([head, variant, pcount[variant], recs, "нет", "", "", "", "", "", "", "", ""])
        else:
            out.append([head, variant, pcount[variant], recs, st, meas,
                        r["название"], r["тип"], r["широта"], r["долгота"],
                        r["id"], r["источник"],
                        "переименование" if field != "название" else "имя"])

    with io.open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["гнездо", "написание", "записей", "записей_в_гнезде", "привязка",
                    "сходство", "осм_название", "тип", "широта", "долгота", "id",
                    "источник", "совпало_по"])
        w.writerows(out)

    total = sum(stat.values())
    print(f"  {OUT.relative_to(ROOT)} — {total} написаний")
    for k in ("строгое", "нечёткое", "далеко", "нет"):
        print(f"    {k:9} {stat[k]:5}  {stat[k]*100/total:4.1f} %")
    got = sum(r[2] for r in out if r[4] in ("строгое", "нечёткое"))
    print(f"\n  записей свода с координатами: {got} из {len(places)} "
          f"({got*100//len(places)} %)")
    if stat["далеко"]:
        print(f"  ⚠️ {stat['далеко']} написаний отброшено как тёзки за пределами "
              f"своей волости (дальше {SAME_PLACE_KM} км от её якоря)")


if __name__ == "__main__":
    sys.exit(main())
