# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir

"""Сутності грантового порталу: грантодавці, можливості, запити клієнтів, збіги, заявки,
гейти стадій, задачі, документи програм і заявок, запуски агента, журнал імпорту.

Правило даних: невідомо → None («немає даних»), не 0 і не "" (Г15).
"""
from datetime import date, datetime

from sqlmodel import Field

from .base import BaseRow, utcnow


class Funder(BaseRow, table=True):
    """Грантодавець / фінансова установа (сталий довідник)."""
    name: str = Field(index=True)
    name_full: str = ""
    typ: str = ""                    # DFI / ЄС / ООН / держава / фонд / банк
    url: str = ""
    kraina: str = ""
    notes: str = ""
    src: str = ""                    # звідки запис (dataset)
    src_key: str = Field(default="", index=True)


class Opportunity(BaseRow, table=True):
    """Можливість фінансування: грант / програма / конкурс з умовами й дедлайном."""
    title: str = Field(index=True)
    funder_id: int | None = Field(default=None, foreign_key="funder.id", index=True)
    source_org: str = ""
    kind: str = Field(default="grant", index=True)
    kategoriya: str | None = None
    url: str = ""
    url_hash: str | None = Field(default=None, index=True, unique=True)
    dedup_key: str = Field(default="", index=True)
    amount: str = ""                 # як у джерелі (вільний текст)
    amount_min: float | None = None
    amount_max: float | None = None
    currency: str | None = None   # EUR / USD / UAH / CHF
    cofin_pct: float | None = None
    deadline: date | None = Field(default=None, index=True)
    deadline_typ: str = ""           # hard / rolling / poetapno / ""
    status: str = Field(default="open", index=True)
    for_whom: str = ""
    zayavnyky: str = ""              # CSV кодів ZAYAVNYKY; "" = невідомо
    geo: str = ""
    tags: str = ""                   # CSV кодів TEGY
    tags_avto: bool = True           # теги виведені автоматично (можна виправити вручну)
    reliability: str = Field(default="plausible", index=True)
    notes: str = ""
    first_seen: date | None = None
    last_checked: date | None = None
    source_feed: str = ""            # radar:<дата> / portal / grant_market / ручне
    active: bool = Field(default=True, index=True)
    zviryty: str = ""                # розбіжність (напр. дедлайн у календарі інший)
    skryner_json: str = ""           # останній скринінг агента (JSON grant-skryner)
    verdykt: str = ""                # подаватися / ні / уточнити
    confidence: float | None = None
    zlyto_v: int | None = Field(default=None, index=True)   # дубль: обʼєднано в іншу картку (id)

    @property
    def tags_list(self) -> list[str]:
        return [t for t in self.tags.split(",") if t]

    @property
    def zayavnyky_list(self) -> list[str]:
        return [t for t in self.zayavnyky.split(",") if t]

    def dniv_do(self, sohodni: date | None = None) -> int | None:
        if not self.deadline:
            return None
        return (self.deadline - (sohodni or utcnow().date())).days


class Potreba(BaseRow, table=True):
    """Запит клієнта: на що шукаємо фінансування."""
    nazva: str = Field(index=True)            # коротко: «Біогаз 1 МВт для ТОВ …»
    klient: str = ""                          # хто клієнт (громада / підприємство)
    edrpou: str = ""
    opys: str = ""
    tags: str = ""                            # CSV TEGY
    oblast: str = ""
    zayavnyk: str = ""                        # код ZAYAVNYKY
    byudzhet_eur: float | None = None
    potribno_do: date | None = None
    owner_id: int | None = Field(default=None, foreign_key="user.id")
    status: str = Field(default="aktyvnyi", index=True)   # aktyvnyi / pauza / zakrytyi
    prorobka_json: str = ""                   # результат проробки: зведення, готовність, ризики, що треба від клієнта

    @property
    def tags_list(self) -> list[str]:
        return [t for t in self.tags.split(",") if t]


class Match(BaseRow, table=True):
    """Збіг запит ↔ можливість: бал і пояснення."""
    potreba_id: int = Field(foreign_key="potreba.id", index=True)
    opportunity_id: int = Field(foreign_key="opportunity.id", index=True)
    score: float = Field(default=0.0, index=True)
    breakdown_json: str = ""
    stan: str = Field(default="novyi", index=True)   # novyi / u_roboti / vidkhyleno
    prychyna: str = ""
    computed_at: datetime = Field(default_factory=utcnow)


class Application(BaseRow, table=True):
    """Заявка: конкретна потреба на конкретну можливість, рух стадіями."""
    nazva: str = ""
    potreba_id: int | None = Field(default=None, foreign_key="potreba.id", index=True)
    opportunity_id: int | None = Field(default=None, foreign_key="opportunity.id", index=True)
    stadiya: str = Field(default="ideya", index=True)
    rezultat: str = ""                       # vyhrano / vidmova / vidklykano
    owner_id: int | None = Field(default=None, foreign_key="user.id")
    prychyna_vidboru: str = ""               # чому взяли в роботу (для гейту ідея→скринінг)
    suma_zapyt: float | None = None
    podano_at: datetime | None = None
    podano_kto: str = ""                     # ім'я людини, що подала (лише людина)
    nastupnyi_krok: str = ""
    nastupnyi_krok_do: date | None = None
    arkhiv: bool = Field(default=False, index=True)
    verdykt: str = ""                        # скринінг ПІД ЦЕЙ запит: подаватися / ні / уточнити
    skryner_json: str = ""                   # обґрунтування, джерела, чекліст документів


