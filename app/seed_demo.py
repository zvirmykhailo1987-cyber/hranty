"""Synthetic demo data for the Grants portal.

Everything here is invented: funders, programmes, clients, people and documents.
URLs point to ``*.example`` (RFC 2606), tax codes use the reserved ``99`` prefix,
phones are not generated at all. The generator is deterministic (fixed seed) and
idempotent: running it twice leaves the database unchanged.

    python -m app.seed_demo            # fill an empty database
    python -m app.seed_demo --reset    # wipe the database file first (demo/dev only)
"""
from __future__ import annotations

import json
import random
import sys
from datetime import timedelta

from sqlmodel import Session, func, select

from . import config
from .models import (Application, Document, Funder, Opportunity, Potreba, ProgramFile, Task, User,
                     Znannya, utcnow)
from .security import hash_pw

SEED = 2026

# Demo accounts — individual logins, each with its own password (documented in DEMO.md).
DEMO_USERS = {
    "marko": ("Marko Avenir", "rozrobnyk"),
    "bohdan": ("Bohdan Quillan", "perehovorschyk_dok"),
    "oksana": ("Oksana Linvel", "perehovory_kontakt"),
    "halyna": ("Halyna Brookfeld", "zapyty_lysty"),
}


def demo_password(login: str) -> str:
    return f"{login}-demo-2026"


FUNDERS = [
    ("Northbridge Climate Fund", "fund", "NL"),
    ("Danube Municipal Facility", "DFI", "AT"),
    ("Carpathian Rural Trust", "fund", "PL"),
    ("Baltic Water Partnership", "EU", "LT"),
    ("Riverside Development Bank", "bank", "DE"),
    ("Open Communities Foundation", "fund", "UA"),
    ("Green Transition Window", "EU", "BE"),
    ("Veterans Livelihood Alliance", "UN", "CH"),
]

# (title, kind, amount, for_whom, notes, funder index, deadline offset days or None, reliability)
PROGRAMMES = [
    ("Biogas and biomethane pilots for farms", "grant", "up to €400 000", "SME, farmer cooperatives",
     "Anaerobic digestion of manure and agri residues; digestate use plan required.", 0, 45, "confirmed"),
    ("Municipal wastewater rehabilitation window", "loan", "€1 000 000 – 8 000 000", "municipal utilities",
     "Sewage treatment plants and wastewater networks; 20% co-financing.", 1, 90, "confirmed"),
    ("Project preparation facility (feasibility studies)", "ta", "up to €250 000", "municipal",
     "Feasibility study, design documentation and project preparation for infrastructure.", 1, None, "confirmed"),
    ("Small community water supply grants", "grant", "up to €150 000", "municipal, utility",
     "Water supply wells, pumping stations and pipes for rural communities.", 3, 21, "plausible"),
    ("Waste sorting stations for hromadas", "grant", "€50 000 – 300 000", "municipal",
     "Waste collection, sorting lines and recycling points.", 6, 12, "confirmed"),
    ("District heating modernisation loans", "loan", "€2 000 000 – 15 000 000", "municipal utilities",
     "Boiler houses, heat networks and heat pumps for district heating.", 4, 120, "confirmed"),
    ("Energy efficiency in public buildings", "grant", "up to €600 000", "municipal",
     "Thermal modernisation and insulation of schools and clinics; energy efficiency audit.", 6, 60, "plausible"),
    ("Rooftop solar for SMEs", "blended", "€30 000 – 500 000", "SME",
     "Solar PV and storage for small and medium business; grant plus partner-bank loan.", 4, None, "plausible"),
    ("Agri processing equipment co-financing", "grant", "up to €120 000", "farmer, SME",
     "Processing equipment for fruit, vegetables and grain; 30% own contribution.", 2, 30, "confirmed"),
    ("Veteran-owned business start grants", "grant", "up to €25 000", "SME, veterans",
     "Start-up grants for veteran-owned micro business.", 7, 18, "confirmed"),
    ("Capacity building for civil society organisations", "grant", "up to €40 000", "NGO",
     "Organisational development and capacity building for CSOs.", 5, 40, "plausible"),
    ("School shelters and education spaces", "grant", "up to €200 000", "municipal",
     "Education infrastructure: shelters, classrooms, inclusive spaces.", 5, -10, "confirmed"),
    ("Biomethane grid injection guarantee", "guarantee", "up to €5 000 000", "SME",
     "Partial credit guarantee for biomethane plants connected to the gas grid.", 4, 75, "unverified"),
    ("Circular economy innovation call", "call", "€100 000 – 750 000", "SME, research",
     "Waste recycling and circular economy innovation; consortium with a university encouraged.", 6, 8, "confirmed"),
    ("Rural heat pumps pilot", "grant", "up to €90 000", "municipal",
     "Heat pump pilots for rural schools and kindergartens.", 0, 55, "plausible"),
    ("Water utility equity window", "equity", "€3 000 000 – 20 000 000", "utility",
     "Equity for regional water utilities with a reform plan.", 1, None, "unverified"),
]

