"""Sign-off over real HTTP with individual accounts: the author can never approve
their own document, a role without the right gets 403, someone else may approve."""
from sqlmodel import select

from app.models import Application, Document, Opportunity, Potreba


def _zayavka(s):
    p = Potreba(nazva="Demo request", klient="Demo client")
    o = Opportunity(title="Demo programme", url="https://programme.example/1")
    s.add(p); s.add(o); s.commit()
    a = Application(potreba_id=p.id, opportunity_id=o.id, stadiya="dokumenty")
    s.add(a); s.commit(); s.refresh(a)
    return a


def _zavantazhyty(klient, aid):
    r = klient.post(f"/zayavky/{aid}/dokument", data={"vyd": "kontseptsiya", "nazva": "Concept"},
                    files={"fayl": ("concept.txt", b"synthetic concept text", "text/plain")},
                    follow_redirects=False)
    assert r.status_code == 303
    return r


def test_author_cannot_approve_own_document(s, bohdan):
    a = _zayavka(s)
    _zavantazhyty(bohdan, a.id)                      # bohdan has the approve right AND is the author
    d = s.exec(select(Document).where(Document.application_id == a.id)).one()
    r = bohdan.post(f"/dokumenty/{d.id}/pohodyty", follow_redirects=False)
    assert r.status_code == 403
    s.refresh(d)
    assert not d.pohodzheno_kym and d.pohodzheno_at is None


def test_role_without_approve_right_gets_403(s, marko, oksana):
    a = _zayavka(s)
    _zavantazhyty(marko, a.id)
    d = s.exec(select(Document).where(Document.application_id == a.id)).one()
    assert oksana.post(f"/dokumenty/{d.id}/pohodyty", follow_redirects=False).status_code == 403


def test_another_person_with_the_right_approves(s, marko, bohdan):
    a = _zayavka(s)
    _zavantazhyty(marko, a.id)
    d = s.exec(select(Document).where(Document.application_id == a.id)).one()
    r = bohdan.post(f"/dokumenty/{d.id}/pohodyty", follow_redirects=False)
    assert r.status_code == 303
    s.refresh(d)
    assert d.pohodzheno_kym_id is not None and d.pohodzheno_kym_id != d.avtor_id


def test_new_version_cannot_join_another_applications_group(s, marko):
    a1, a2 = _zayavka(s), _zayavka(s)
    _zavantazhyty(marko, a1.id)
    d1 = s.exec(select(Document).where(Document.application_id == a1.id)).one()
    marko.post(f"/zayavky/{a2.id}/dokument", data={"group_uid": d1.group_uid, "nazva": "x"},
               files={"fayl": ("x.txt", b"other", "text/plain")}, follow_redirects=False)
    d2 = s.exec(select(Document).where(Document.application_id == a2.id)).one()
    assert d2.versiya == 1
