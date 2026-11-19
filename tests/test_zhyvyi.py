# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Живий портал: щоденне обслуговування, стрічка змін, знання."""
from datetime import timedelta

from sqlmodel import select

from app.models import Application, Opportunity, Potreba, Task, Zmina, Znannya, utcnow
from app.services import zhyvyi
from app.znannya import importuvaty

SOHODNI = utcnow().date()


def test_obslugovuvannya_zakryttya_i_zbihy(s):
    p = Potreba(nazva="Очисні громади", tags="ochysni,voda", zayavnyk="hromada", status="aktyvnyi")
    zakryta = Opportunity(title="Програма для очисних громад", tags="ochysni,voda", zayavnyky="hromada",
                          deadline=SOHODNI - timedelta(days=1), status="open", active=True, reliability="confirmed")
    skoro = Opportunity(title="Кредити на очисні споруди громад", tags="ochysni,voda", zayavnyky="hromada",
                        deadline=SOHODNI + timedelta(days=5), status="open", active=True, reliability="confirmed")
    s.add_all([p, zakryta, skoro]); s.commit()
    a = Application(potreba_id=p.id, opportunity_id=zakryta.id, stadiya="dokumenty"); s.add(a); s.commit()
    r = zhyvyi.obslugovuvannya(s, SOHODNI)
    assert r["zakryto"] == 1 and r["zakryvayetsya"] == 1 and r["zadachi"] == 1
    s.refresh(zakryta)
    assert zakryta.status == "closed" and not zakryta.active
    typy = {z.typ: z for z in s.exec(select(Zmina))}
    assert typy["prohrama_zakryta"].vazhlyvist == 3                # на закриту програму є заявка в роботі
    assert "zakryvayetsya" in typy and "novyi_zbih" in typy
    assert s.exec(select(Task).where(Task.application_id == a.id)).first()
    assert zhyvyi.vzhe_sohodni(s, SOHODNI)
    n = len(s.exec(select(Zmina)).all())
    zhyvyi.obslugovuvannya(s, SOHODNI)                            # повтор — без дублів подій і задач
    assert len(s.exec(select(Zmina)).all()) == n and len(s.exec(select(Task)).all()) == 1


def test_znannya_import_i_storinky(s, marko):
    paket = [{"slug": "test-bryf", "typ": "bryf", "title": "Пульс дня", "dek": "Коротко про головне",
              "body_md": "## Розділ\n\nТекст із фактом [1].", "dzherela": [{"title": "Джерело", "url": "https://example.org",
              "date": "2026-10-22", "reliability": "confirmed"}], "data": "2026-10-22"}]
    assert importuvaty(paket)["novi"] == 1
    assert importuvaty(paket) == {"novi": 0, "onovleno": 0, "bez_zmin": 1}
    s.expire_all()
    assert s.exec(select(Znannya)).one().typ == "bryf"
    assert s.exec(select(Zmina).where(Zmina.typ == "nove_znannya")).one()
    assert "Пульс дня" in marko.get("/znannya").text
    html = marko.get("/znannya/test-bryf").text
    assert "<h2>Розділ</h2>" in html and "example.org" in html
    assert "Пульс дня" in marko.get("/znannya?q=головне").text
    holovna = marko.get("/").text
    assert "Що змінилось" in holovna and "Нове в знаннях" in holovna
