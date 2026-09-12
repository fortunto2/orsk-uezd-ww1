#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["rapidfuzz>=3"]
# ///
"""Сравнение мер сходства на эталонной выборке пар.

Порог 0,84 и свёртки согласных в analyze.py были подобраны на глаз. Здесь они
меряются: reference/gold-pairs.csv размечен внешним справочником, а не тем, что
проверяется, поэтому сравнение честное.

    uv run scripts/benchmark.py        # → derived/benchmark.md

Две колонки цифр не случайны. «Все пары» включает тривиальные отрицательные
(«Абзаково ≠ Бискужа»), которые не отсеет никакой алгоритм, и потому все меры
там выглядят прекрасно. Смотреть надо на **трудные**: положительные против
отрицательных с общим началом от трёх букв.
"""
import csv
import io
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze import FOLD, strip_prefix, jaro_winkler, plain as PLAIN_NAME  # noqa: E402

try:
    from rapidfuzz.distance import JaroWinkler, Levenshtein
except ImportError:
    sys.exit("Нужен rapidfuzz: uv run scripts/benchmark.py")

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / "reference" / "gold-pairs.csv"
OUT = ROOT / "derived" / "benchmark.md"

# Расширенный набор свёрток — тот, что стоял в первой версии analyze.py и
# склеивал «Гайсина» с «Хасановым». Держим его здесь как контрольный образец:
# утверждение «х↔г и ч↔с вредны» должно быть числом, а не воспоминанием.
FOLD_WIDE = FOLD + [("х", "г"), ("ч", "с")]


def make_skeleton(folds, drop_endings=True):
    import re
    def f(name):
        s = strip_prefix(name)[1].lower()
        s = re.sub(r"[ъь\s\-\.\,]", "", s)
        for a, b in folds:
            s = s.replace(a, b)
        if drop_endings:
            s = re.sub(r"(ов|ева|ина|ино|ово|ева|ски|скии|скаа|ска)$", "", s)
        return s
    return f


PLAIN = make_skeleton([], drop_endings=False)
NOFOLD = make_skeleton([])
CURRENT = make_skeleton(FOLD)
WIDE = make_skeleton(FOLD_WIDE)

MEASURES = {
    "difflib + скелет (нынешний)": lambda a, b: SequenceMatcher(None, CURRENT(a), CURRENT(b)).ratio(),
    "difflib + сырая строка": lambda a, b: SequenceMatcher(None, PLAIN(a), PLAIN(b)).ratio(),
    "difflib + без свёрток": lambda a, b: SequenceMatcher(None, NOFOLD(a), NOFOLD(b)).ratio(),
    "difflib + широкие свёртки": lambda a, b: SequenceMatcher(None, WIDE(a), WIDE(b)).ratio(),
    "Jaro-Winkler + скелет": lambda a, b: JaroWinkler.similarity(CURRENT(a), CURRENT(b)),
    "Jaro-Winkler + сырая": lambda a, b: JaroWinkler.similarity(PLAIN(a), PLAIN(b)),
    "Левенштейн норм. + скелет": lambda a, b: Levenshtein.normalized_similarity(CURRENT(a), CURRENT(b)),
    # Та самая функция, что стоит в analyze.py. Меряем именно её: rapidfuzz
    # ниже — контроль, а не замена. Реализации Jaro расходятся на строках с
    # повторяющимися буквами (см. ниже), и мерить одной, а внедрять другую —
    # верный способ получить в работе не те числа, что в отчёте.
    "Jaro-Winkler НАШ + сырая": lambda a, b: jaro_winkler(PLAIN_NAME(a), PLAIN_NAME(b)),
    # Компромисс: берём лучшее из двух взглядов — Джаро—Винклер видит общее
    # начало, скелет видит звучание. Написания «Бикбау»/«Пикбау» ловит второй,
    # «Бикбердина»/«Бикберды» — первый.
    "максимум (наш JW, скелет)": lambda a, b: max(
        jaro_winkler(PLAIN_NAME(a), PLAIN_NAME(b)),
        SequenceMatcher(None, CURRENT(a), CURRENT(b)).ratio()),
    "difflib, максимум из двух": lambda a, b: max(
        SequenceMatcher(None, CURRENT(a), CURRENT(b)).ratio(),
        SequenceMatcher(None, PLAIN(a), PLAIN(b)).ratio()),
}

