import json
import subprocess
import sys
import time

UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/152"
ENT=["chelovek_donesenie","chelovek_gospital","chelovek_zahoronenie","chelovek_plen",
     "chelovek_nagrazhdenie","chelovek_predstavlenie","chelovek_nagradnaya_kartochka",
     "commander","person","chelovek_posluzhnoi_spisok","chelovek_uchetnaya_kartochka"]
FIELDS=["ids","last_name","first_name","middle_name","birth_place","birth_place_gubernia",
 "birth_place_uezd","birth_place_volost","location","birth_date","rank","data_vibitiya",
 "event_name","event_id","military_unit_name","event_place","lazaret_name","camp_name",
 "date_death","award_name","nomer_dokumenta","data_dokumenta","data_i_mesto_priziva",
 "archive_short","nomer_fonda","nomer_opisi","nomer_dela","date_birth","data_vibitiya_end"]
def query(**kw):
    qf={f:"" for f in FIELDS}; qf.update(kw)
    out=[]; frm=0; total=None
    while True:
        body={"indices":["gwar"],"entities":ENT,"queryFields":qf,"filterFields":{},
              "from":frm,"size":"500","builderType":"Heroes"}
        p=subprocess.run(["curl","-sSk","--max-time","120","-A",UA,
            "-H","Content-Type: application/json","-H","Referer: https://gwar.mil.ru/heroes/",
            "-X","POST","https://gwar.mil.ru/gt_data/?builder=Heroes","--data-binary",json.dumps(body,ensure_ascii=False)],
            capture_output=True, check=False)
        try: d=json.loads(p.stdout.decode())
        except Exception: print("  ! парс не удался на from=",frm); break
        h=d.get("hits",{}); total=h.get("total")
        got=h.get("hits",[])
        out+= [x["_source"] for x in got]
        frm+=len(got)
        if not got or frm>=total or frm>=10000: break
        time.sleep(0.5)
    return total,out
if __name__=="__main__":
    key=sys.argv[1]; val=sys.argv[2]; tag=sys.argv[3]
    t,rows=query(**{key:val})
    print(f"{key}={val} -> total {t}, собрано {len(rows)}")
    with open(f"/tmp/gt_{tag}.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)
