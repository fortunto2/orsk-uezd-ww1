#!/usr/bin/env python3
"""Эталонная выборка пар написаний: чем мерить качество гнёзд.

Порог 0,84 в analyze.py подобран на глаз. Пока нет эталона, любая замена
алгоритма — смена одного непроверенного решения на другое, которое просто
звучит солиднее. Здесь эталон строится так, чтобы **не зависеть от метрики,
которую он проверяет**: пары размечает внешний справочник, а не сходство строк.

  положительная пара — оба написания привязались СТРОГО к одному объекту
                       справочника (совпали нормализованные формы: регистр,
                       ё/е, дефис, окончание, приставка — без фонетики);
  отрицательная      — привязались строго к разным объектам, отстоящим более
                       чем на 20 км.

Отрицательные делятся на **трудные** и остальные. Трудная — у которой общее
начало от трёх букв: «Бикбау ≠ Бикбова» (75 км), ровно та ошибка, на которой
уже обжигались вручную. Признак трудности нарочно примитивен — общий префикс,
а не мера сходства: критерий отбора не должен быть той же величиной, качество
которой выборка потом измеряет. Метрики без разделения на трудные выглядят
отлично: пара «Абзаково ≠ Бискужа» не отсеет ни один алгоритм.

    python3 scripts/goldstandard.py     # → reference/gold-pairs.csv

Файл можно и нужно править руками: колонка «источник» говорит, откуда пара —
`справочник` или `вручную`. Ручные пары скрипт не трогает.
"""
import csv
import io
import math
import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze import block_key, skeleton, strip_prefix  # noqa: E402
from reconcile import norm_strict  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GEO = ROOT / "derived" / "places-geo.csv"
REF = ROOT / "reference" / "gazetteer.csv"
OUT = ROOT / "reference" / "gold-pairs.csv"
FAR_KM = 20


def km(lat1, lon1, lat2, lon2):
    return 111 * math.hypot(lat1 - lat2, (lon1 - lon2) * math.cos(math.radians(lat1)))


def same_block(a, b):
    return (block_key(skeleton(strip_prefix(a)[1]))
            == block_key(skeleton(strip_prefix(b)[1])))


def keep_manual():
    """Ручную разметку скрипт не перезаписывает — она дороже машинной."""
    if not OUT.exists():
        return []
    return [r for r in csv.reader(io.open(OUT, encoding="utf-8"))
            if len(r) == 5 and r[4] == "вручную"]