CLIENTS = [
    ("Biogas plant 1 MW for a dairy farm", "Green Meadow Dairy LLC", "biznes", "Ternopilska", 1_200_000,
     "Manure from 1 800 cows plus maize silage; anaerobic digestion with CHP and digestate fields."),
    ("Wastewater treatment plant for Zelenyi Hai", "Zelenyi Hai community", "hromada", "Lvivska", 6_500_000,
     "Rehabilitation of the sewage treatment plant (6 000 inhabitants) and 4 km of wastewater networks."),
    ("Waste sorting line for Sosnivka", "Sosnivka community", "hromada", "Rivnenska", 280_000,
     "Waste sorting station with a baler; recycling of plastic and paper."),
    ("Rooftop solar for a bakery", "Kalyna Bakery LLC", "biznes", "Ternopilska", 140_000,
     "Solar PV 150 kW on the workshop roof to cut energy costs."),
    ("Feasibility study: district heating", "Bilyi Kamin community", "hromada", "Khmelnytska", 220_000,
     "Feasibility study and design documentation for district heating with a biomass boiler."),
    ("Fruit processing equipment", "Orchard Valley Farm", "fermer", "Vinnytska", 95_000,
     "Processing line for apples: washing, drying and packing; agri SME."),
]

ARTICLES = [
    ("how-matching-works", "stattya", "How grant matching is scored",
     "Six explainable components, each with a reason. Unknown is shown as no data, never zero.",
     "Each client request is matched against every open programme.\n\n"
     "| Component | Weight |\n|---|---|\n| Sector tags | 0.35 |\n| Who may apply | 0.20 |\n"
     "| Amount | 0.15 |\n| Deadline | 0.15 |\n| Geography | 0.10 |\n| Reliability | 0.05 |\n\n"
     "A programme that excludes the applicant type is filtered out regardless of the total."),
    ("co-financing-basics", "bryf", "Co-financing: what funders usually expect",
     "Typical own-contribution ranges and how to evidence them.",
     "Most infrastructure windows expect **10–30%** co-financing. Evidence: council decision, "
     "budget line or a bank letter. In-kind contributions must be valued in the budget."),
    ("deadline-types", "bryf", "Hard, rolling and staged deadlines",
     "Why the deadline type matters more than the date.",
     "*Hard* deadlines close the call. *Rolling* calls review on arrival — earlier is better. "
     "*Staged* calls have a concept note first and a full proposal later."),
]


def _users(s: Session) -> dict[str, User]:
    out: dict[str, User] = {}
    for login, (name, role) in DEMO_USERS.items():
        u = s.exec(select(User).where(User.login == login)).first()
        if not u:
            u = User(name=name, login=login, role=role)
        u.name, u.role, u.active = name, role, True
        if not u.password_hash:
            u.password_hash = hash_pw(demo_password(login))
        s.add(u)
        out[login] = u
    s.commit()
    for u in out.values():
        s.refresh(u)
    return out


