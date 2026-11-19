"""Ядро етапу A: ролі, словники, розбір, імпорт, підбір, гейти, файли, API."""
import json
from datetime import timedelta

import pytest
from sqlmodel import select

from app import config
from app.models import CAP_ADMIN, Application, Document, Match, Opportunity, Potreba, User, utcnow
from app.models.people import CAP_APPROVE, CAP_SEND
from app.services import docstore, gates, ingest, matching, rozbir

SOHODNI = utcnow().date()


# ── ролі ──────────────────────────────────────────────────────────────────────
def test_komanda_i_prava(s):
    users = {u.login: u for u in s.exec(select(User))}
    assert set(users) == {"marko", "bohdan", "oksana", "halyna"}
    # roles are enforced: approval belongs to one role, sending to two, admin to one
    assert {k for k, u in users.items() if u.can(CAP_APPROVE)} == {"bohdan"}
    assert {k for k, u in users.items() if u.can(CAP_SEND)} == {"marko", "halyna"}
    assert {k for k, u in users.items() if u.can(CAP_ADMIN)} == {"marko"}


def test_bez_vkhodu_redirect():
    from fastapi.testclient import TestClient
    from app.main import app
    r = TestClient(app).get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/login"


# ── розбір ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("tekst,ochik", [
    ("до €25 000 (партнерство двох організацій)", (None, 25000.0, "EUR")),
    ("6 000–12 000 CHF на проєкт", (6000.0, 12000.0, "CHF")),
    ("до 1 млн грн", (None, 1e6, "UAH")),
    ("немає даних", (None, None, None)),
    ("грант 20% кредиту", (None, None, None)),
])
def test_suma(tekst, ochik):
    assert rozbir.suma(tekst) == ochik


def test_tegy_na_pochatku_slova():
    assert "biometan" in rozbir.tegy_z_tekstu("Будівництво біогазової установки")
    assert "ochysni" in rozbir.tegy_z_tekstu("реконструкція очисних споруд")
    assert "voda" not in rozbir.tegy_z_tekstu("перевод коштів")          # «вод» усередині слова не рахується


def test_kurs_ne_vygadanyi(monkeypatch):
    monkeypatch.delenv("HRANTY_KURSY", raising=False)
    assert rozbir.v_eur(1000, "UAH") is None, "без налаштованого курсу гривню не перераховуємо"
    monkeypatch.setenv("HRANTY_KURSY", "UAH=0.02")
    assert rozbir.v_eur(1000, "UAH") == pytest.approx(20.0)


# ── імпорт ────────────────────────────────────────────────────────────────────
def _radar_fayl(tmp_path, zapysy):
    d = tmp_path / "_gate_2026-10-20"
    d.mkdir()
    f = d / "opportunities.json"
    f.write_text(json.dumps(zapysy, ensure_ascii=False), encoding="utf-8")
    return f


ZAPYS = {"title": "Грант на біогазові установки для МСП", "kind": "grant", "source_org": "Тест-фонд",
         "url": "https://example.org/biogas", "amount": "до €200 000", "deadline": (SOHODNI + timedelta(days=40)).isoformat(),
         "status": "open", "for_whom": "МСП, фермерські господарства", "reliability": "confirmed"}


def test_import_idempotentnyi(s, tmp_path):
    f = _radar_fayl(tmp_path, [ZAPYS, {**ZAPYS, "title": "Інше", "url": "https://example.org/other"}])
    l1 = ingest.importuvaty_radar(s, f, gejt=False)
    l2 = ingest.importuvaty_radar(s, f, gejt=False)
    assert (l1.dodano, l2.dodano) == (2, 0)
    assert len(list(s.exec(select(Opportunity)))) == 2
    o = s.exec(select(Opportunity).where(Opportunity.url == ZAPYS["url"])).first()
    assert "biometan" in o.tags_list and "biznes" in o.zayavnyky_list
    assert o.amount_max == 200000 and o.currency == "EUR"


def test_zakryte_ne_vydalyayetsya(s, tmp_path):
    f = _radar_fayl(tmp_path, [{**ZAPYS, "deadline": (SOHODNI - timedelta(days=1)).isoformat()}])
    ingest.importuvaty_radar(s, f, gejt=False)
    o = s.exec(select(Opportunity)).first()
    assert o.status == "closed" and o.active is False


def test_ruchni_tegy_ne_peretyrayutsya(s, tmp_path):
    f = _radar_fayl(tmp_path, [ZAPYS])
    ingest.importuvaty_radar(s, f, gejt=False)
    o = s.exec(select(Opportunity)).first()
    o.tags, o.tags_avto = "pkd", False
    s.add(o); s.commit()
    ingest.importuvaty_radar(s, f, gejt=False)
    s.refresh(o)
    assert o.tags == "pkd"