# Чем мерить — решение содержательное, и три первые попытки были неверны.
# Оставлены здесь, потому что каждая ошибалась по-своему.
#
# 1. F1 считает пропуск и ложное объединение одинаково вредными. Для
#    генеалогического поиска это не так: ненайденное написание — ненайденный
#    предок, а лишнее в гнезде человек отбрасывает взглядом.
# 2. F2 (полнота весит вдвое) выродилась: лучший порог сполз к 0,63, где
#    полнота 1,000 при точности 0,529 — половина объединений ложные. Величина,
#    которую максимизируешь, всегда найдёт способ вырасти без пользы.
# 3. «Максимум полноты при точности ≥ 0,8» провалился тоньше и опаснее: почти
#    ни одна мера такой точности не достигла. Но это свойство не мер, а
#    выборки — в неё нарочно набрано 142 трудных отрицательных пары на 81
#    положительную, тогда как в работе алгоритм сравнивает написания внутри
#    одного свода одной волости, где похожих-но-разных несопоставимо меньше.
#    Точность зависит от доли положительных в потоке, а долю здесь задала моя
#    рука при сборе выборки. Мерить ею — мерить собственный произвол.
#
# Отсюда две величины, от баланса классов не зависящие:
#
#   полнота   — какую долю известных пар «одно и то же» метод объединяет;
#   ложных-Т  — какую долю ТРУДНЫХ отрицательных («Бикбау» / «Бикбова», 75 км)
#               он ошибочно склеивает.
#
# Порог выбирается как наибольшая полнота при ложных-Т не выше 10 %.
BETA = 2
MAX_FALSE_HARD = 0.10


def load():
    rows = list(csv.DictReader(io.open(GOLD, encoding="utf-8")))
    pairs = [(r["написание_1"], r["написание_2"], r["одно_и_то_же"] == "1",
              r["трудная"] == "1") for r in rows]
    if not pairs:
        sys.exit("UNKNOWN: эталон пуст — мерить нечем")
    pos = sum(1 for p in pairs if p[2])
    if pos == 0 or pos == len(pairs):
        sys.exit("UNKNOWN: в эталоне метки одного класса — метрика не определена")
    return pairs


def score(pairs, fn, threshold):
    tp = fp = fn_ = 0
    hard_neg = hard_fp = 0
    for a, b, same, hard in pairs:
        pred = fn(a, b) >= threshold
        if not same and hard:
            hard_neg += 1
            hard_fp += pred
        if pred and same:
            tp += 1
        elif pred and not same:
            fp += 1
        elif not pred and same:
            fn_ += 1
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn_) if tp + fn_ else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    b2 = BETA ** 2
    f2 = (1 + b2) * p * r / (b2 * p + r) if p + r else 0.0
    false_hard = hard_fp / hard_neg if hard_neg else 0.0
    return p, r, f2, tp, fp, fn_, f1, false_hard


GRID = [i / 100 for i in range(60, 100)]


def best(pairs, fn):
    """Порог с наибольшей полнотой среди тех, где ложных-Т не больше предела.

    Если такого порога нет вовсе — мера не годится, и это сообщается, а не
    подменяется лучшим из плохих: «ни один порог не удержал ложные» и «вот
    лучший» — разные ответы.
    """
    ok = []
    for t in GRID:
        sc = score(pairs, fn, t)
        if sc[7] <= MAX_FALSE_HARD:
            ok.append((t,) + sc)
    return max(ok, key=lambda x: x[2]) if ok else None