def seed(s: Session) -> dict:
    from .services import ingest, matching, zhyvyi

    if config.IS_PROD:
        raise RuntimeError("seed_demo refuses to run in production")
    rnd = random.Random(SEED)  # noqa: S311 — deterministic demo data, not security  # nosec B311
    today = utcnow().date()
    # A database that already holds data is never touched — not even its accounts.
    if (s.exec(select(Potreba)).first() or s.exec(select(Opportunity)).first()
            or s.exec(select(User).where(func.length(User.password_hash) > 0)).first()):
        return {"skipped": True}
    users = _users(s)
    made = {"opportunities": 0, "requests": 0, "applications": 0}

    for name, typ, country in FUNDERS:
        if not s.exec(select(Funder).where(Funder.name == name)).first():
            s.add(Funder(name=name, name_full=name, typ=typ, kraina=country,
                         url=f"https://{name.lower().replace(' ', '-')}.example", src="demo", src_key=name))
    s.commit()

    zapysy = []
    for i, (title, kind, amount, for_whom, notes, fi, dl, rel) in enumerate(PROGRAMMES):
        funder = FUNDERS[fi][0]
        zapysy.append({
            "title": title, "source_org": funder, "kind": kind, "amount": amount,
            "url": f"https://{funder.lower().replace(' ', '-')}.example/programmes/{i + 1}",
            "deadline": (today + timedelta(days=dl)).isoformat() if dl is not None else None,
            "status": "rolling" if dl is None else "open", "for_whom": for_whom, "notes": notes,
            "geo": "Ukraine", "reliability": rel, "first_seen": (today - timedelta(days=rnd.randint(3, 60))).isoformat(),
        })
    log = ingest.importuvaty_zapysy(s, zapysy, "demo", "seed_demo")
    made["opportunities"] = log.dodano
    ingest.zvyazaty_funderiv(s)

    owners = [users["oksana"], users["marko"], users["bohdan"]]
    potreby = []
    for i, (nazva, klient, zayavnyk, oblast, byudzhet, opys) in enumerate(CLIENTS):
        from .services import rozbir
        p = Potreba(nazva=nazva, klient=klient, edrpou=f"99{SEED + i:06d}", opys=opys, oblast=oblast,
                    zayavnyk=zayavnyk, byudzhet_eur=float(byudzhet),
                    potribno_do=today + timedelta(days=rnd.randint(40, 200)),
                    tags=",".join(rozbir.tegy_z_tekstu(nazva, opys)),
                    owner_id=owners[i % len(owners)].id, created_by=owners[i % len(owners)].name)
        s.add(p)
        potreby.append(p)
    s.commit()
    for p in potreby:
        s.refresh(p)
        matching.pererakhuvaty(s, p)
    made["requests"] = len(potreby)

    # Applications across every stage, linked to the best match of each request.
    from .models import Match
    stadii = ["ideya", "skryninh", "dokumenty", "podacha", "rezultat", "dokumenty"]
    for i, p in enumerate(potreby):
        m = s.exec(select(Match).where(Match.potreba_id == p.id).order_by(Match.score.desc())).first()
        if not m:
            continue
        o = s.get(Opportunity, m.opportunity_id)
        st = stadii[i % len(stadii)]
        a = Application(nazva=f"{p.klient} → {o.title}"[:300], potreba_id=p.id, opportunity_id=o.id, stadiya=st,
                        owner_id=p.owner_id, prychyna_vidboru="Strong sector and applicant fit.",
                        suma_zapyt=min(p.byudzhet_eur or 0, o.amount_max or p.byudzhet_eur or 0) or None,
                        verdykt="подаватися" if st != "ideya" else "",
                        nastupnyi_krok="Collect the council decision on co-financing" if st in ("ideya", "skryninh", "dokumenty") else "",
                        nastupnyi_krok_do=today + timedelta(days=5 + i), created_by=users["oksana"].name)
        if st in ("podacha", "rezultat"):
            a.podano_at, a.podano_kto = utcnow() - timedelta(days=3 + i), users["halyna"].name
        if st == "rezultat":
            a.rezultat = "vyhrano"
        m.stan = "u_roboti"
        s.add(m)
        s.add(a)
        s.commit()
        s.refresh(a)
        made["applications"] += 1
        _documents(s, a, users, approved=st in ("podacha", "rezultat"))
        s.add(Task(title="Check the budget against the programme ceiling", application_id=a.id,
                   potreba_id=p.id, assignee_id=users["halyna"].id, due=today + timedelta(days=2 + i),
                   dzherelo="ручна", created_by=users["bohdan"].name))
    s.commit()

    for o in s.exec(select(Opportunity)).all()[:4]:
        _program_file(s, o)

    for slug, typ, title, dek, body in ARTICLES:
        if not s.exec(select(Znannya).where(Znannya.slug == slug)).first():
            s.add(Znannya(slug=slug, typ=typ, title=title, dek=dek, body_md=body, tags="demo",
                          data=today - timedelta(days=len(slug) % 9), dzherelo="demo",
                          dzherela_json=json.dumps([{"title": "Portal methodology (demo)",
                                                     "url": "https://docs.example/methodology",
                                                     "date": today.isoformat(), "reliability": "confirmed"}])))
    s.commit()

    zhyvyi.obslugovuvannya(s)        # the daily maintenance job, run once so the feed is alive
    return made


