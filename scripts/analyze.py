#!/usr/bin/env python3
"""Производные таблицы к своду уроженцев Орского уезда в документах ПМВ.

Главная задача — не статистика, а поиск. Писари записывали одну деревню
десятком способов («Нижний Биг-Бирд» и «Бикбердина» — одно село), и человек,
который ищет предка по современному написанию, половину записей не находит.
Скрипт собирает словарь вариантов и сводки, которые из плоского CSV не видны.

    python3 scripts/analyze.py            # → derived/*.csv, derived/summary.md

Зависимостей нет, только стандартная библиотека.
"""
import csv
import io
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "pmv-orsk-uezd-1914-1918.csv"
if len(sys.argv) > 1 and sys.argv[1].endswith(".csv"):
    SRC = Path(sys.argv[1])       # свод версии 2 передаётся аргументом
OUT = ROOT / "derived"

# Приставки, которые в источнике то есть, то нет: «Верхне-Бикберд» и «Бикбердина»
# — одно гнездо названий. Приставку отрезаем для сравнения, но СОХРАНЯЕМ в
# отдельном поле: Верхняя и Нижняя Бикберда — два разных селения, и склеивать
# их в одно нельзя.
PREFIX = re.compile(
    r"^(верхне|верхний|верхняя|верхн|нижне|нижний|нижняя|нижн|"
    r"ново|новый|новая|ст|старо|старый|мало|малый|малая|больше|большой|большая)"
    r"[\s\-\.]*", re.I)

# Гласные писарь слышал как придётся; звонкие и глухие в тюркских названиях
# передавались вперемешку (Бикбау / Пикбау). Сводим к одному скелету.
# ⚠️ Свёртки подбирались по ошибкам, а не по теории. х→г и ч→с пришлось
# убрать: с ними «Гайсин» и «Хасанов» оказались одним гнездом.
FOLD = [("ё", "е"), ("й", "и"), ("ы", "и"), ("э", "е"), ("я", "а"), ("ю", "у"),
        ("п", "б"), ("ф", "в"), ("к", "г"), ("т", "д"), ("ш", "с"), ("щ", "с"),
        ("з", "с"), ("ж", "с"), ("ц", "с")]
VOWELS = set("аеиоу")


def strip_prefix(name):
    """('Нижне-Бикберды') → ('нижне', 'бикберды')."""
    m = PREFIX.match(name.strip())
    if not m:
        return "", name.strip()
    return m.group(1).lower(), name[m.end():].strip()


def skeleton(name):
    """Фонетический скелет: то, что у разных написаний одного села общее."""
    s = name.lower()
    s = re.sub(r"[ъь\s\-\.\,]", "", s)
    for a, b in FOLD:
        s = s.replace(a, b)
    # Окончания -о/-а/-ы/-ино/-ова писарь ставил произвольно.
    s = re.sub(r"(ов|ева|ина|ино|ово|ева|ски|скии|скаа|ска)$", "", s)
    return s


def jaro_winkler(a, b, prefix_weight=0.1):
    """Мера Джаро—Винклера. Своя, чтобы датасет пересобирался без зависимостей.

    Почему она, а не difflib: бенчмарк на эталонной выборке (см.
    derived/benchmark.md) даёт полноту 0,506 против 0,284 при той же доле
    ложных склеек. Винклеровская добавка премирует совпадающее начало слова —
    а писарь путал прежде всего середину и хвост: «Бикбердина» / «Бикбирды».

    Сверено с rapidfuzz.distance.JaroWinkler на всех парах эталона:
    максимальное расхождение 1e-9 (scripts/benchmark.py --selftest).
    """
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    window = max(len(a), len(b)) // 2 - 1
    if window < 0:
        window = 0
    a_hit, b_hit = [False] * len(a), [False] * len(b)
    matches = 0
    for i, ch in enumerate(a):
        for j in range(max(0, i - window), min(len(b), i + window + 1)):
            if not b_hit[j] and b[j] == ch:
                a_hit[i] = b_hit[j] = True
                matches += 1
                break
    if not matches:
        return 0.0
    k = transpositions = 0
    for i, ch in enumerate(a):
        if not a_hit[i]:
            continue
        while not b_hit[k]:
            k += 1
        if ch != b[k]:
            transpositions += 1
        k += 1
    t = transpositions / 2
    jaro = (matches / len(a) + matches / len(b) + (matches - t) / matches) / 3
    prefix = 0
    for ca, cb in zip(a[:4], b[:4]):
        if ca != cb:
            break
        prefix += 1
    return jaro + prefix * prefix_weight * (1 - jaro)


