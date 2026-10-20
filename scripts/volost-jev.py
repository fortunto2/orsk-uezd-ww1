#!/usr/bin/env python3
"""Хвост нормализации волостей — классификатором Jev (TypeSafe System One).

Jev не генерирует текст: на каждый вопрос он выбирает один из заданных вариантов и
отдаёт вероятности. Поэтому он не может «придумать» волость — только выбрать из
канонического списка, который собрал normalize-volost.py, или ответить «другое».
Результат кешируется в derived/volost-jev.json; normalize-volost.py подхватывает его
для написаний, которые не связал порог сходства.

    TYPESAFE_API_KEY=… python3 scripts/volost-jev.py
"""
import csv
import importlib.util
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VARIANTS = ROOT / "derived" / "v2" / "volost-variants.csv"
CACHE = ROOT / "derived" / "v2" / "volost-jev.json"
BATCH = 20  # 32 вопроса с 29 вариантами — уже max_tokens_exceeded
OTHER = "другое"

spec = importlib.util.spec_from_file_location("nv", ROOT / "scripts" / "normalize-volost.py")
nv = importlib.util.module_from_spec(spec); spec.loader.exec_module(nv)


def canon_bases(rows):
    """Основы, которые порог уже связал: только они и могут быть ответом."""
    out = {}
    for r in rows:
        if r["волость_норм"] and r["сходство"] == "100":
            n, base, kind = nv.split(r["написание"])
            b = nv.ALIASES.get(base, base)
            out.setdefault(b, []).append(r["написание"])
    return {b: v[:4] for b, v in out.items() if b}


def ask(batch, bases):
    criteria = {b: f"волость Орского уезда; так её писали: {', '.join(v)}" for b, v in bases.items()}
    criteria[OTHER] = "станица, посёлок, волость другого уезда или нечитаемое"
    q = {f"v{i}": {"type": "choice", "criteria": criteria,
                   "instructions": f"Какая это волость, если писарь записал её как «{raw}»?"}
         for i, raw in enumerate(batch)}
    req = {"state": "Написания волостей Орского уезда из документов 1914–1918 годов; писари ошибались на слух.",
           "model": "jev-latest", "questions": q}
    base = os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai").rstrip("/")
    r = urllib.request.Request(f"{base}/v1/systemone", data=json.dumps(req).encode(),
                               headers={"Authorization": f"Bearer {os.environ['TYPESAFE_API_KEY']}",
                                        "Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=120) as resp:
        body = json.load(resp)
    return body["answers"], body.get("usage", {})


def main():
    rows = list(csv.DictReader(open(VARIANTS, encoding="utf-8")))
    bases = canon_bases(rows)
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    todo = [r["написание"] for r in rows if not r["волость_норм"] and r["написание"] not in cache]
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else len(todo)
    todo = todo[:limit]
    print(f"канонических основ {len(bases)}, к разбору {len(todo)}", file=sys.stderr)
    tokens = 0
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        ans, usage = ask(batch, bases)
        tokens += usage.get("input_tokens", 0)
        for j, raw in enumerate(batch):
            a = ans[f"v{j}"]
            cache[raw] = [a["choice"], round(a["probabilities"][a["choice"]], 3)]
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0))
        print(f"{i + len(batch)}/{len(todo)}  токенов {tokens}", file=sys.stderr)
        time.sleep(0.3)


if __name__ == "__main__":
    main()
