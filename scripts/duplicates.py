#!/usr/bin/env python3
"""Вероятные дубли персон: один человек в нескольких документах.

Свод дедуплицирован грубо — по точному совпадению «фамилия + имя + отчество +
селение + часть». Такой ключ пропускает ровно то, чем эти документы и славны:
одного человека, записанного писарями по-разному. В донесении «Манкаманов», в
госпитальной карточке «Мелкоманов» — ключ видит двоих.

Здесь работает модель Феллеги—Санта (1969) с оценкой параметров через EM.
Разметки она не требует и тем отличается от градиентного бустинга, к которому
тянется рука: для бустинга нужны размеченные пары, а их нет. Модель сама
оценивает по данным, насколько согласие по каждому полю характерно для пар
«один человек» и для пар «разные люди», и складывает из этого вес.

Почему не splink или dedupe (они делают ровно это и лучше): обе тянут
зависимости, а свод должен пересобираться `python3` без установки чего-либо.
На 1368 записях разница в качестве модели меньше, чем разница между «скрипт
запустился» и «скрипт требует окружения».

    python3 scripts/duplicates.py      # → derived/person-duplicates.csv
"""
import csv
import io
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze import SRC, block_key, jaro_winkler, skeleton  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "derived" / "person-duplicates.csv"
NAME_MATCH = 0.90     # с какого сходства считаем поле «согласным»
REPORT_FROM = 0.95    # с какой вероятности выводим пару
EM_ROUNDS = 30

FIELDS = ["фамилия", "имя", "отчество", "часть", "волость", "населённый_пункт", "чин"]


def agree(a, b, field):
    """Согласны ли поля. Пустое значение — не согласие и не несогласие."""
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    if not a or not b:
        return None
    if field in ("волость", "чин"):
        return a == b
    return jaro_winkler(a, b) >= NAME_MATCH


def candidates(rows):
    """Блокировка: пары, где похожа фамилия либо совпало имя И селение.

    Сравнивать все 1368² пар можно, но бессмысленно: подавляющее большинство
    отличается всем сразу. Два блока вместо одного нужны потому, что писарь
    иногда перевирал именно фамилию — тогда паре помогает связка «имя + село».
    """
    by_surname, by_name_place = defaultdict(list), defaultdict(list)
    for i, r in enumerate(rows):
        sn = r["фамилия"].strip()
        if sn:
            by_surname[block_key(skeleton(sn))].append(i)
        nm, pl = r["имя"].strip().lower(), r["населённый_пункт"].strip()
        if nm and pl:
            by_name_place[(nm[:4], block_key(skeleton(pl)))].append(i)
    pairs = set()
    for buckets in (by_surname, by_name_place):
        for idx in buckets.values():
            if len(idx) > 400:      # блок-переросток сравнивать незачем
                continue
            for a in range(len(idx)):
                for b in range(a + 1, len(idx)):
                    pairs.add((idx[a], idx[b]))
    return sorted(pairs)


def em(vectors):
    """Оценка m, u и доли совпадений без разметки.

    m[f] — вероятность согласия по полю f, если это один человек;
    u[f] — то же, если разные. Начальные значения намеренно грубые: EM их
    уточняет. λ — доля пар «один человек» среди кандидатов.
    """
    m = {f: 0.90 for f in FIELDS}
    u = {f: 0.10 for f in FIELDS}
    lam = 0.01
    for _ in range(EM_ROUNDS):
        num_m = {f: 0.0 for f in FIELDS}
        num_u = {f: 0.0 for f in FIELDS}
        den_m = {f: 0.0 for f in FIELDS}
        den_u = {f: 0.0 for f in FIELDS}
        total = 0.0
        for vec in vectors:
            pm = lam
            pu = 1 - lam
            for f, g in vec.items():
                pm *= m[f] if g else 1 - m[f]
                pu *= u[f] if g else 1 - u[f]
            w = pm / (pm + pu) if pm + pu else 0.0
            total += w
            for f, g in vec.items():
                den_m[f] += w
                den_u[f] += 1 - w
                if g:
                    num_m[f] += w
                    num_u[f] += 1 - w
        for f in FIELDS:
            if den_m[f]:
                m[f] = min(max(num_m[f] / den_m[f], 0.01), 0.99)
            if den_u[f]:
                u[f] = min(max(num_u[f] / den_u[f], 0.001), 0.99)
        lam = min(max(total / len(vectors), 1e-6), 0.5)
    return m, u, lam


def main():
    rows = list(csv.DictReader(io.open(SRC, encoding="utf-8")))
    pairs = candidates(rows)
    print(f"{len(rows)} записей · {len(pairs)} пар-кандидатов после блокировки")
    if not pairs:
        sys.exit("UNKNOWN: блокировка не дала ни одной пары — сравнивать нечего")

    vectors = []
    for i, j in pairs:
        vec = {}
        for f in FIELDS:
            g = agree(rows[i][f], rows[j][f], f)
            if g is not None:
                vec[f] = g
        vectors.append(vec)

    m, u, lam = em(vectors)
    print(f"\nEM: доля совпадений среди кандидатов {lam:.4f}")
    print(f"  {'поле':18} {'m (один чел.)':>14} {'u (разные)':>12} {'вес согласия':>14}")
    for f in FIELDS:
        w = math.log2(m[f] / u[f]) if u[f] else 0
        print(f"  {f:18} {m[f]:>14.3f} {u[f]:>12.3f} {w:>+14.2f}")

    out = []
    for (i, j), vec in zip(pairs, vectors):
        pm, pu = lam, 1 - lam
        for f, g in vec.items():
            pm *= m[f] if g else 1 - m[f]
            pu *= u[f] if g else 1 - u[f]
        prob = pm / (pm + pu) if pm + pu else 0.0
        if prob >= REPORT_FROM:
            a, b = rows[i], rows[j]
            out.append([f"{prob:.4f}",
                        " ".join(x for x in (a["фамилия"], a["имя"], a["отчество"]) if x),
                        " ".join(x for x in (b["фамилия"], b["имя"], b["отчество"]) if x),
                        a["населённый_пункт"] or a["волость"],
                        b["населённый_пункт"] or b["волость"],
                        a["часть"], b["часть"], a["id_записи"], b["id_записи"]])
    out.sort(key=lambda r: -float(r[0]))
    with io.open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["вероятность", "запись_1", "запись_2", "место_1", "место_2",
                    "часть_1", "часть_2", "id_1", "id_2"])
        w.writerows(out)
    print(f"\n  {OUT.relative_to(ROOT)} — {len(out)} пар с вероятностью ≥ {REPORT_FROM}")
    for r in out[:8]:
        print(f"    {r[0]}  {r[1]:38} ≈ {r[2]:38} [{r[3]}]")

    # Сколько персон стоит за 1368 записями, если верить парам ≥ 0,99.
    # Компоненты связности, а не число пар: три записи одного человека дают
    # три пары, но схлопываются в одного.
    parent = list(range(len(rows)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    linked = 0
    for (i, j), vec in zip(pairs, vectors):
        pm, pu = lam, 1 - lam
        for f, g in vec.items():
            pm *= m[f] if g else 1 - m[f]
            pu *= u[f] if g else 1 - u[f]
        if pm / (pm + pu) >= 0.99 if pm + pu else False:
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[rj] = ri
                linked += 1
    persons = len({find(i) for i in range(len(rows))})
    print(f"\n  если верить парам ≥ 0,99: {len(rows)} записей соответствуют "
          f"примерно {persons} персонам ({linked} склеек)")


if __name__ == "__main__":
    sys.exit(main())