def plain(name):
    """Написание без приставки, регистра и разделителей — без всякой фонетики.

    Мера сравнивает именно такие строки: свёртки согласных на эталоне полноту
    не подняли. Скелет остался, но только для раскладки по корзинам.
    """
    s = strip_prefix(name)[1].lower().replace("ё", "е")
    return re.sub(r"[ъь\s\-\.\,]", "", s)


def block_key(sk):
    """Ключ блокировки: согласный костяк первых слогов.

    Сравнивать каждое название с каждым — 661² операций и куча ложных пар.
    Сначала раскладываем по грубым корзинам, внутри корзины сравниваем точно.
    """
    cons = [c for c in sk if c not in VOWELS]
    return "".join(cons[:3]) or sk[:3]


def cluster(names, counts, threshold=0.96, measure="both"):
    """Собираем написания в гнёзда вокруг самого частого («лидера»).

    Сначала было объединение по цепочке: A похоже на B, B на C — значит все
    трое одно. На живых данных цепочка связала «Гайсин» с «Хасановым» через
    «Хасинова», и таких сцепок набралось с десяток. Поэтому объединения по
    транзитивности нет: каждый вариант сравнивается с ЗАГОЛОВКОМ гнезда, и
    сходство с ним выписывается в таблицу — читатель может отсеять слабые
    сам, не веря порогу на слово.

    Порог 0,95 выбран не на глаз, а по эталонной выборке: это наибольшая
    полнота среди порогов, удерживающих долю ложных склеек среди трудных
    отрицательных пар ниже 10 % (derived/benchmark.md).

    Мер две, и это не лень, а следствие того, что измерено:

      measure="both"     Максимум из двух взглядов: Джаро—Винклер по
                         написанию без фонетики видит общее начало
                         («Бикбердина» / «Бикберды»), difflib по скелету видит
                         звучание («Бикбау» / «Пикбау»). Для ТОПОНИМОВ. На
                         эталоне полнота 0,370 против 0,284 у старой меры.
                         Чистый Джаро—Винклер даёт формально больше (0,407),
                         но эталон смещён против фонетики по построению —
                         положительные пары в нём взяты из привязки, которая
                         сравнивает написания БЕЗ свёрток, — и на известном
                         примере гнездо нашей деревни при нём распадалось с
                         шести написаний до трёх. Здесь число проиграло
                         разобранному случаю сознательно, и это записано,
                         чтобы следующий читатель не «исправил» обратно.
      measure="skeleton" difflib по фонетическому скелету, порог 0,86. Для
                         ФАМИЛИЙ, потому что эталона для них нет: справочник
                         существует только для селений. Перенос топонимной
                         меры сюда вслепую разваливает известные гнёзда — с
                         Джаро—Винклером фамилия, записанная в своде девятью
                         способами, собирается в двойку: варианты расходятся
                         в первом слоге, который эта мера премирует сильнее
                         всего.

                         Порог 0,86 подобран не эталоном, а глазами: снижение
                         с 0,88 добавляет 17 гнёзд, из них 14 бесспорных
                         («Севастьянов / Севостьянов», «Казырбаков /
                         Кызырбаков / Козырбаков») и 3 сомнительных
                         («Муртазин / Муратшин», «Бородулин / Бородин»,
                         «Абдрашитов / Абдрашимов»). Одна ошибка на шесть
                         гнёзд — цена, приемлемая для словаря-подсказки, но
                         это оценка вручную, а не измерение.
    """
    order = sorted(names, key=lambda n: (-counts[n], -len(n), n))
    skel = {n: skeleton(strip_prefix(n)[1]) for n in names}
    flat = {n: plain(n) for n in names}
    buckets = defaultdict(list)
    for n in names:
        buckets[block_key(skel[n])].append(n)

    taken, nests = set(), {}
    for leader in order:
        if leader in taken:
            continue
        taken.add(leader)
        members = [(leader, 1.0)]
        for cand in buckets[block_key(skel[leader])]:
            if cand in taken:
                continue
            if flat[cand] == flat[leader] or skel[cand] == skel[leader]:
                ratio = 1.0
            elif measure == "skeleton":
                ratio = SequenceMatcher(None, skel[leader], skel[cand]).ratio()
            else:
                ratio = max(jaro_winkler(flat[leader], flat[cand]),
                            SequenceMatcher(None, skel[leader], skel[cand]).ratio())
            if ratio >= threshold:
                taken.add(cand)
                members.append((cand, ratio))
        nests[leader] = members
    return nests