def main():
    geo = [r for r in csv.DictReader(io.open(GEO, encoding="utf-8"))
           if r["привязка"] == "строгое"]
    ref = list(csv.DictReader(io.open(REF, encoding="utf-8")))
    pairs, seen = [], set()

    def hard(a, b):
        """Трудная ли пара: общее начало от трёх букв. Нарочно не метрика."""
        x, y = strip_prefix(a)[1].lower(), strip_prefix(b)[1].lower()
        n = 0
        for ca, cb in zip(x, y):
            if ca != cb:
                break
            n += 1
        return "1" if n >= 3 else "0"

    def add(a, b, label, src):
        key = tuple(sorted((a, b)))
        if a == b or key in seen:
            return
        # Пары, различающиеся ТОЛЬКО приставкой («Александровский» против
        # «Ново-Александровский»), из эталона исключены. Они проверяют не меру
        # сходства, а отдельное решение — что делать с «Верхне-/Нижне-/Ново-»;
        # гнёзда намеренно держат такие написания вместе, вынося приставку в
        # свою колонку. Оставь их здесь — и эталон будет штрафовать метод за
        # то, что он делает нарочно.
        pa, sa = norm_strict(a)
        pb, sb = norm_strict(b)
        if sa == sb and pa != pb:
            return
        seen.add(key)
        pairs.append([key[0], key[1], label, hard(a, b), src])

    # 1. Внутри свода: два написания на одном объекте справочника.
    by_id = {}
    for r in geo:
        by_id.setdefault(r["id"], []).append(r)
    for group in by_id.values():
        for a, b in combinations(group, 2):
            add(a["написание"], b["написание"], "1", "справочник")

    # 2. Свод ↔ справочник: написание и то название, с которым оно совпало.
    #
    # ⚠️ Кроме случаев, где совпадение прошло через старое или альтернативное
    # название: «Ащылысай» и «Григорьевский» — одно село, но это ПЕРЕИМЕНОВАНИЕ,
    # и никакая мера сходства строк тут не поможет и не должна. В эталоне такая
    # пара была бы вечным ложноотрицательным и занижала бы всё подряд.
    # Переименования — отдельный и самостоятельно ценный артефакт: derived/renames.csv.
    renames = []
    for r in geo:
        if r.get("совпало_по") == "переименование":
            renames.append([r["написание"], r["осм_название"], r["тип"],
                            r["широта"], r["долгота"], r["id"], r["источник"]])
            continue
        add(r["написание"], r["осм_название"], "1", "справочник")

    # 3. Трудные отрицательные: разные объекты далеко друг от друга.
    for a, b in combinations(geo, 2):
        if a["id"] == b["id"] or not same_block(a["написание"], b["написание"]):
            continue
        if km(float(a["широта"]), float(a["долгота"]),
              float(b["широта"]), float(b["долгота"])) > FAR_KM:
            add(a["написание"], b["написание"], "0", "справочник")

    # 4. Написание против чужого названия справочника из того же блока.
    used = {r["осм_название"] for r in geo}
    for r in geo:
        la, lo = float(r["широта"]), float(r["долгота"])
        for other in ref:
            nm = other["название"]
            if nm in used or not same_block(r["написание"], nm):
                continue
            if km(la, lo, float(other["широта"]), float(other["долгота"])) > FAR_KM:
                add(r["написание"], nm, "0", "справочник")

    ren_out = ROOT / "derived" / "renames.csv"
    with io.open(ren_out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["написание_в_документе", "нынешнее_название", "тип",
                    "широта", "долгота", "id", "источник"])
        w.writerows(sorted(renames))
    print(f"  {ren_out.relative_to(ROOT)} — {len(renames)} переименований")

    # 5. Сильнейшие отрицательные даёт сам справочник: два РЕАЛЬНЫХ села с
    # фонетически неразличимыми названиями, стоящие в разных концах уезда.
    # «Бикбау» и «Бикбова» — 75 км, и именно на этой паре я однажды ошибся
    # вручную. Такие примеры независимы от свода и от любой нашей метрики.
    by_skel = {}
    for r in ref:
        nm = r["название"].strip()
        if not nm:
            continue
        by_skel.setdefault(skeleton(strip_prefix(nm)[1]), []).append(r)
    for group in by_skel.values():
        if len(group) < 2:
            continue
        for a, b in combinations(group[:6], 2):
            if a["название"] == b["название"]:
                continue
            if km(float(a["широта"]), float(a["долгота"]),
                  float(b["широта"]), float(b["долгота"])) > FAR_KM:
                add(a["название"], b["название"], "0", "справочник")

    manual = keep_manual()
    for row in manual:
        key = tuple(sorted((row[0], row[1])))
        if key not in seen:
            seen.add(key)
            pairs.append(row)

    pairs.sort(key=lambda p: (p[2], p[0]))
    with io.open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["написание_1", "написание_2", "одно_и_то_же", "трудная", "источник"])
        w.writerows(pairs)
    pos = sum(1 for p in pairs if p[2] == "1")
    hard_neg = sum(1 for p in pairs if p[2] == "0" and p[3] == "1")
    print(f"  {OUT.relative_to(ROOT)} — {len(pairs)} пар: "
          f"{pos} положительных, {len(pairs)-pos} отрицательных "
          f"(из них трудных {hard_neg}); вручную размечено {len(manual)}")


if __name__ == "__main__":
    sys.exit(main())