# ── підбір ────────────────────────────────────────────────────────────────────
def _opp(s, **kw):
    base = dict(title="Програма", kind="grant", url="", status="open", reliability="confirmed",
                deadline=SOHODNI + timedelta(days=60), active=True)
    base.update(kw)
    o = Opportunity(**base)
    s.add(o); s.commit(); s.refresh(o)
    return o


def _potreba(s, **kw):
    base = dict(nazva="Біометан", tags="biometan", zayavnyk="biznes", oblast="Тернопільська", byudzhet_eur=500000)
    base.update(kw)
    p = Potreba(**base)
    s.add(p); s.commit(); s.refresh(p)
    return p


def test_pidbir_biometan_pershyi(s):
    p = _potreba(s)
    dobre = _opp(s, title="Біогаз МСП", tags="biometan,vde", zayavnyky="biznes", geo="Україна",
                 amount_max=1_000_000, currency="EUR")
    _opp(s, title="Школи", tags="osvita", zayavnyky="hromada")
    matching.pererakhuvaty(s, p)
    zbihy = list(s.exec(select(Match).where(Match.potreba_id == p.id).order_by(Match.score.desc())))
    assert zbihy and zbihy[0].opportunity_id == dobre.id
    rb = json.loads(zbihy[0].breakdown_json)
    assert {k["kod"] for k in rb["komponenty"]} == set(matching.VAHY)
    assert all(k["prychyna"] for k in rb["komponenty"])


def test_vidsutnye_pole_neytralne_ne_nul(s):
    p = _potreba(s, byudzhet_eur=None, oblast="")
    o = _opp(s, tags="biometan", zayavnyky="biznes")
    r = matching.score(p, o)
    suma = next(k for k in r.komponenty if k.kod == "suma")
    assert suma.znach == 0.5 and suma.nemaye_danykh


def test_zhorstki_filtry(s):
    p = _potreba(s)
    zakryta = _opp(s, tags="biometan", status="closed", active=False)
    ne_toi = _opp(s, tags="biometan", zayavnyky="hromada")
    assert matching.score(p, zakryta).score == 0 and matching.score(p, zakryta).vidsiyano
    assert matching.score(p, ne_toi).vidsiyano == "не той тип заявника"


def test_bez_zbihu_napryamku_ne_vyshche_03(s):
    p = _potreba(s)
    o = _opp(s, tags="osvita", zayavnyky="biznes", geo="Україна")
    assert matching.score(p, o).score <= 0.3


def test_vidkhyleni_ne_povertayutsya(s):
    p = _potreba(s)
    _opp(s, tags="biometan", zayavnyky="biznes")
    matching.pererakhuvaty(s, p)
    m = s.exec(select(Match)).first()
    m.stan = "vidkhyleno"; s.add(m); s.commit()
    matching.pererakhuvaty(s, p)
    assert s.exec(select(Match)).first().stan == "vidkhyleno"


# ── гейти ─────────────────────────────────────────────────────────────────────
def _zayavka(s, stadiya="ideya", verdykt=""):
    p = _potreba(s)
    o = _opp(s, tags="biometan", zayavnyky="biznes", verdykt=verdykt)
    a = Application(potreba_id=p.id, opportunity_id=o.id, stadiya=stadiya)
    s.add(a); s.commit(); s.refresh(a)
    return a


def test_gejt_skryninh_potrebuye_balu_abo_prychyny(s):
    a = _zayavka(s)
    assert gates.perevirka(s, a, "skryninh")
    a.prychyna_vidboru = "клієнт уже має ТЕО"
    assert gates.perevirka(s, a, "skryninh") == []


def test_ne_mozhna_pereskochyty(s):
    a = _zayavka(s)
    assert "по черзі" in gates.perevirka(s, a, "dokumenty")[0]


def test_dokumenty_potrebuyut_verdyktu(s):
    a = _zayavka(s, "skryninh")
    assert gates.perevirka(s, a, "dokumenty")
    a2 = _zayavka(s, "skryninh", verdykt="подаватися")
    assert gates.perevirka(s, a2, "dokumenty") == []


def test_podacha_blokuye_nepohodzhenu_ai_chernetku(s):
    a = _zayavka(s, "dokumenty", verdykt="подаватися")
    for vyd in ("kontseptsiya", "byudzhet"):
        s.add(Document(application_id=a.id, vyd=vyd, nazva=vyd, ai_chernetka=(vyd == "kontseptsiya"),
                       avtor="Агент", group_uid=vyd))
    s.commit()
    prych = gates.perevirka(s, a, "podacha")
    assert any("AI-чернетка" in p for p in prych)
    for d in s.exec(select(Document)):
        d.pohodzheno_kym = "Богдан"; s.add(d)
    s.commit()
    assert gates.perevirka(s, a, "podacha") == []


