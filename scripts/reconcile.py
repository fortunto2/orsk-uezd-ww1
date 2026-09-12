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

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "reference" / "gazetteer.csv"
OUT = ROOT / "derived" / "places-geo.csv"
FUZZY_MIN = 0.84


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


def main():
    if not REF.exists():
        sys.exit("Нет reference/gazetteer.csv — сначала scripts/gazetteer.py")
    data = list(csv.DictReader(io.open(SRC, encoding="utf-8")))
    places = [r["населённый_пункт"].strip() for r in data if r["населённый_пункт"].strip()]
    pcount = Counter(places)
    nests = cluster(set(places), pcount)
    ref_rows, strict, fuzzy = load_reference()
    print(f"{len(ref_rows)} объектов справочника · {len(nests)} гнёзд свода\n")

    out, stat = [], Counter()
    for head, members in sorted(nests.items(), key=lambda kv: -sum(pcount[v] for v, _ in kv[1])):
        recs = sum(pcount[v] for v, _ in members)
        for variant, _ in sorted(members, key=lambda m: -pcount[m[0]]):
            hit = strict.get(norm_strict(variant))
            if hit:
                # Из нескольких одноимённых сёл берём крупнейшее по типу: село
                # вероятнее хутора, а урочище — последнее.
                rank = {"city": 0, "town": 1, "village": 2, "hamlet": 3,
                        "isolated_dwelling": 4, "locality": 5}
                r, nm, field = min(hit, key=lambda h: rank.get(h[0]["тип"], 9))
                out.append([head, variant, pcount[variant], recs, "строгое", "1.00",
                            r["название"], r["тип"], r["широта"], r["долгота"],
                            r["id"], r["источник"],
                            "переименование" if field != "название" else "имя"])
                stat["строгое"] += 1
                continue
            sk = skeleton(strip_prefix(variant)[1])
            best, best_r, best_f = None, 0.0, "имя"
            for r, nm, field in fuzzy.get(block_key(sk), []):
                ratio = SequenceMatcher(None, sk, skeleton(strip_prefix(nm)[1])).ratio()
                if ratio > best_r:
                    best, best_r, best_f = r, ratio, field
            if best and best_r >= FUZZY_MIN:
                out.append([head, variant, pcount[variant], recs, "нечёткое", f"{best_r:.2f}",
                            best["название"], best["тип"], best["широта"],
                            best["долгота"], best["id"], best["источник"],
                            "переименование" if best_f != "название" else "имя"])
                stat["нечёткое"] += 1
            else:
                out.append([head, variant, pcount[variant], recs, "нет", "", "", "", "", "", "", "", ""])
                stat["нет"] += 1

    with io.open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["гнездо", "написание", "записей", "записей_в_гнезде", "привязка",
                    "сходство", "осм_название", "тип", "широта", "долгота", "id",
                    "источник", "совпало_по"])
        w.writerows(out)

    total = sum(stat.values())
    print(f"  {OUT.relative_to(ROOT)} — {total} написаний")
    for k in ("строгое", "нечёткое", "нет"):
        print(f"    {k:9} {stat[k]:4}  {stat[k]*100/total:4.1f} %")
    # Сколько ЗАПИСЕЙ свода получили координаты — это и есть практический выход.
    got = sum(r[2] for r in out if r[4] != "нет")
    print(f"\n  записей свода с координатами: {got} из {len(places)} "
          f"({got*100//len(places)} %)")


if __name__ == "__main__":
    sys.exit(main())
