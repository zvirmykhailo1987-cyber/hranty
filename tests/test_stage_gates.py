# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Application stage gates, end to end: what it takes to ENTER each stage, who may sign off,
and that only a person with the right records a submission and its result."""
from datetime import timedelta

from sqlmodel import select

from app.models import Application, AuditEntry, Document, Match, Opportunity, Potreba, Task, User, utcnow
from app.services import gates

SOHODNI = utcnow().date()


def _zayavka(s, stadiya="ideya", verdykt="", o_verdykt=""):
    p = Potreba(nazva="Biogas 1 MW", tags="biometan", zayavnyk="biznes")
    o = Opportunity(title="Biogas call", url="https://funder.example/c", verdykt=o_verdykt,
                    deadline=SOHODNI + timedelta(days=60))
    s.add(p); s.add(o); s.commit()
    a = Application(potreba_id=p.id, opportunity_id=o.id, stadiya=stadiya, verdykt=verdykt)
    s.add(a); s.commit(); s.refresh(a)
    return a


def _dok(s, a, vyd, versiya=1, group=None, pohodzheno=True, **kw):
    d = Document(application_id=a.id, vyd=vyd, nazva=vyd, versiya=versiya, group_uid=group or vyd,
                 pohodzheno_kym="Bohdan" if pohodzheno else "", **kw)
    s.add(d); s.commit(); s.refresh(d)
    return d


def _user(s, login):
    return s.exec(select(User).where(User.login == login)).one()


# ── gate rules ───────────────────────────────────────────────────────────────

def test_unknown_stage_is_refused(s):
    a = _zayavka(s)
    assert gates.perevirka(s, a, "nowhere") == ["невідома стадія «nowhere»"]


def test_moving_back_or_staying_has_no_gate(s):
    a = _zayavka(s, "podacha")
    assert gates.perevirka(s, a, "ideya") == [] and gates.perevirka(s, a, "podacha") == []


def test_screening_passes_on_a_strong_match_score_and_fails_on_a_weak_one(s):
    a = _zayavka(s)
    m = Match(potreba_id=a.potreba_id, opportunity_id=a.opportunity_id, score=0.49)
    s.add(m); s.commit()
    assert gates.perevirka(s, a, "skryninh") == ["бал збігу нижче 0.5 — вкажіть, чому беремо в роботу"]
    m.score = 0.5; s.add(m); s.commit()
    assert gates.perevirka(s, a, "skryninh") == []


def test_documents_stage_needs_the_verdict_apply_from_either_source(s):
    assert gates.perevirka(s, _zayavka(s, "skryninh", verdykt="уточнити"), "dokumenty")
    assert gates.perevirka(s, _zayavka(s, "skryninh", o_verdykt="не подаватися"), "dokumenty")
    assert gates.perevirka(s, _zayavka(s, "skryninh", o_verdykt="подаватися"), "dokumenty") == []
    # the application's own verdict wins over the programme's
    assert gates.perevirka(s, _zayavka(s, "skryninh", verdykt="уточнити", o_verdykt="подаватися"), "dokumenty")
    bez_prohramy = Application(potreba_id=None, opportunity_id=None, stadiya="skryninh")
    s.add(bez_prohramy); s.commit()
    assert gates.perevirka(s, bez_prohramy, "dokumenty")


def test_submission_with_no_documents_names_every_gap(s):
    a = _zayavka(s, "dokumenty")
    prych = gates.perevirka(s, a, "podacha")
    assert "бракує документа: Концепція / опис проєкту" in prych
    assert "бракує документа: Бюджет" in prych
    assert "у заявці немає жодного документа" in prych


def test_submission_needs_every_required_document(s):
    a = _zayavka(s, "dokumenty")
    _dok(s, a, "kontseptsiya")
    assert gates.perevirka(s, a, "podacha") == ["бракує документа: Бюджет"]
    _dok(s, a, "byudzhet")
    assert gates.perevirka(s, a, "podacha") == []


def test_only_the_latest_version_counts_for_signoff(s):
    a = _zayavka(s, "dokumenty")
    _dok(s, a, "kontseptsiya")
    _dok(s, a, "byudzhet", versiya=1, group="b")
    _dok(s, a, "byudzhet", versiya=2, group="b", pohodzheno=False)
    assert gates.perevirka(s, a, "podacha") == ["не погоджено: byudzhet"]
    _dok(s, a, "byudzhet", versiya=3, group="b")                    # a signed newer version clears it
    assert gates.perevirka(s, a, "podacha") == []


def test_result_needs_a_person_who_recorded_the_submission_and_holds_the_right(s):
    a = _zayavka(s, "podacha")
    _dok(s, a, "kontseptsiya")
    a.podano_at = utcnow()                                           # a date alone is not enough
    assert "позначте, хто й коли подав заявку (подає лише людина)" in gates.perevirka(s, a, "rezultat")
    a.podano_kto = "Halyna"
    assert gates.perevirka(s, a, "rezultat") == []
    assert gates.perevirka(s, a, "rezultat", _user(s, "oksana")) == ["немає права позначати подачу"]
    assert gates.perevirka(s, a, "rezultat", _user(s, "halyna")) == []


def test_signoff_by_name_for_rows_without_an_author_id(s):
    bohdan = _user(s, "bohdan")
    assert gates.mozhe_pohodzhuvaty(bohdan, Document(avtor=bohdan.name, avtor_id=None)) == \
        "автор не може погодити власний документ"
    assert gates.mozhe_pohodzhuvaty(bohdan, Document(avtor="Someone else", avtor_id=None)) == ""


# ── the same rules over HTTP ─────────────────────────────────────────────────

def test_full_path_from_idea_to_result_over_http(s, marko, bohdan, halyna):
    """Every gate passed by the person who is allowed to pass it, nothing skipped."""
    a = _zayavka(s)
    aid = a.id
    # idea → screening: a weak score needs a stated reason
    r = marko.post(f"/zayavky/{aid}/stadiya", data={"nova": "skryninh"}, follow_redirects=False)
    assert r.status_code == 409
    r = marko.post(f"/zayavky/{aid}/stadiya", data={"nova": "skryninh", "prychyna_vidboru": " client has a study "},
                   follow_redirects=False)
    assert r.status_code == 303
    # screening → documents: the verdict is recorded by someone with the data right
    assert marko.post(f"/reyestr/{a.opportunity_id}/verdykt", data={"verdykt": "подаватися", "prymitka": "fits"},
                      follow_redirects=False).status_code == 303
    assert marko.post(f"/zayavky/{aid}/stadiya", data={"nova": "dokumenty"}, follow_redirects=False).status_code == 303
    # documents → submission: both required documents, uploaded by Halyna and signed off by Bohdan
    for vyd in ("kontseptsiya", "byudzhet"):
        assert halyna.post(f"/zayavky/{aid}/dokument", data={"vyd": vyd, "nazva": vyd},
                           files={"fayl": (f"{vyd}.txt", b"synthetic", "text/plain")},
                           follow_redirects=False).status_code == 303
    assert marko.post(f"/zayavky/{aid}/stadiya", data={"nova": "podacha"}, follow_redirects=False).status_code == 409
    for d in s.exec(select(Document).where(Document.application_id == aid)):
        assert bohdan.post(f"/dokumenty/{d.id}/pohodyty", follow_redirects=False).status_code == 303
    assert marko.post(f"/zayavky/{aid}/stadiya", data={"nova": "podacha"}, follow_redirects=False).status_code == 303
    # submission → result: only after a person marked who submitted and when
    assert halyna.post(f"/zayavky/{aid}/stadiya", data={"nova": "rezultat"}, follow_redirects=False).status_code == 409
    assert halyna.post(f"/zayavky/{aid}/podano", data={"data_podachi": SOHODNI.isoformat()},
                       follow_redirects=False).status_code == 303
    assert halyna.post(f"/zayavky/{aid}/stadiya", data={"nova": "rezultat"}, follow_redirects=False).status_code == 303
    assert halyna.post(f"/zayavky/{aid}/rezultat", data={"rezultat": "vyhrano"}, follow_redirects=False).status_code == 303
    s.expire_all()
    a = s.get(Application, aid)
    assert (a.stadiya, a.rezultat, a.podano_kto, a.prychyna_vidboru) == ("rezultat", "vyhrano", "Галина", "client has a study")
    assert a.podano_at.date() == SOHODNI
    dii = [e.action for e in s.exec(select(AuditEntry).order_by(AuditEntry.id))]
    assert dii.count("application.stage") == 4 and "application.submitted_mark" in dii and "application.result" in dii
    assert s.get(Opportunity, a.opportunity_id).notes.endswith("скринінг: fits")
    assert marko.get(f"/zayavky/{aid}").status_code == 200


def test_result_is_refused_before_the_result_stage_and_for_unknown_values(s, halyna):
    a = _zayavka(s, "podacha")
    assert halyna.post(f"/zayavky/{a.id}/rezultat", data={"rezultat": "vyhrano"}, follow_redirects=False).status_code == 400
    a.stadiya = "rezultat"; s.add(a); s.commit()
    assert halyna.post(f"/zayavky/{a.id}/rezultat", data={"rezultat": "maybe"}, follow_redirects=False).status_code == 400
    s.refresh(a)
    assert a.rezultat == ""


def test_submission_mark_needs_the_submission_stage_and_a_real_date(s, halyna):
    a = _zayavka(s, "dokumenty")
    assert halyna.post(f"/zayavky/{a.id}/podano", data={"data_podachi": SOHODNI.isoformat()},
                       follow_redirects=False).status_code == 400
    a.stadiya = "podacha"; s.add(a); s.commit()
    assert halyna.post(f"/zayavky/{a.id}/podano", data={"data_podachi": "someday"},
                       follow_redirects=False).status_code == 400
    s.refresh(a)
    assert a.podano_at is None and a.podano_kto == ""


def test_stage_change_on_a_missing_application_is_404(marko):
    assert marko.post("/zayavky/999999/stadiya", data={"nova": "skryninh"}, follow_redirects=False).status_code == 404
    assert marko.get("/zayavky/999999").status_code == 404


def test_new_document_version_inherits_kind_and_number(s, marko):
    a = _zayavka(s, "dokumenty")
    marko.post(f"/zayavky/{a.id}/dokument", data={"vyd": "byudzhet", "nazva": "Budget"},
               files={"fayl": ("b.txt", b"v1", "text/plain")})
    v1 = s.exec(select(Document).where(Document.application_id == a.id)).one()
    marko.post(f"/zayavky/{a.id}/dokument", data={"vyd": "", "group_uid": v1.group_uid, "nazva": "Budget"},
               files={"fayl": ("b.txt", b"v2", "text/plain")})
    v2 = s.exec(select(Document).where(Document.application_id == a.id, Document.versiya == 2)).one()
    assert (v2.vyd, v2.group_uid, v2.avtor_id) == ("byudzhet", v1.group_uid, _user(s, "marko").id)
    assert gates.aktualni_dokumenty(s, a.id) == [v2]


def test_document_with_a_forbidden_type_is_400(s, marko):
    a = _zayavka(s, "dokumenty")
    r = marko.post(f"/zayavky/{a.id}/dokument", data={"nazva": "x"},
                   files={"fayl": ("x.exe", b"MZ", "application/octet-stream")}, follow_redirects=False)
    assert r.status_code == 400
    assert marko.post("/zayavky/999999/dokument", data={"nazva": "x"},
                      files={"fayl": ("x.txt", b"x", "text/plain")}, follow_redirects=False).status_code == 404


def test_document_download_is_an_attachment_with_a_safe_name(s, marko):
    a = _zayavka(s, "dokumenty")
    marko.post(f"/zayavky/{a.id}/dokument", data={"vyd": "byudzhet", "nazva": 'Budget: "final"/v2'},
               files={"fayl": ("b.txt", b"numbers", "text/plain")})
    d = s.exec(select(Document).where(Document.application_id == a.id)).one()
    r = marko.get(f"/dokumenty/{d.id}/fayl")
    assert r.status_code == 200 and r.content == b"numbers"
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment") and "Budget-%20-final-v2.txt" in cd
    assert marko.get("/dokumenty/999999/fayl").status_code == 404
    assert marko.post("/dokumenty/999999/pohodyty", follow_redirects=False).status_code in (403, 404)


def test_next_step_owner_must_exist(s, marko):
    a = _zayavka(s)
    assert marko.post(f"/zayavky/{a.id}/krok", data={"krok": "call", "owner_id": "999999"},
                      follow_redirects=False).status_code == 400
    halyna = _user(s, "halyna")
    r = marko.post(f"/zayavky/{a.id}/krok", data={"krok": " call the funder ", "do": SOHODNI.isoformat(),
                                                  "owner_id": str(halyna.id)}, follow_redirects=False)
    assert r.status_code == 303
    s.refresh(a)
    assert (a.nastupnyi_krok, a.nastupnyi_krok_do, a.owner_id) == ("call the funder", SOHODNI, halyna.id)
    assert marko.post("/zayavky/999999/krok", data={"krok": "x"}, follow_redirects=False).status_code == 404


def test_archive_toggles_and_hides_from_the_board(s, marko):
    a = _zayavka(s)
    r = marko.post(f"/zayavky/{a.id}/arkhiv", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/zayavky"
    s.refresh(a)
    assert a.arkhiv
    assert f"/zayavky/{a.id}\"" not in marko.get("/zayavky").text
    r = marko.post(f"/zayavky/{a.id}/arkhiv", follow_redirects=False)
    assert r.headers["location"] == f"/zayavky/{a.id}"
    assert marko.post("/zayavky/999999/arkhiv", follow_redirects=False).status_code == 404


def test_tasks_validate_links_and_never_redirect_off_site(s, marko):
    a = _zayavka(s)
    assert marko.post("/zadachi", data={"title": "  "}, follow_redirects=False).status_code == 400
    assert marko.post("/zadachi", data={"title": "x", "assignee_id": "999999"}, follow_redirects=False).status_code == 400
    r = marko.post("/zadachi", data={"title": "Collect the statute", "application_id": str(a.id),
                                     "due": SOHODNI.isoformat(), "povernutys": "//evil.example/x"},
                   follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    t = s.exec(select(Task).where(Task.application_id == a.id)).one()
    assert (t.title, t.due, t.dzherelo) == ("Collect the statute", SOHODNI, "ручна")
    r = marko.post(f"/zadachi/{t.id}/hotovo", data={"povernutys": f"/zayavky/{a.id}"}, follow_redirects=False)
    assert r.headers["location"] == f"/zayavky/{a.id}"
    s.refresh(t)
    assert t.done
    assert marko.post("/zadachi/999999/hotovo", follow_redirects=False).status_code == 404


def test_a_role_without_the_send_right_cannot_record_a_submission(s, oksana):
    a = _zayavka(s, "podacha")
    _dok(s, a, "kontseptsiya"); _dok(s, a, "byudzhet")
    r = oksana.post(f"/zayavky/{a.id}/podano", data={"data_podachi": SOHODNI.isoformat()}, follow_redirects=False)
    assert r.status_code == 403
    s.refresh(a)
    assert a.podano_at is None and a.podano_kto == ""


def test_the_irreversible_and_sign_off_routes_keep_their_capability():
    """Pinned on purpose: the generated boundary sweep follows the code, so it cannot notice a
    capability that was removed from a route. This list can."""
    from tests.test_mezhi_dostupu import ROUTES, _caps_of
    caps = {(r.path, m): _caps_of(r) for r, m in ROUTES}
    assert caps[("/zayavky/{aid}/podano", "POST")] == {"send"}
    assert caps[("/zayavky/{aid}/rezultat", "POST")] == {"send"}
    assert caps[("/dokumenty/{did}/pohodyty", "POST")] == {"approve"}
    assert caps[("/zayavky/{aid}/arkhiv", "POST")] == {"delete"}
    for path in ("/reyestr/{oid}/verdykt", "/reyestr/{oid}/klasyfikatsiya", "/biblioteka"):
        assert caps[(path, "POST")] == {"data"}, path
