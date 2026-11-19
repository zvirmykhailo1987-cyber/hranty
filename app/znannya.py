"""Імпорт «Знань» (статті й брифи редакції, статті каналу) з пакета JSON, який збирає ПК (deploy/radar_sync.py).
Формат: [{slug, typ, title, dek, body_md, dzherela:[{title,url,date,reliability}], tags, data, dzherelo, url, access}].
Ідемпотентно: оновлення за slug; нове знання → подія стрічки «Що змінилось».
CLI:  python -m app.znannya --fayl ШЛЯХ
"""
import json
import sys
from pathlib import Path

from sqlmodel import Session, select

from .db import engine, init_db
from .models import Znannya, utcnow
from .services import rozbir
from .services.zhyvyi import podiya

TYPY = {"stattya": "Стаття", "bryf": "Бриф", "stattya_kanalu": "Стаття каналу"}


def importuvaty(zapysy: list[dict]) -> dict:
    init_db()
    r = {"novi": 0, "onovleno": 0, "bez_zmin": 0}
    with Session(engine) as s:
        for z in zapysy:
            slug = (z.get("slug") or "").strip()[:200]
            if not slug or not z.get("title"):
                continue
            polya = {"typ": z.get("typ") if z.get("typ") in TYPY else "stattya", "title": z["title"][:500],
                     "dek": (z.get("dek") or "")[:2000], "body_md": z.get("body_md") or "",
                     "dzherela_json": json.dumps(z.get("dzherela") or [], ensure_ascii=False),
                     "tags": (z.get("tags") or "")[:500], "data": rozbir.data(z.get("data")),
                     "dzherelo": (z.get("dzherelo") or "")[:100], "url": (z.get("url") or "")[:500],
                     "access": z.get("access") or "internal"}
            zn = s.exec(select(Znannya).where(Znannya.slug == slug)).first()
            if not zn:
                s.add(Znannya(slug=slug, created_by="імпорт знань", **polya))
                podiya(s, f"znannya:{slug}", "nove_znannya", f"{TYPY[polya['typ']]}: {polya['title']}", 1, znannya_slug=slug)
                r["novi"] += 1
                continue
            zminy = {k: v for k, v in polya.items() if getattr(zn, k) != v}
            if zminy:
                for k, v in zminy.items():
                    setattr(zn, k, v)
                zn.updated_at = utcnow()
                s.add(zn)
                r["onovleno"] += 1
            else:
                r["bez_zmin"] += 1
        s.commit()
    return r


if __name__ == "__main__":
    f = Path(sys.argv[sys.argv.index("--fayl") + 1])
    print("знання:", importuvaty(json.loads(f.read_text(encoding="utf-8"))))
