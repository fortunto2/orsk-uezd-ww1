#!/usr/bin/env python3
"""Справочник населённых пунктов территории бывшего Орского уезда.

Зачем: гнёзда написаний (см. spelling-variants.md) отвечают на вопрос «похожи
ли две строки». Справочник отвечает на другой, более сильный: «какому реальному
селу соответствует эта запись». Он же служит независимым арбитром при проверке
качества гнёзд — два написания, привязанные к одному объекту OSM, заведомо
одно село, и на этом строится эталонная выборка.

Два источника, потому что ни один не полон: OSM хорошо знает нынешние хутора,
Wikidata — исторические и башкирские названия. Половина селений свода не
найдётся ни там, ни там: их больше нет на карте, и это содержательный факт.

    python3 scripts/gazetteer.py          # → reference/gazetteer.csv

⚠️ Лицензии источников разные и **не совпадают с лицензией репозитория**:
OSM — ODbL, Wikidata — CC0. Подробнее — reference/README.md.
"""
import csv
import io
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reference" / "gazetteer.csv"
API = "https://overpass-api.de/api/interpreter"
WDQS = "https://query.wikidata.org/sparql"

# Орский уезд занимал восток нынешней Оренбургской области (Кувандыкский,
# Гайский, Новоорский, Адамовский, Домбаровский) и юго-восток Башкортостана
# (Зианчуринский, Хайбуллинский, Зилаирский, Баймакский). Рамка взята с запасом.
BBOX = (50.6, 55.4, 53.4, 61.2)

# place=locality — это в том числе урочища на месте исчезнувших деревень, а их
# в своде немало: половина упомянутых селений на карте больше не значится.
QUERY = """
[out:json][timeout:180];
(
  node["place"~"^(city|town|village|hamlet|isolated_dwelling|locality)$"]({s},{w},{n},{e});
  way["place"~"^(city|town|village|hamlet|locality)$"]({s},{w},{n},{e});
);
out center tags;
"""


WD_QUERY = """
SELECT ?item ?ru ?ba ?tt ?coord WHERE {
  SERVICE wikibase:box {
    ?item wdt:P625 ?coord .
    bd:serviceParam wikibase:cornerSouthWest "Point(%(w)s %(s)s)"^^geo:wktLiteral .
    bd:serviceParam wikibase:cornerNorthEast "Point(%(e)s %(n)s)"^^geo:wktLiteral .
  }
  ?item wdt:P31 ?type . ?type wdt:P279* wd:Q486972 .
  OPTIONAL { ?item rdfs:label ?ru FILTER(lang(?ru)="ru") }
  OPTIONAL { ?item rdfs:label ?ba FILTER(lang(?ba)="ba") }
  OPTIONAL { ?item rdfs:label ?tt FILTER(lang(?tt)="tt") }
}
"""
UA = "orsk-uezd-ww1 gazetteer (github.com/fortunto2/orsk-uezd-ww1)"


def fetch_wikidata():
    """Wikidata знает исчезнувшие сёла и башкирские названия, которых нет в OSM."""
    import urllib.parse
    q = WD_QUERY % {"s": BBOX[0], "w": BBOX[1], "n": BBOX[2], "e": BBOX[3]}
    url = WDQS + "?" + urllib.parse.urlencode({"query": q})
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/sparql-results+json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        data = json.load(r)
    out = []
    for b in data["results"]["bindings"]:
        name = b.get("ru", {}).get("value") or b.get("ba", {}).get("value")
        if not name:
            continue
        pt = b["coord"]["value"]  # Point(lon lat)
        try:
            lon, lat = (float(x) for x in pt[pt.index("(") + 1:pt.index(")")].split())
        except ValueError:
            continue
        out.append([name, b.get("ba", {}).get("value", ""), b.get("tt", {}).get("value", ""),
                    "", "", "wikidata", f"{lat:.5f}", f"{lon:.5f}",
                    b["item"]["value"].rsplit("/", 1)[-1], "wikidata"])
    return out


def fetch():
    q = QUERY.format(s=BBOX[0], w=BBOX[1], n=BBOX[2], e=BBOX[3])
    req = urllib.request.Request(API, data=q.encode(), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def main():
    print(f"Overpass: place-объекты в рамке {BBOX} …")
    data = fetch()
    rows, seen = [], set()
    for el in data.get("elements", []):
        t = el.get("tags", {})
        name = (t.get("name") or "").strip()
        if not name:
            continue
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if lat is None:
            continue
        key = (name, round(lat, 3), round(lon, 3))
        if key in seen:
            continue
        seen.add(key)
        rows.append([
            name, t.get("name:ba", ""), t.get("name:tt", ""),
            t.get("old_name", ""), t.get("alt_name", ""),
            t.get("place", ""), f"{lat:.5f}", f"{lon:.5f}",
            f"{el['type']}/{el['id']}", "osm",
        ])
    print(f"  OSM: {len(rows)}")

    print("Wikidata: населённые пункты в той же рамке …")
    wd = fetch_wikidata()
    added = 0
    for r in wd:
        key = (r[0], round(float(r[6]), 3), round(float(r[7]), 3))
        if key in seen:
            continue
        seen.add(key)
        rows.append(r)
        added += 1
    print(f"  Wikidata: {added} новых (всего получено {len(wd)})")
    rows.sort(key=lambda r: r[0])
    OUT.parent.mkdir(exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["название", "название_ba", "название_tt", "старое_название",
                    "альт_название", "тип", "широта", "долгота", "id", "источник"])
        w.writerows(rows)
    kinds = {}
    for r in rows:
        kinds[r[5]] = kinds.get(r[5], 0) + 1
    print(f"\n  {OUT.relative_to(ROOT)} — {len(rows)} объектов")
    print("  " + " · ".join(f"{k}: {v}" for k, v in sorted(kinds.items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    sys.exit(main())
