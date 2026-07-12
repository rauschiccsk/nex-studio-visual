"""Service layer for :class:`~backend.db.models.system_settings.SystemSetting`.

Runtime-mutable ICC-wide configuration. Known keys are registered in
:data:`DEFAULT_SETTINGS` so a fresh install resolves them without a
seed migration — the first time someone edits a value through the
Settings page, a row appears in ``system_settings`` and from then on
the DB value wins.

All methods accept ``db: Session`` as the first argument and only ever
call ``session.flush()`` — transaction commit is the router's
responsibility.

Beyond the CRUD, this module exposes typed helpers
(``get_str`` / ``get_int`` / ``get_float`` / ``get_bool``) that service
and route code uses to replace previously hard-coded constants. Each
helper is backed by a 30-second process-local cache so hot paths (port
validation, timeouts resolved per request) don't hit the DB on every
call; :func:`invalidate_cache` is called from :func:`upsert` so a
Settings-UI edit becomes visible immediately.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.foundation import User
from backend.db.models.system_settings import SystemSetting
from backend.schemas.system_setting import SystemSettingRead, SystemSettingValueType


@dataclass(frozen=True)
class _Default:
    """Service-layer default for a known setting key.

    ``label`` (human Slovak name) and ``unit`` (optional suffix hint) are
    registry-only presentation metadata — they are NEVER stored per-row;
    the read path always sources them from here so a stored override still
    shows the current Slovak label/description/unit.
    """

    value: str
    description: str
    label: str
    value_type: SystemSettingValueType = "string"
    unit: str = ""


#: Known settings + their defaults. A row in ``system_settings`` overrides
#: these at runtime; when no row exists, the default is returned with
#: ``is_default=True``. Type hints are casted on read by the typed
#: helpers further down.
DEFAULT_SETTINGS: dict[str, _Default] = {
    # ── ICC ─────────────────────────────────────────────────────────
    "github_org": _Default(
        value="rauschiccsk",
        label="GitHub organizácia",
        unit="",
        description=(
            "Organizácia na GitHube, ktorou sa predvyplnia adresy repozitárov na formulári "
            'nového projektu (v tvare „organizácia/názov-projektu").'
        ),
    ),
    # ── Miera autonómie — the autonomy dial (v2.0.0, CR-V2-008 / AUTON-1, AUTON-6) ──
    # GLOBAL default level of the 4-level Miera autonómie dial — how often the AI Agent
    # stops at a schvaľovací bod for the Manažér's approval (design §2.3). The four presets:
    #   * ``plna``               — Plná autonómia: runs the whole build non-stop.
    #   * ``len_na_konci``       — Len na konci: stops only when the build is verified/done.
    #   * ``pri_klucovych_bodoch`` — Pri kľúčových bodoch: stops after Návrh + at build-done.
    #   * ``po_kazdej_faze``     — Po každej fáze: stops after each dial-governed phase
    #                              (Návrh / Programovanie / Verifikácia) for maximum control.
    # This is the GLOBAL layer of the AUTON-6 resolution order (per-build → per-project →
    # global, first non-NULL wins): the per-project (``projects.miera_autonomie``) and
    # per-build (``pipeline_state.miera_autonomie``) nullable columns override it; NULL there
    # inherits this default. Two stops are ALWAYS outside the dial regardless of level: the
    # Špecifikácia approval (end of Príprava) and deploy (UAT/PROD) — see the orchestrator
    # evaluator. The dial also scales the Auditor's depth (OQ-9) and sets fast-fix = full-auto.
    "miera_autonomie": _Default(
        value="plna",
        label="Miera autonómie",
        unit="",
        description=(
            "Ako často sa AI agent zastaví a počká na tvoje schválenie: plná / len na konci / "
            "pri kľúčových bodoch / po každej fáze. Dá sa prepísať pre jednotlivý projekt aj "
            "konkrétnu stavbu."
        ),
    ),
    # ── Token-stop poistka — the spine's runtime-mutable build stop (STEP 1, REDESIGN §9) ──
    # GLOBAL cap on how many MILLIONS of tokens a Programovanie build may spend before the engine
    # cooperatively PAUSES at the next task boundary and (for an away Manažér) nudges via Telegram — the
    # Manažér then reads the token-limit state and decides whether to continue (``pokracovat``). 0 = no
    # stop (the partner runs in one go, the pre-spine behaviour). Dynamic by design: when Anthropic
    # changes its token policy the Manažér edits this ONE value in Nastavenia → Systém, no code change.
    # GLOBAL-only for the spine (per-project/per-build override columns are left UNBUILT — the proven
    # 074 dial pattern is documented for later); default '0' keeps every existing v2 PROD project
    # byte-identical (non-stop). The append-only PipelineMessage log IS the token ledger
    # (``pipeline_metrics.aggregate_pipeline_usage``) — no new counter, no migration (a row appears only
    # on the first Settings edit; until then get_int returns this default 0).
    "programovanie_token_stop_millions": _Default(
        value="0",
        value_type="int",
        label="Limit tokenov na stavbu",
        unit="mil. tokenov",
        description=(
            "Keď spotreba tokenov počas programovania prekročí túto hranicu, systém sa slušne "
            "zastaví na najbližšej hranici úlohy a upozorní ťa. 0 = bez limitu (beží naraz)."
        ),
    ),
    # ── Pipeline / AI ───────────────────────────────────────────────
    "claude_stream_timeout_seconds": _Default(
        value="1800",
        value_type="int",
        label="Časový limit toku AI",
        unit="sekúnd",
        description=(
            "Najdlhší čas, počas ktorého môže bežať jeden tok AI, kým sa proces ukončí. "
            "Zvýš, ak dlhé výpisy celej špecifikácie občas narazia na limit."
        ),
    ),
    "claude_design_doc_timeout_seconds": _Default(
        value="1800",
        value_type="int",
        label="Časový limit generovania návrhovej dokumentácie",
        unit="sekúnd",
        description=(
            "Časový limit na vygenerovanie návrhových dokumentov (BEHAVIOR.md / DESIGN.md) "
            "zo schválenej vývojovej dokumentácie."
        ),
    ),
    "claude_task_plan_timeout_seconds": _Default(
        value="1800",
        value_type="int",
        label="Časový limit generovania plánu úloh",
        unit="sekúnd",
        description="Časový limit na vygenerovanie plánu úloh (Epika → Funkcia → Úloha).",
    ),
    "github_api_timeout_seconds": _Default(
        value="10",
        value_type="int",
        label="Časový limit GitHub API",
        unit="sekúnd",
        description="Časový limit HTTP volaní na GitHub (overenie, vytvorenie a zmazanie repozitára).",
    ),
    "conversation_history_limit": _Default(
        value="100",
        value_type="int",
        label="Limit histórie konverzácie",
        unit="správ",
        description=(
            "Koľko posledných správ konverzácie s Architektom sa načíta ako kontext pre AI. "
            "Staršie správy sa uchovávajú, ale AI sa už neposielajú."
        ),
    ),
    "design_doc_max_chars": _Default(
        value="12000",
        value_type="int",
        label="Maximálna dĺžka návrhovej dokumentácie",
        unit="znakov",
        description=(
            "Najviac znakov z DESIGN.md, ktoré sa vložia do AI promptu pri programovaní úlohy, "
            "aby sa nezahltil kontext."
        ),
    ),
    # ── Auth ────────────────────────────────────────────────────────
    "access_token_expire_minutes": _Default(
        value="480",
        value_type="int",
        label="Platnosť prihlásenia",
        unit="minút",
        description="Ako dlho platí prihlásenie, kým sa musíš znova prihlásiť (480 = 8 hodín).",
    ),
    # ── Ports (ICC Port Registry v2 / D-020) ───────────────────────
    "port_range_min": _Default(
        value="10100",
        value_type="int",
        label="Najnižší port pre projekty",
        unit="",
        description=(
            "Najnižšie číslo portu, ktoré NEX Studio prideľuje projektom. "
            "Zmena za behu je riziková pre existujúce nasadenia."
        ),
    ),
    "port_range_max": _Default(
        value="14999",
        value_type="int",
        label="Najvyšší port pre projekty",
        unit="",
        description="Najvyššie číslo portu v rozsahu pre komerčné projekty.",
    ),
    "port_block_size": _Default(
        value="10",
        value_type="int",
        label="Veľkosť bloku portov",
        unit="portov",
        description=(
            "Koľko portov dostane jeden projekt. Štandard: bloky po 10 (backend / frontend / databáza + 7 rezervných)."
        ),
    ),
    # ── Path templates ─────────────────────────────────────────────
    "default_source_path_template": _Default(
        value="/opt/projects/{slug}",
        label="Predvolené umiestnenie zdrojového kódu",
        unit="",
        description=(
            'Kam sa štandardne uloží zdrojový kód projektu. „{slug}" sa nahradí názvom projektu. '
            "Podľa konvencie žijú nové projekty v /opt/projects/<názov>/."
        ),
    ),
    "default_kb_path_template": _Default(
        value="/home/icc/knowledge/projects/{slug}",
        label="Predvolené umiestnenie znalostnej bázy",
        unit="",
        description="Predvolený priečinok pre živé dokumenty jednotlivého projektu.",
    ),
    "template_init_script_path": _Default(
        value="",
        label="Cesta k inicializačnému skriptu šablóny",
        unit="",
        description=(
            "Úplná cesta k skriptu init.sh, ktorý pri vytvorení projektu založí jeho priečinok "
            "a základné súbory. Prázdne = automatické zakladanie je vypnuté."
        ),
    ),
    "template_init_timeout_seconds": _Default(
        value="60",
        value_type="int",
        label="Časový limit inicializácie šablóny",
        unit="sekúnd",
        description=(
            "Najdlhší čas na dobehnutie skriptu init.sh. Bežné založenie trvá do 5 sekúnd; "
            "60 pokrýva prvé vytváranie priečinkov a Docker."
        ),
    ),
    "reserved_port_ranges": _Default(
        value="",
        label="Rezervované rozsahy portov",
        unit="",
        description=(
            "Rozsahy portov vyhradené pre projekty spravované mimo NEX Studia (oddelené čiarkou, "
            'napr. „10110-10159"). Z týchto rozsahov systém port nepridelí. Prázdne = žiadne rezervácie.'
        ),
    ),
    # ── Metrics / ROI pricing (E5, CR-NS-043) ───────────────────────
    "developer_hourly_rate": _Default(
        value="0.0",
        value_type="float",
        label="Hodinová sadzba vývojára",
        unit="€ / hod",
        description=(
            "Priemerná hodinová sadzba ľudského vývojára pre porovnanie na stránke Metriky. "
            "0 = nenastavené → návratnosť sa nezobrazí (nikdy sa nevymyslí číslo)."
        ),
    ),
    "api_price_input_per_mtok": _Default(
        value="0.0",
        value_type="float",
        label="Cena za vstupné tokeny",
        unit="$ / mil. tokenov",
        description=(
            "Cena Claude API za 1 milión vstupných tokenov — pre výpočet nákladov na stránke "
            "Metriky. 0 = nenastavené → náklad sa nezobrazí."
        ),
    ),
    "api_price_output_per_mtok": _Default(
        value="0.0",
        value_type="float",
        label="Cena za výstupné tokeny",
        unit="$ / mil. tokenov",
        description=(
            "Cena Claude API za 1 milión výstupných tokenov — pre výpočet nákladov na stránke "
            "Metriky. 0 = nenastavené → náklad sa nezobrazí."
        ),
    ),
    # ── Metrics / ROI — per-PHASE agent-vs-human model (v2 metrics per-phase basis, CR-V2-029) ─────
    # The v1 11 per-role keys (metrics_minutes_per_mtok_{coordinator,designer,customer,implementer,
    # auditor} + metrics_hourly_wage_{coordinator,designer,customer,implementer,auditor,director}) and
    # the now-dead director-rate (metrics_director_minutes_per_human_role_hour) are RETIRED here — the
    # v2 AI-Agent + Auditor engine has no fixed roles, only the four visible build phases, and the priced
    # Director overhead is gone (the Manažér overhead is info-only now). Replaced by per-PHASE rate +
    # wage keys (4 phases × {rate, wage}), feeding services.metrics.compute_project_metrics.
    #
    # Per-phase token→minutes conversion (human-time): minutes of equivalent human work per 1,000,000
    # total tokens (IN+OUT) for that phase. 0 = unset → that phase's human-time/cost is null (never
    # fabricated). Seeded via the Settings UI (NOT here — defaults stay 0.0 so a fresh install reads
    # "not configured", not a fake number).
    "metrics_minutes_per_mtok_priprava": _Default(
        value="0.0",
        value_type="float",
        label="Ľudský čas — fáza Príprava",
        unit="min / mil. tokenov",
        description=(
            "Koľko minút ľudskej práce zodpovedá 1 miliónu tokenov vo fáze Príprava. "
            "0 = nenastavené → čas a náklad tejto fázy sa nezobrazia."
        ),
    ),
    "metrics_minutes_per_mtok_navrh": _Default(
        value="0.0",
        value_type="float",
        label="Ľudský čas — fáza Návrh",
        unit="min / mil. tokenov",
        description=(
            "Koľko minút ľudskej práce zodpovedá 1 miliónu tokenov vo fáze Návrh. 0 = nenastavené → nezobrazí sa."
        ),
    ),
    # CR-1 (nex-studio-visual): the Vizuál phase is a comparison phase too (COMPARISON_PHASES derives from
    # STAGE_VALUES), so it carries its own rate/wage keys. Default 0.0 = unconfigured → its human-time/cost is null.
    "metrics_minutes_per_mtok_vizual": _Default(
        value="0.0",
        value_type="float",
        label="Ľudský čas — fáza Vizuál",
        unit="min / mil. tokenov",
        description=(
            "Koľko minút ľudskej práce zodpovedá 1 miliónu tokenov vo fáze Vizuál. 0 = nenastavené → nezobrazí sa."
        ),
    ),
    "metrics_minutes_per_mtok_programovanie": _Default(
        value="0.0",
        value_type="float",
        label="Ľudský čas — fáza Programovanie",
        unit="min / mil. tokenov",
        description=(
            "Koľko minút ľudskej práce zodpovedá 1 miliónu tokenov vo fáze Programovanie. "
            "0 = nenastavené → nezobrazí sa."
        ),
    ),
    "metrics_minutes_per_mtok_verifikacia": _Default(
        value="0.0",
        value_type="float",
        label="Ľudský čas — fáza Verifikácia",
        unit="min / mil. tokenov",
        description=(
            "Koľko minút ľudskej práce zodpovedá 1 miliónu tokenov vo fáze Verifikácia. 0 = nenastavené → nezobrazí sa."
        ),
    ),
    # Per-phase hourly wage (currency-agnostic) for the human-cost side (human-time × wage). 0 = unset → null.
    "metrics_hourly_wage_priprava": _Default(
        value="0.0",
        value_type="float",
        label="Hodinová mzda — fáza Príprava",
        unit="€ / hod",
        description=(
            "Hodinová mzda ľudskej práce pre fázu Príprava (na výpočet ľudského nákladu). "
            "0 = nenastavené → nezobrazí sa."
        ),
    ),
    "metrics_hourly_wage_navrh": _Default(
        value="0.0",
        value_type="float",
        label="Hodinová mzda — fáza Návrh",
        unit="€ / hod",
        description="Hodinová mzda ľudskej práce pre fázu Návrh. 0 = nenastavené → nezobrazí sa.",
    ),
    # CR-1 (nex-studio-visual): per-phase wage for the Vizuál phase (see the rate key above).
    "metrics_hourly_wage_vizual": _Default(
        value="0.0",
        value_type="float",
        label="Hodinová mzda — fáza Vizuál",
        unit="€ / hod",
        description="Hodinová mzda ľudskej práce pre fázu Vizuál. 0 = nenastavené → nezobrazí sa.",
    ),
    "metrics_hourly_wage_programovanie": _Default(
        value="0.0",
        value_type="float",
        label="Hodinová mzda — fáza Programovanie",
        unit="€ / hod",
        description="Hodinová mzda ľudskej práce pre fázu Programovanie. 0 = nenastavené → nezobrazí sa.",
    ),
    "metrics_hourly_wage_verifikacia": _Default(
        value="0.0",
        value_type="float",
        label="Hodinová mzda — fáza Verifikácia",
        unit="€ / hod",
        description="Hodinová mzda ľudskej práce pre fázu Verifikácia. 0 = nenastavené → nezobrazí sa.",
    ),
    # Per-family API price (IN/OUT per 1,000,000 tokens). Falls back to the flat api_price_*_per_mtok
    # pair (which itself falls back to env) for the _unknown family + any family left at 0.
    "api_price_input_per_mtok_opus": _Default(
        value="0.0",
        value_type="float",
        label="Cena vstupu — Opus",
        unit="$ / mil. tokenov",
        description=("Cena za 1 milión vstupných tokenov pre modely Opus. Ak je 0, použije sa všeobecná cena vstupu."),
    ),
    "api_price_output_per_mtok_opus": _Default(
        value="0.0",
        value_type="float",
        label="Cena výstupu — Opus",
        unit="$ / mil. tokenov",
        description=(
            "Cena za 1 milión výstupných tokenov pre modely Opus. Ak je 0, použije sa všeobecná cena výstupu."
        ),
    ),
    "api_price_input_per_mtok_sonnet": _Default(
        value="0.0",
        value_type="float",
        label="Cena vstupu — Sonnet",
        unit="$ / mil. tokenov",
        description=(
            "Cena za 1 milión vstupných tokenov pre modely Sonnet. Ak je 0, použije sa všeobecná cena vstupu."
        ),
    ),
    "api_price_output_per_mtok_sonnet": _Default(
        value="0.0",
        value_type="float",
        label="Cena výstupu — Sonnet",
        unit="$ / mil. tokenov",
        description=(
            "Cena za 1 milión výstupných tokenov pre modely Sonnet. Ak je 0, použije sa všeobecná cena výstupu."
        ),
    ),
    "api_price_input_per_mtok_haiku": _Default(
        value="0.0",
        value_type="float",
        label="Cena vstupu — Haiku",
        unit="$ / mil. tokenov",
        description=("Cena za 1 milión vstupných tokenov pre modely Haiku. Ak je 0, použije sa všeobecná cena vstupu."),
    ),
    "api_price_output_per_mtok_haiku": _Default(
        value="0.0",
        value_type="float",
        label="Cena výstupu — Haiku",
        unit="$ / mil. tokenov",
        description=(
            "Cena za 1 milión výstupných tokenov pre modely Haiku. Ak je 0, použije sa všeobecná cena výstupu."
        ),
    ),
}


# ── Process-local cache for typed getters ─────────────────────────────

_CACHE_TTL_SECONDS = 30.0
_cache: dict[str, tuple[str, str, float]] = {}
"""Map key → (value, value_type, expires_at_monotonic)."""


def invalidate_cache(key: Optional[str] = None) -> None:
    """Drop the cached value for *key* (or the whole cache if ``None``).

    Called from :func:`upsert` so a Settings-UI change takes effect on
    the next request rather than waiting up to ``_CACHE_TTL_SECONDS``.
    """
    if key is None:
        _cache.clear()
    else:
        _cache.pop(key, None)


def _load_effective(db: Session, key: str) -> tuple[str, str]:
    """Return ``(value_str, value_type)`` for *key* — DB row if present,
    default otherwise. Callers are expected to have validated the key
    exists in :data:`DEFAULT_SETTINGS`."""
    row = db.execute(select(SystemSetting).where(SystemSetting.key == key)).scalar_one_or_none()
    if row is not None:
        return row.value, row.value_type

    default = DEFAULT_SETTINGS.get(key)
    if default is None:
        raise KeyError(f"Unknown system setting: {key!r}")
    return default.value, default.value_type


def _cached(db: Session, key: str) -> tuple[str, str]:
    now = time.monotonic()
    cached = _cache.get(key)
    if cached is not None and cached[2] > now:
        return cached[0], cached[1]
    value, value_type = _load_effective(db, key)
    _cache[key] = (value, value_type, now + _CACHE_TTL_SECONDS)
    return value, value_type


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in ("true", "1", "yes", "on")


def get_str(db: Session, key: str) -> str:
    """Return the effective string value of *key* (cached)."""
    value, _ = _cached(db, key)
    return value


def get_int(db: Session, key: str) -> int:
    """Return the effective integer value of *key* (cached)."""
    value, _ = _cached(db, key)
    return int(value)


def get_float(db: Session, key: str) -> float:
    """Return the effective float value of *key* (cached)."""
    value, _ = _cached(db, key)
    return float(value)


def get_float_or_none(db: Session, key: str) -> Optional[float]:
    """Return the float value of *key* ONLY when a ``system_settings`` row exists for it, else ``None``.

    Unlike :func:`get_float` (which falls back to the registered default → 0.0 when no row exists),
    this distinguishes an explicitly-stored ``0.0`` from "never configured": a row → its value
    (including 0.0) is honored; no row → ``None``. Lets a caller honor an explicit 0 while still
    treating "no row at all" as unset (e.g. the developer_hourly_rate fallback chain in metrics).
    Not cached — it is read once on the read-time metrics path, not a hot request loop, and must see
    row presence directly (the typed cache stores only value+type, not whether a row backs it)."""
    row = db.execute(select(SystemSetting).where(SystemSetting.key == key)).scalar_one_or_none()
    if row is None:
        return None
    try:
        return float(row.value)
    except (TypeError, ValueError):
        return None


def get_bool(db: Session, key: str) -> bool:
    """Return the effective bool value of *key* (cached).

    Accepts the usual truthy strings ``true/1/yes/on`` (case-insensitive).
    """
    value, _ = _cached(db, key)
    return _parse_bool(value)


# ── Read (router-facing) ─────────────────────────────────────────────


def _to_read_from_default(key: str, default: _Default) -> SystemSettingRead:
    return SystemSettingRead(
        key=key,
        value=default.value,
        label=default.label,
        unit=default.unit,
        value_type=default.value_type,
        description=default.description,
        updated_at=None,
        updated_by=None,
        updated_by_username=None,
        is_default=True,
    )


def _to_read_from_row(row: SystemSetting, username: Optional[str] = None) -> SystemSettingRead:
    # label / unit / description are REGISTRY metadata — sourced from
    # DEFAULT_SETTINGS, never from the stored row, so a runtime override
    # still shows the current Slovak label/description/unit rather than a
    # stale row.description. The row supplies only value + updated_* +
    # is_default=False. Unknown keys (admin-inserted, no default) fall
    # back to the raw key as label and the row's own description.
    meta = DEFAULT_SETTINGS.get(row.key)
    return SystemSettingRead(
        key=row.key,
        value=row.value,
        label=meta.label if meta else row.key,
        unit=meta.unit if meta else "",
        value_type=row.value_type,
        description=meta.description if meta else row.description,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
        updated_by_username=username,
        is_default=False,
    )


def _resolve_username(db: Session, user_id: Optional[UUID]) -> Optional[str]:
    """Return the ``username`` for a user id, or ``None`` if the user is
    missing (deleted or NULL on the row)."""
    if user_id is None:
        return None
    return db.execute(select(User.username).where(User.id == user_id)).scalar_one_or_none()


def list_all(db: Session) -> list[SystemSettingRead]:
    """Return every known setting.

    The result is the union of every key in :data:`DEFAULT_SETTINGS`
    plus any row in ``system_settings`` that does not correspond to a
    registered default (forward-compat for admin-inserted keys). DB
    rows override defaults; missing keys fall back to the default.

    Ordered by key ASC so the Settings page renders a stable list.
    """
    stored: dict[str, SystemSetting] = {row.key: row for row in db.execute(select(SystemSetting)).scalars()}
    keys = sorted(set(DEFAULT_SETTINGS.keys()) | set(stored.keys()))
    out: list[SystemSettingRead] = []
    for key in keys:
        row = stored.get(key)
        if row is not None:
            out.append(_to_read_from_row(row, _resolve_username(db, row.updated_by)))
            continue
        default = DEFAULT_SETTINGS.get(key)
        if default is not None:
            out.append(_to_read_from_default(key, default))
    return out


def get_by_key(db: Session, key: str) -> SystemSettingRead:
    """Return one setting by key — DB row if present, default otherwise.

    Raises :class:`ValueError` if the key is neither stored nor in
    :data:`DEFAULT_SETTINGS`.
    """
    row = db.execute(select(SystemSetting).where(SystemSetting.key == key)).scalar_one_or_none()
    if row is not None:
        return _to_read_from_row(row, _resolve_username(db, row.updated_by))

    default = DEFAULT_SETTINGS.get(key)
    if default is None:
        raise ValueError(f"Unknown system setting: {key!r}")
    return _to_read_from_default(key, default)


def _validate_value_for_type(value: str, value_type: SystemSettingValueType) -> None:
    """Raise :class:`ValueError` when *value* cannot be parsed as *value_type*."""
    if value_type == "string":
        return
    if value_type == "int":
        try:
            int(value)
        except ValueError as exc:
            raise ValueError(f"Value {value!r} is not a valid int") from exc
        return
    if value_type == "float":
        try:
            float(value)
        except ValueError as exc:
            raise ValueError(f"Value {value!r} is not a valid float") from exc
        return
    if value_type == "bool":
        if value.strip().lower() not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
            raise ValueError(f"Value {value!r} is not a valid bool — use true/false/1/0/yes/no/on/off")


def upsert(
    db: Session,
    key: str,
    value: str,
    *,
    updated_by: Optional[UUID] = None,
) -> SystemSettingRead:
    """Create or update a setting row.

    Only keys registered in :data:`DEFAULT_SETTINGS` may be upserted —
    unknown keys are rejected so the Settings UI cannot drift from the
    backend's list of recognised settings. The row's ``description``
    and ``value_type`` are sourced from the default when creating;
    updates keep the stored description untouched.

    Raises :class:`ValueError` when ``key`` is unknown or when
    ``value`` cannot be parsed against the registered ``value_type``.
    """
    default = DEFAULT_SETTINGS.get(key)
    if default is None:
        raise ValueError(f"Unknown system setting: {key!r}")

    _validate_value_for_type(value, default.value_type)

    row = db.execute(select(SystemSetting).where(SystemSetting.key == key)).scalar_one_or_none()

    if row is None:
        row = SystemSetting(
            key=key,
            value=value,
            value_type=default.value_type,
            description=default.description,
            updated_by=updated_by,
        )
        db.add(row)
    else:
        row.value = value
        # Keep value_type in sync with the registered default —
        # defensive against ``DEFAULT_SETTINGS`` evolution.
        row.value_type = default.value_type
        row.updated_by = updated_by

    db.flush()
    invalidate_cache(key)
    return _to_read_from_row(row, _resolve_username(db, row.updated_by))
