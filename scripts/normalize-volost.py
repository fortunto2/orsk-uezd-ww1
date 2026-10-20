#!/usr/bin/env python3
"""Нормализация волостей в своде версии 2: 2762 написания → канонические волости.

Писари писали волость как слышали: «Серганская», «Сарганская», «Усерганская 2-я»,
«2-я Усерганская». Разбор: номер выделяется отдельно (где бы он ни стоял), основа
сравнивается с каноническим списком нечётко. Канонический список — основы, которые
встречаются не реже MIN_BASE раз (опечатка редка, настоящая волость часта) плюс
явные правки ниже. Исходная колонка не трогается; добавляются `волость_норм` и
`волость_сходство` (0–100, либо «jev 0.93» — ответ классификатора). Ниже порога — пусто: лучше не знать, чем выдумать.

    uv run --with rapidfuzz scripts/normalize-volost.py
"""
import collections
import csv
import json
import re
import sys
from pathlib import Path

from rapidfuzz import process
from rapidfuzz.distance import Levenshtein

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "pmv-orsk-uezd-1914-1918-v2.csv"
MAP = ROOT / "derived" / "v2" / "volost-variants.csv"
JEV = ROOT / "derived" / "v2" / "volost-jev.json"   # ответы классификатора Jev для хвоста (scripts/volost-jev.py)
JEV_MIN = 0.8      # ниже Jev ошибается: «Павловская» → Покровская 0.42, «Сетновская» → Сергеевская 0.41
MIN_BASE = 15      # основа считается волостью, если встречается хотя бы столько раз
THRESHOLD = 85     # нормализованное сходство Левенштейна, 0–100

# основы-синонимы, которые порог не свяжет: дефис, одна буква в начале
ALIASES = {"серганская": "усерганская", "сарганская": "усерганская",
           "хайбулинская": "усерганы-хайбуллинская", "хайбуллинская": "усерганы-хайбуллинская",
           "новониколаевская": "ново-николаевская", "новопокровская": "ново-покровская",
           "бурзянотаналыкская": "бурзяно-таналыкская", "карагайкипчакская": "карагай-кипчакская",
           "усарганская": "усерганская", "усергановская": "усерганская", "сергановская": "усерганская",
           "тунгаурская": "тангауровская", "тангаурская": "тангауровская"}


def split(raw):
    """«Усерганская 2-я вол.» → (2, 'усерганская'). Станица/посёлок остаются типом."""
    s = raw.lower().replace("ё", "е")
    kind = "станица" if "станиц" in s else ("поселок" if "посел" in s or "пос." in s else "волость")
    for i, w in enumerate(("перв", "втор", "трет", "четверт", "пят", "шест", "седьм", "восьм"), 1):
        s = re.sub(rf"\b{w}\w*\b", f"{i}-я", s)
    num = re.search(r"(\d+)\s*-?\s*(?:я|й|ая)?", s)
    n = int(num.group(1)) if num and int(num.group(1)) < 10 else None
    s = re.sub(r"\d+\s*-?\s*(?:я|й|ая)?", " ", s)
    s = re.sub(r"\b(волост\w*|вол\.?|станица|станицы|пос\.?|поселок)\b", " ", s)
    s = re.sub(r"[^а-я\- ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -")
    return n, s, kind


def canon_name(n, base, kind):
    title = "-".join(w.capitalize() for w in base.split("-"))
    if kind == "станица":
        return f"{title} станица"
    return f"{n}-я {title} вол." if n else f"{title} вол."


def main():
    rows = list(csv.DictReader(open(SRC, encoding="utf-8")))
    parts = {raw: split(raw) for raw in {r["волость"] for r in rows if r["волость"]}}
    freq = collections.Counter()
    for r in rows:
        if r["волость"]:
            freq[parts[r["волость"]][1]] += 1
    bases = sorted(b for b, c in freq.items() if c >= MIN_BASE and b and b not in ALIASES)
    jev = json.loads(JEV.read_text()) if JEV.exists() else {}
    mapping = {}
    for raw, (n, base, kind) in parts.items():
        b = ALIASES.get(base, base)
        if b in bases:
            mapping[raw] = (canon_name(n, b, kind), 100)
            continue
        hit = process.extractOne(b, bases, scorer=Levenshtein.normalized_similarity) if b else None
        score = round(hit[1] * 100) if hit else 0
        mapping[raw] = (canon_name(n, hit[0], kind), score) if hit and score >= THRESHOLD else ("", score)
        if not mapping[raw][0] and raw in jev:
            choice, p = jev[raw]
            if choice in bases and p >= JEV_MIN:
                mapping[raw] = (canon_name(n, choice, kind), f"jev {p:.2f}")

    with open(MAP, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["написание", "записей", "волость_норм", "сходство"])
        cnt = collections.Counter(r["волость"] for r in rows if r["волость"])
        for raw, c in cnt.most_common():
            w.writerow([raw, c, *mapping[raw]])

    for r in rows:
        r["волость_норм"], r["волость_сходство"] = mapping.get(r["волость"], ("", ""))
    cols = list(rows[0].keys())
    i = cols.index("волость") + 1
    cols = cols[:i] + ["волость_норм", "волость_сходство"] + [c for c in cols[i:] if c not in ("волость_норм", "волость_сходство")]
    with open(SRC, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)

    with_vol = sum(1 for r in rows if r["волость"])
    norm = sum(1 for r in rows if r["волость_норм"])
    print(f"написаний {len(parts)} → волостей {len({m[0] for m in mapping.values() if m[0]})}; "
          f"записей с волостью {with_vol}, нормализовано {norm} ({norm / with_vol:.1%})", file=sys.stderr)


if __name__ == "__main__":
    main()