class StageRule(BaseRow, table=True):
    """Гейт стадії: що потрібно, щоб УВІЙТИ в стадію."""
    stadiya: str = Field(index=True, unique=True)
    potribni_dok: str = ""                   # CSV VYDY_DOK
    opys: str = ""


class Task(BaseRow, table=True):
    title: str
    application_id: int | None = Field(default=None, foreign_key="application.id", index=True)
    potreba_id: int | None = Field(default=None, foreign_key="potreba.id", index=True)
    assignee_id: int | None = Field(default=None, foreign_key="user.id", index=True)
    due: date | None = Field(default=None, index=True)
    done: bool = Field(default=False, index=True)
    dzherelo: str = ""                       # ручна / агент-чекліст


class ProgramFile(BaseRow, table=True):
    """Файл програми (положення, форма, шаблон, прецедент) з походженням."""
    opportunity_id: int | None = Field(default=None, foreign_key="opportunity.id", index=True)
    nazva: str = ""
    vyd: str = Field(default="inshe", index=True)
    source_url: str = ""
    fetched_at: datetime | None = None
    sha256: str = Field(default="", index=True)
    mime: str = ""
    rozmir: int = 0
    stored_name: str = ""
    tekst: str = ""                          # витягнутий текст (для пошуку й агента)


class Document(BaseRow, table=True):
    """Документ заявки з версіями. AI-чернетка не проходить гейт, доки її не погоджено людиною."""
    application_id: int | None = Field(default=None, foreign_key="application.id", index=True)
    potreba_id: int | None = Field(default=None, foreign_key="potreba.id", index=True)  # документи клієнта до вибору гранту
    nazva: str = ""
    vyd: str = Field(default="inshe", index=True)
    dzherelo: str = ""                       # звідки файл (шлях у проєкті / URL)
    group_uid: str = Field(default="", index=True)
    versiya: int = 1
    ai_chernetka: bool = False
    agent_run_id: int | None = Field(default=None, foreign_key="agentrun.id")
    stored_name: str = ""
    rozmir: int = 0
    avtor: str = ""
    avtor_id: int | None = Field(default=None, index=True)   # who uploaded this version
    pohodzheno_kym: str = ""
    pohodzheno_kym_id: int | None = None
    pohodzheno_at: datetime | None = None


class AgentRun(BaseRow, table=True):
    """Запуск AI-агента (виконує воркер на ПК). Агент ніколи не подає заявку."""
    application_id: int | None = Field(default=None, foreign_key="application.id", index=True)
    opportunity_id: int | None = Field(default=None, foreign_key="opportunity.id", index=True)
    krok: str = Field(default="zbir", index=True)     # zbir / skryninh / cheklist / chernetka
    status: str = Field(default="v_cherzi", index=True)  # v_cherzi / pratsyuye / ok / gejt_ne_projdeno / pomylka
    klas_pomylky: str = ""
    zapytav: str = ""
    pochatok: datetime | None = None
    kinets: datetime | None = None
    zhurnal: str = ""
    rezultat_json: str = ""
    gejt_json: str = ""
    worker: str = ""


class IngestLog(BaseRow, table=True):
    feed: str = ""
    fayl: str = ""
    dodano: int = 0
    onovleno: int = 0
    dubli: int = 0
    nevalidni: int = 0
    prymitka: str = ""


class Znannya(BaseRow, table=True):
    """Знання порталу: статті й брифи редакції, статті Telegram-каналу (оновлюються автоматично щодня)."""
    slug: str = Field(index=True, unique=True)
    typ: str = Field(default="stattya", index=True)      # stattya / bryf / stattya_kanalu
    title: str = ""
    dek: str = ""
    body_md: str = ""
    dzherela_json: str = ""                              # [{title,url,date,reliability}]
    tags: str = ""
    data: date | None = Field(default=None, index=True)
    dzherelo: str = ""                                   # redaktsiya:<дата> / kanal
    url: str = ""                                        # Telegraph, якщо є
    access: str = "internal"


class Zmina(BaseRow, table=True):
    """Подія стрічки «Що змінилось»: нові можливості й збіги, зміни дедлайнів, закриття програм у роботі."""
    kluch: str = Field(index=True, unique=True)          # ідемпотентність: тип+обʼєкт+день
    typ: str = Field(index=True)                         # nova_mozhlyvist / dedlain_zmineno / zakryvayetsya /
                                                         # prohrama_zakryta / novyi_zbih / nove_znannya
    vazhlyvist: int = Field(default=1, index=True)       # 1 інфо · 2 увага · 3 діяти
    tekst: str = ""
    opportunity_id: int | None = Field(default=None, index=True)
    potreba_id: int | None = Field(default=None, index=True)
    application_id: int | None = Field(default=None, index=True)
    znannya_slug: str = ""