def write(path, header, rows):
    with io.open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
    print(f"  {path.relative_to(ROOT)} — {len(rows)} строк")


def main():
    rows = list(csv.DictReader(io.open(SRC, encoding="utf-8")))
    OUT.mkdir(exist_ok=True)
    print(f"{len(rows)} записей из {SRC.name}\n")

    # --- 1. Гнёзда написаний населённых пунктов -----------------------------
    places = [r["населённый_пункт"].strip() for r in rows if r["населённый_пункт"].strip()]
    pcount = Counter(places)
    groups = cluster(set(places), pcount)

    variant_rows, nest_rows = [], []
    for head, members in groups.items():
        total = sum(pcount[v] for v, _ in members)
        for v, ratio in sorted(members, key=lambda m: (-pcount[m[0]], -m[1])):
            pref, bare = strip_prefix(v)
            variant_rows.append([head, v, pref, bare, pcount[v], f"{ratio:.2f}", total])
        if len(members) > 1:
            nest_rows.append([head, len(members), total, f"{min(r for _, r in members):.2f}",
                              " · ".join(v for v, _ in
                                         sorted(members, key=lambda m: -pcount[m[0]]))])
    variant_rows.sort(key=lambda r: (-r[6], r[0], -r[4]))
    for r in variant_rows:
        del r[6]
    nest_rows.sort(key=lambda r: (-r[1], -r[2]))

    write(OUT / "place-variants.csv",
          ["гнездо", "написание_в_источнике", "приставка", "основа", "записей",
           "сходство_с_гнездом"],
          variant_rows)
    write(OUT / "place-nests.csv",
          ["гнездо", "вариантов", "записей", "мин_сходство", "все_написания"], nest_rows)

    # --- 2. Сводка по гнёздам: структура выбытия ----------------------------
    where = {v: head for head, members in groups.items() for v, _ in members}
    by_place = defaultdict(Counter)
    vol = defaultdict(Counter)
    for r in rows:
        p = r["населённый_пункт"].strip()
        if not p:
            continue
        by_place[where[p]][r["причина_выбытия"] or "не указано"] += 1
        if r["волость"]:
            vol[where[p]][r["волость"]] += 1
    place_rows = []
    for head, c in sorted(by_place.items(), key=lambda kv: -sum(kv[1].values())):
        v = vol[head].most_common(1)
        place_rows.append([head, sum(c.values()),
                           c.get("ранен/контужен", 0) + c.get("ранен", 0),
                           c.get("болен", 0), c.get("пропал без вести", 0),
                           c.get("убит", 0), v[0][0] if v else ""])
    write(OUT / "places.csv",
          ["гнездо", "всего", "ранен", "болен", "пропал_без_вести", "убит", "волость"],
          place_rows)

    # --- 3. Гнёзда фамилий --------------------------------------------------
    surnames = [r["фамилия"].strip() for r in rows if r["фамилия"].strip()]
    scount = Counter(surnames)
    # ⚠️ Фамилии кластеризуются СТАРОЙ мерой и старым порогом: эталона для
    # них нет, а перенос топонимной меры вслепую разваливает известные гнёзда.
    sgroups = cluster(set(surnames), scount, threshold=0.86, measure="skeleton")
    srows = []
    for head, members in sgroups.items():
        if len(members) < 2:
            continue
        srows.append([head, len(members), sum(scount[v] for v, _ in members),
                      f"{min(r for _, r in members):.2f}",
                      " · ".join(v for v, _ in
                                 sorted(members, key=lambda m: -scount[m[0]]))])
    srows.sort(key=lambda r: (-r[1], -r[2]))
    write(OUT / "surname-nests.csv",
          ["гнездо", "вариантов", "записей", "мин_сходство", "все_написания"], srows)

    # --- 4. Помесячная хронология -------------------------------------------
    months = defaultdict(Counter)
    for r in rows:
        d = r["дата_события"]
        if len(d) >= 7:
            months[d[:7]][r["причина_выбытия"] or "не указано"] += 1
    causes = ["ранен/контужен", "болен", "пропал без вести", "убит",
              "оставлен на поле боя"]
    mrows = [[m, sum(months[m].values())] + [months[m].get(c, 0) for c in causes]
             for m in sorted(months)]
    write(OUT / "monthly.csv", ["месяц", "всего"] + causes, mrows)

    # --- 5. Воинские части ---------------------------------------------------
    units = Counter(r["часть"].strip() for r in rows if r["часть"].strip())
    write(OUT / "units.csv", ["часть", "уроженцев_уезда"],
          [[u, n] for u, n in units.most_common()])

    # --- 6. График по месяцам, SVG без библиотек ----------------------------
    svg_monthly(mrows, OUT / "monthly.svg")
    print(f"  derived/monthly.svg")

    summary(rows, groups, pcount, nest_rows, srows, mrows, units)
    print(f"  derived/summary.md")