def _documents(s: Session, a: Application, users: dict[str, User], approved: bool) -> None:
    from .services import docstore
    for vyd, nazva, avtor in (("kontseptsiya", "Project concept note", users["halyna"]),
                              ("byudzhet", "Budget and co-financing plan", users["marko"])):
        body = (f"{nazva}\n\nApplication: {a.nazva}\nRequested amount: {a.suma_zapyt or 'tbd'} EUR\n"
                "Synthetic demo document — not a real application.\n").encode()
        meta = docstore.zberehty(body, f"{vyd}.txt")
        d = Document(application_id=a.id, nazva=nazva, vyd=vyd, versiya=1, stored_name=meta["stored_name"],
                     rozmir=meta["rozmir"], avtor=avtor.name, avtor_id=avtor.id, created_by=avtor.name,
                     group_uid=f"demo-{a.id}-{vyd}")
        if approved:
            d.pohodzheno_kym, d.pohodzheno_kym_id, d.pohodzheno_at = users["bohdan"].name, users["bohdan"].id, utcnow()
        s.add(d)


def _program_file(s: Session, o: Opportunity) -> None:
    from .services import docstore
    if s.exec(select(ProgramFile).where(ProgramFile.opportunity_id == o.id)).first():
        return
    tekst = (f"{o.title}\n\nTerms of reference (synthetic demo).\nEligible applicants: {o.for_whom}.\n"
             f"Amount: {o.amount}.\n").encode()
    meta = docstore.zberehty(tekst, "terms.txt", "prohramy")
    s.add(ProgramFile(opportunity_id=o.id, nazva="Programme terms (demo)", vyd="polozhennya",
                      source_url=o.url, fetched_at=utcnow(), sha256=meta["sha256"], mime="text/plain",
                      rozmir=meta["rozmir"], stored_name=meta["stored_name"], tekst=tekst.decode()))


def main(argv: list[str]) -> int:
    if config.IS_PROD:
        print("seed_demo refuses to run in production", file=sys.stderr)
        return 2
    from .db import engine, init_db
    if "--reset" in argv:
        from sqlmodel import SQLModel
        SQLModel.metadata.drop_all(engine)
    init_db()
    with Session(engine) as s:
        print("demo data:", seed(s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