def test_avtor_ne_pohodzhuye_sebe(s):
    k = s.exec(select(User).where(User.login == "bohdan")).first()
    m = s.exec(select(User).where(User.login == "marko")).first()
    assert gates.mozhe_pohodzhuvaty(k, Document(application_id=1, avtor=k.name, avtor_id=k.id))   # author
    # same display name, different account → identity is the id, not the name
    assert gates.mozhe_pohodzhuvaty(k, Document(application_id=1, avtor=k.name, avtor_id=m.id)) == ""
    assert gates.mozhe_pohodzhuvaty(m, Document(application_id=1, avtor="Оксана", avtor_id=999))   # no CAP_APPROVE
    assert gates.mozhe_pohodzhuvaty(k, Document(application_id=1, avtor="Оксана", avtor_id=999)) == ""


def test_rezultat_lyshe_pislya_podachi_lyudynoyu(s):
    a = _zayavka(s, "podacha")
    assert gates.perevirka(s, a, "rezultat")
    a.podano_at, a.podano_kto = utcnow(), "Галина"
    assert gates.perevirka(s, a, "rezultat") == []


# ── веб: шлях заявки й права ─────────────────────────────────────────────────
def test_ekrany_vidkryvayutsya(marko, s):
    p = _potreba(s)
    o = _opp(s, tags="biometan", zayavnyky="biznes")
    matching.pererakhuvaty(s, p)
    for url in ["/", "/potreby", "/potreby/nova", f"/potreby/{p.id}", "/reyestr", f"/reyestr/{o.id}",
                "/zayavky", "/biblioteka", "/reyestr?teg=biometan&status="]:
        r = marko.get(url)
        assert r.status_code == 200, url


def test_klasyfikatsiya_na_holovniy_i_kilka_napryamiv(marko, s):
    _opp(s, title="Програма біометан", tags="biometan")
    _opp(s, title="Програма школи", tags="hromada,osvita")
    _opp(s, title="Програма вода", tags="voda")
    r = marko.get("/")
    assert r.status_code == 200 and "Класифікація грантів" in r.text and 'name="teg" value="osvita"' in r.text
    r = marko.get("/reyestr?teg=osvita&teg=biometan&status=")
    assert "Програма біометан" in r.text and "Програма школи" in r.text and "Програма вода" not in r.text
    assert "teg=osvita&teg=biometan" in r.text or "Знайдено 2" in r.text


def test_shlyakh_zayavky_i_gejt_409(marko, s):
    p = _potreba(s)
    _opp(s, tags="biometan", zayavnyky="biznes", geo="Україна", amount_max=1e6, currency="EUR")
    matching.pererakhuvaty(s, p)
    m = s.exec(select(Match)).first()
    r = marko.post(f"/zbihy/{m.id}/v-robotu", follow_redirects=False)
    aid = int(r.headers["location"].rsplit("/", 1)[-1])
    assert marko.post(f"/zayavky/{aid}/stadiya", data={"nova": "skryninh"}, follow_redirects=False).status_code == 303
    r = marko.post(f"/zayavky/{aid}/stadiya", data={"nova": "dokumenty"}, follow_redirects=False)
    assert r.status_code == 409 and "вердикт" in r.text


def test_podano_bud_khto_z_komandy(halyna, s):
    a = _zayavka(s, "podacha")
    assert halyna.post(f"/zayavky/{a.id}/podano", data={"data_podachi": SOHODNI.isoformat()},
                        follow_redirects=False).status_code == 303
    s.refresh(a)
    assert a.podano_kto == "Галина"


def test_pohodzhennya_cherez_veb(bohdan, marko, s):
    a = _zayavka(s, "dokumenty")
    r = marko.post(f"/zayavky/{a.id}/dokument", data={"vyd": "byudzhet", "nazva": "Бюджет"},
                      files={"fayl": ("budget.xlsx", b"PK\x03\x04 test", "application/octet-stream")}, follow_redirects=False)
    assert r.status_code == 303
    d = s.exec(select(Document)).first()
    assert marko.post(f"/dokumenty/{d.id}/pohodyty").status_code == 403   # автор не погоджує сам себе
    assert bohdan.post(f"/dokumenty/{d.id}/pohodyty", follow_redirects=False).status_code == 303


# ── файли ─────────────────────────────────────────────────────────────────────
def test_docstore_bezpeka():
    with pytest.raises(docstore.FaylPomylka):
        docstore.zberehty(b"MZ", "virus.exe")
    with pytest.raises(docstore.FaylPomylka):
        docstore.zberehty(b"", "a.pdf")
    with pytest.raises(docstore.FaylPomylka):
        docstore.shlyakh("../../etc/passwd")
    m = docstore.zberehty(b"%PDF-1.4 test", "x.pdf", "prohramy")
    assert docstore.shlyakh(m["stored_name"]).exists() and len(m["sha256"]) == 64


# ── API ───────────────────────────────────────────────────────────────────────
def test_api_token_i_import():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    assert c.post("/api/ingest/radar", content=b"[]").status_code == 401
    r = c.post("/api/ingest/radar?data=2026-10-20", content=json.dumps([ZAPYS]).encode(),
               headers={"Authorization": f"Bearer {config.API_TOKEN}"})
    assert r.status_code == 200 and r.json()["dodano"] == 1