def svg_monthly(mrows, path):
    """Столбики по месяцам. Подписи — только январи, иначе не читается."""
    if not mrows:
        return
    w, h, pad = 900, 320, 46
    top = max(r[1] for r in mrows) or 1
    bw = (w - 2 * pad) / len(mrows)
    bars, ticks = [], []
    for i, r in enumerate(mrows):
        bh = (h - 2 * pad) * r[1] / top
        x, y = pad + i * bw, h - pad - bh
        bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw*0.82:.1f}" '
                    f'height="{bh:.1f}"><title>{r[0]}: {r[1]}</title></rect>')
        if r[0].endswith("-01"):
            ticks.append(f'<text x="{x:.1f}" y="{h-pad+16:.0f}" class="ax">{r[0][:4]}</text>')
    grid = "".join(
        f'<line x1="{pad}" x2="{w-pad}" y1="{h-pad-(h-2*pad)*v/top:.1f}" '
        f'y2="{h-pad-(h-2*pad)*v/top:.1f}" class="g"/>'
        f'<text x="{pad-8}" y="{h-pad-(h-2*pad)*v/top+4:.1f}" class="ax r">{v}</text>'
        for v in (0, top // 2, top))
    path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'font-family="system-ui,sans-serif">'
        f'<style>rect{{fill:#7a3b2e}}.g{{stroke:#d8d2c8}}'
        f'.ax{{font-size:12px;fill:#6b6459}}.r{{text-anchor:end}}'
        f'.t{{font-size:14px;fill:#2b2621}}</style>'
        f'<text x="{pad}" y="24" class="t">Выбытие уроженцев Орского уезда '
        f'по месяцам, 1914—1918</text>{grid}{"".join(bars)}{"".join(ticks)}</svg>',
        encoding="utf-8")


def summary(rows, groups, pcount, nests, snests, mrows, units):
    """Отчёт цифрами — чтобы не пересчитывать вручную при обновлении свода."""
    placed = sum(pcount.values())
    multi = [n for n in nests if n[1] >= 3]
    peak = max(mrows, key=lambda r: r[1]) if mrows else ("—", 0)
    causes = Counter(r["причина_выбытия"] or "не указано" for r in rows)
    lines = [
        "# Что видно в данных\n",
        "> Файл генерируется `scripts/analyze.py`. Руками не править.\n",
        f"Записей: **{len(rows)}**. С указанием населённого пункта: "
        f"**{placed}** ({placed*100//len(rows)} %).\n",
        "## Написания названий\n",
        f"Различных написаний населённых пунктов — **{len(pcount)}**. "
        f"После объединения по звучанию остаётся **{len(groups)}** гнёзд: "
        f"**{len(nests)}** из них собраны из двух и более написаний, "
        f"**{len(multi)}** — из трёх и более.\n",
        "Самые разбросанные:\n",
        "| Гнездо | Вариантов | Записей | Написания |",
        "|---|---|---|---|",
    ]
    for n in nests[:8]:
        lines.append(f"| {n[0]} | {n[1]} | {n[2]} | {n[4]} |")
    lines += [
        "",
        f"Фамилий: **{len(set(r['фамилия'] for r in rows))}** написаний, "
        f"из них **{len(snests)}** гнёзд собраны из нескольких вариантов.\n",
        "## Хронология\n",
        f"Пик выбытия — **{peak[0]}** ({peak[1]} записей).\n",
        "| Год | Записей |", "|---|---|",
    ]
    years = Counter(r["дата_события"][:4] for r in rows if r["дата_события"])
    for y, n in sorted(years.items()):
        lines.append(f"| {y} | {n} |")
    lines += ["", "## Причины выбытия\n", "| Причина | Записей | Доля |", "|---|---|---|"]
    for c, n in causes.most_common():
        lines.append(f"| {c} | {n} | {n*100/len(rows):.1f} % |")
    lines += [
        "",
        f"## Части\n\nСоединений — **{len(units)}** на {len(rows)} человек, "
        f"в среднем {len(rows)/len(units):.1f} уроженца уезда на часть.\n",
        "| Часть | Уроженцев |", "|---|---|",
    ]
    for u, n in units.most_common(10):
        lines.append(f"| {u} | {n} |")
    (OUT / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