def main():
    pairs = load()
    hard = [p for p in pairs if p[2] or p[3]]
    npos = sum(1 for p in pairs if p[2])
    nhard_neg = sum(1 for p in pairs if not p[2] and p[3])
    print(f"эталон: {len(pairs)} пар, из них трудных {len(hard)}\n")

    lines = [
        "# Чем мерить гнёзда: сравнение мер сходства\n",
        "> Генерируется `uv run scripts/benchmark.py`. Руками не править.\n",
        f"Эталон — `reference/gold-pairs.csv`: {len(pairs)} пар, из них "
        f"{npos} положительных и {nhard_neg} трудных отрицательных. Размечен "
        "внешним справочником, а не той мерой, которую проверяет.\n",
        "Критерий — **наибольшая полнота при доле ложных объединений среди "
        f"трудных отрицательных не выше {MAX_FALSE_HARD:.0%}**. Точность "
        "намеренно не используется: она зависит от доли положительных пар в "
        "выборке, а эту долю задала рука составителя.\n",
        "| Мера | Порог | Полнота | Ложных-Т | F1 | Пропущено |",
        "|---|---|---|---|---|---|",
    ]
    rows, failed = [], []
    for name, fn in MEASURES.items():
        b = best(hard, fn)
        if b is None:
            failed.append(name)
            print(f"  {name:30} — ни один порог не удержал ложные-Т ≤ {MAX_FALSE_HARD}")
            continue
        t, p, r, f2, tp, fp, fn_, f1, fh = b
        rows.append((r, name, t, fh, f1, fn_))
        print(f"  {name:30} порог {t:.2f}  полнота {r:.3f}  "
              f"ложных-Т {fh:.3f}  пропущено {fn_}")
    for r, name, t, fh, f1, miss in sorted(rows, reverse=True):
        lines.append(f"| {name} | {t:.2f} | **{r:.3f}** | {fh:.3f} | {f1:.3f} | {miss} |")
    if failed:
        lines += ["", "Ни при каком пороге не удерживают ложные-Т ниже "
                  f"{MAX_FALSE_HARD:.0%}: {', '.join(failed)}."]

    # Известный ответ: три канонических примера должны совпасть с rapidfuzz
    # до девятого знака, иначе в реализации ошибка и мерить нечего.
    for a, b in (("MARTHA", "MARHTA"), ("DIXON", "DICKSONX"),
                 ("JELLYFISH", "SMELLYFISH")):
        if abs(jaro_winkler(a, b) - JaroWinkler.similarity(a, b)) > 1e-9:
            sys.exit(f"UNKNOWN: наш Jaro-Winkler расходится с rapidfuzz на {a}/{b}")

    cur = MEASURES["difflib + скелет (нынешний)"]
    _p, r0, _f2, _tp, fp0, fn0, _f1, fh0 = score(hard, cur, 0.84)[:8]
    cur_best = best(hard, cur)
    lines += ["", "## Что стоит в коде и что из этого следует\n",
              f"`analyze.py` сравнивает difflib по фонетическому скелету при "
              f"пороге **0,84**: полнота **{r0:.3f}**, ложных-Т **{fh0:.3f}** "
              f"({fp0} ложных объединений, {fn0} пропущенных). Доля ложных "
              f"{'выше' if fh0 > MAX_FALSE_HARD else 'укладывается в'} "
              f"предел {MAX_FALSE_HARD:.0%}"
              + (f"; при пороге {cur_best[0]:.2f}, где эта мера в предел "
                 f"укладывается, её полнота падает до {cur_best[2]:.3f}."
                 if cur_best and fh0 > MAX_FALSE_HARD else ".")]
    top_r, top_name, top_t, top_fh, *_ = sorted(rows, reverse=True)[0]
    if top_name.startswith("difflib + скелет"):
        lines.append("Нынешняя мера оказалась лучшей из проверенных.")
    else:
        lines.append(
            f"Лучшая из проверенных — **{top_name}** при пороге {top_t:.2f}: "
            f"полнота **{top_r:.3f}** при ложных-Т {top_fh:.3f} — против "
            f"{cur_best[2]:.3f} у нынешней меры на её допустимом пороге "
            f"({top_r/cur_best[2]:.1f}×)." if cur_best else "")

    lines += ["", "## Свёртки согласных: измерено, но не там\n",
              "Первая версия сворачивала х↔г и ч↔с и склеила фамилии «Гайсин» "
              "и «Хасанов». На этом эталоне разницы между наборами свёрток "
              "нет — и это не оправдание широкому набору, а **граница "
              "бенчмарка**: эталон построен по названиям селений, потому что "
              "справочник есть только для них. Для фамилий внешнего арбитра "
              "нет, и склейка «Гайсин / Хасанов» в выборку не попадает ни в "
              "каком виде.\n",
              "Что из этого следует практически: числа ниже относятся к "
              "топонимам. Порог для фамилий (0,88 в `analyze.py`) остаётся "
              "непроверенным, и так и надо к нему относиться.\n",
              "## Две реализации Jaro дают разные числа\n",
              "Классический алгоритм сопоставляет символы жадно слева "
              "направо, и на строках с повторяющимися буквами это не "
              "оптимально: «бикбердина» / «бикбирды» дают у нас 0,872, у "
              "`rapidfuzz` 0,886 (7 совпадений у обоих, но 3 транспозиции "
              "против 2). На канонических примерах (MARTHA/MARHTA и прочих) "
              "реализации совпадают до девятого знака — расхождение вылезает "
              "именно на повторах, которых в тюркских названиях много.\n",
              "Поэтому в таблице меряется **функция из `analyze.py`**, а "
              "`rapidfuzz` оставлен контролем. Обратный порядок — померить "
              "библиотекой, внедрить своё — дал бы отчёт про другой код.\n",
              "## Чего эталон не видит\n",
              "1. **Смещение по построению.** Положительные пары взяты из "
              "строгой привязки к справочнику, а она сравнивает написания "
              "*без* фонетики. Значит, пары, различающиеся именно согласными "
              "(«Бикбау» / «Пикбау»), в выборку почти не попадают — их "
              "добавлено 10 штук вручную, и это мало.\n"
              "2. **Только уцелевшие селения.** Три четверти написаний свода "
              "не привязались ни к OSM, ни к Wikidata: деревень больше нет на "
              "карте. Эталон описывает ту четверть, что дожила.\n"
              "3. **Переименования исключены** (`derived/renames.csv`): "
              "«Григорьевский» и «Ащылысай» — одно село, но никакая мера "
              "сходства строк этого не увидит и не должна.\n"]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n  {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.exit(main())
