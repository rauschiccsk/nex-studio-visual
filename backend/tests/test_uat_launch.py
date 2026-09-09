"""v4.0.30: the UAT 'Spustiť' — mints a §4.4-valid launch token from the app's deploy .env, so the
Manager can open a deployed token-launch app logged-in directly from the UAT tab."""

from __future__ import annotations

from pathlib import Path

from jose import jwt  # python-jose — the backend's declared JWT lib (same one the app verifies with)

from backend.services import uat_launch, uat_provisioner


def _write_env(root: Path, **kv: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    root.joinpath(".env").write_text("\n".join(f"{k}={v}" for k, v in kv.items()), encoding="utf-8")


def test_build_launch_url_mints_a_verifiable_token(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(uat_provisioner, "UAT_ROOT", tmp_path)
    key = "test-launch-signing-key-0123456789"
    _write_env(
        tmp_path / "acme" / "demo-app",
        MANAGER_LAUNCH_SIGNING_KEY=key,
        MANAGER_MODULE_SLUG="demo-app",
        MANAGER_DEPLOY_SLUG="uat-acme",
    )
    url = uat_launch.build_uat_launch_url("acme", "demo-app", "https://uat-acme-app.isnex.eu", subject="zoltan")
    assert url and "/api/v1/launch?lt=" in url
    # The token must decode the SAME way the app verifies it → the app would accept it.
    # jose raises if aud/iss don't match (verified via kwargs), so a clean decode == the app accepts it.
    claims = jwt.decode(
        url.split("lt=")[1],
        key,
        algorithms=["HS256"],
        audience="demo-app",
        issuer="nex-manager",
    )
    assert claims["purpose"] == "module-launch"
    # ICCINT-61: the ticket. This used to assert "uat-test" — a name no Manager can resolve, so the launch
    # died inside the app every time. The subject is the operator who clicked; that is authentication, not
    # impersonation.
    assert claims["sub"] == "zoltan"
    assert claims["deploy"] == "uat-acme"
    assert claims["exp"] - claims["iat"] <= 60  # under the app's hard cap


def test_none_when_no_launch_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(uat_provisioner, "UAT_ROOT", tmp_path)
    _write_env(tmp_path / "acme" / "pw-app", SOME_OTHER="x")  # not token-launch (no launch key)
    assert uat_launch.build_uat_launch_url("acme", "pw-app", "https://x", subject="zoltan") is None


def test_none_when_no_uat_url(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(uat_provisioner, "UAT_ROOT", tmp_path)
    assert uat_launch.build_uat_launch_url("acme", "app", "", subject="zoltan") is None


def test_customer_dir_slug_is_lowercased() -> None:
    """v4.0.31 regression: the launch endpoint must locate the deploy .env under the CANONICAL customer
    dir slug (lowercased subdomain-or-slug) — a mixed-case DB slug like 'ANDROS' → dir 'andros'. Passing
    the raw slug pointed at a non-existent /opt/uat/ANDROS/... → no key → a spurious 400."""
    from types import SimpleNamespace

    from backend.services import deploy

    assert deploy._customer_dir_slug(SimpleNamespace(slug="ANDROS", subdomain=None)) == "andros"
    assert deploy._customer_dir_slug(SimpleNamespace(slug="X", subdomain=" Andros ")) == "andros"


# ── ICCINT-61: the ticket is that the button could never log anybody in ──────


def _manager_answering(monkeypatch, status_code: int):
    """The paired Manager, answering one question about one operator."""
    captured: dict[str, object] = {}

    class _Answer:
        status_code = 0

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers or {}
        answer = _Answer()
        answer.status_code = status_code
        return answer

    monkeypatch.setattr(uat_launch.httpx, "get", fake_get)
    return captured


def _paired_env(tmp_path, monkeypatch):
    monkeypatch.setattr(uat_provisioner, "UAT_ROOT", tmp_path)
    _write_env(
        tmp_path / "acme" / "demo-app",
        NEX_MANAGER_BASE_URL="https://manager.example",
        MANAGER_MODULE_SLUG="demo-app",
        NEX_MANAGER_API_KEY="module-key-0123456789",
    )


def test_an_unknown_operator_is_reported_not_launched(tmp_path, monkeypatch) -> None:
    """The whole ticket in one assertion. Before ICCINT-61 the subject was ``uat-test``, so the Manager's
    answer was ALWAYS "no such person" — and nobody asked in advance, so the operator saw the app's own
    "Prihlásenie skončilo" and went looking for the fault in the app. For five days."""
    _paired_env(tmp_path, monkeypatch)
    _manager_answering(monkeypatch, 404)

    assert uat_launch.manager_knows_operator("acme", "demo-app", "zoltan") is False


def test_a_known_operator_gets_through(tmp_path, monkeypatch) -> None:
    _paired_env(tmp_path, monkeypatch)
    captured = _manager_answering(monkeypatch, 200)

    assert uat_launch.manager_knows_operator("acme", "demo-app", "zoltan") is True
    # BOTH headers, the shape the Manager actually requires — sending only one is what broke
    # nex-productcatalogs across two versions.
    assert captured["headers"]["X-Module-Slug"] == "demo-app"
    assert captured["headers"]["Authorization"].startswith("Bearer ")
    assert str(captured["url"]).endswith("/api/v1/identity/zoltan")


def test_a_manager_we_cannot_ask_does_not_block(tmp_path, monkeypatch) -> None:
    """Not being able to ask is not evidence the operator is unknown — same rule as the CI floor. A check
    that stops on ignorance is one people learn to route around."""
    _paired_env(tmp_path, monkeypatch)

    def boom(url, headers=None, timeout=None):
        raise uat_launch.httpx.ConnectError("nedostupný")

    monkeypatch.setattr(uat_launch.httpx, "get", boom)

    assert uat_launch.manager_knows_operator("acme", "demo-app", "zoltan") is None


def test_an_app_with_no_paired_manager_is_not_asked_about(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(uat_provisioner, "UAT_ROOT", tmp_path)
    _write_env(tmp_path / "acme" / "solo-app", SOME_OTHER="x")

    def never(url, headers=None, timeout=None):  # pragma: no cover — must not run
        raise AssertionError("appka bez spárovaného Managera sa nemá koho pýtať")

    monkeypatch.setattr(uat_launch.httpx, "get", never)

    assert uat_launch.manager_knows_operator("acme", "solo-app", "zoltan") is None


def test_the_made_up_name_cannot_come_back() -> None:
    """``uat-test`` was not a typo — it was a deliberate constant with a comment defending it ("no
    impersonation"). A defended mistake comes back, so it gets a guard rather than just a deletion.

    Guarded by SHAPE, not by hunting the string: the docstrings deliberately name ``uat-test`` to explain
    what went wrong, and a guard that forbids naming a mistake makes the code less honest, not safer.

      * ``subject`` is keyword-only with NO default — nobody can mint without deciding whose ticket it is;
      * the endpoint decides it as the operator who clicked. Swap that for any fixed string and the button
        silently returns to being unopenable, with the failure surfacing inside the app, three systems away
        from the line that caused it. That distance is what cost the five days.
    """
    import inspect
    from pathlib import Path

    subject = inspect.signature(uat_launch.build_uat_launch_url).parameters["subject"]
    assert subject.kind is inspect.Parameter.KEYWORD_ONLY
    assert subject.default is inspect.Parameter.empty, "razenie vstupenky nesmie mať predvolené meno"

    route = (Path(uat_launch.__file__).parents[1] / "api" / "routes" / "deploy.py").read_text(encoding="utf-8")
    assert "subject=_current_user.username" in route, (
        "vstupenka sa už nerazí na meno toho, kto klikol — takú Manager nevie priradiť k nikomu"
    )


# ── ICCINT-69: the deploy opens the door itself instead of asking ────────────


class _Reply:
    def __init__(self, status_code: int, cookies=None):
        self.status_code = status_code
        self.cookies = cookies if cookies is not None else {}


def _door(monkeypatch, tmp_path, *, launch: _Reply, session: _Reply | None = None):
    """The deployed app answering the two requests the probe makes."""
    key = "test-launch-signing-key-0123456789"
    monkeypatch.setattr(uat_provisioner, "UAT_ROOT", tmp_path)
    _write_env(
        tmp_path / "acme" / "demo-app",
        MANAGER_LAUNCH_SIGNING_KEY=key,
        MANAGER_MODULE_SLUG="demo-app",
        MANAGER_DEPLOY_SLUG="uat-acme",
    )
    seen: list[str] = []

    class _Client:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, data=None, **kwargs):
            # ICCINT-92: sonda odteraz skúša najprv tvar so vstupenkou v TELE. Tieto skúšky sú o tom,
            # čo sa deje PO otvorení dverí (prežije sedenie?), nie o tvare — tak nech atrapa odpovie
            # rovnako ako predtým na adresu. Tvar samotný strážia skúšky nižšie.
            seen.append(url)
            return launch

        def get(self, url, cookies=None):
            seen.append(url)
            if "/api/v1/launch" in url:
                return launch
            assert session is not None, "sonda sa nemala dostať až po sedenie"
            return session

    monkeypatch.setattr(uat_launch.httpx, "Client", _Client)
    return seen


def test_the_door_that_accepts_the_ticket_and_dies_right_after_is_caught(tmp_path, monkeypatch) -> None:
    """THE case this exists for, and the exact shape of this week's five-day failure: the app returned a
    clean 303 from the launch and the session died on the very next request, because it introduced itself to
    its Manager in a shape the Manager does not accept. A probe that stopped at the redirect would have
    called that app healthy — as everything else did, for five days."""
    _door(monkeypatch, tmp_path, launch=_Reply(303, {"session": "x"}), session=_Reply(401))

    opens, detail = uat_launch.uat_door_opens("acme", "demo-app", "https://uat-acme-app.isnex.eu", subject="zoltan")

    assert opens is False
    assert "sedenie hneď nato skončilo" in detail


def test_a_working_door_is_followed_all_the_way_through(tmp_path, monkeypatch) -> None:
    seen = _door(monkeypatch, tmp_path, launch=_Reply(303, {"session": "x"}), session=_Reply(200))

    opens, _ = uat_launch.uat_door_opens("acme", "demo-app", "https://uat-acme-app.isnex.eu", subject="zoltan")

    assert opens is True
    assert any("/api/v1/launch" in u for u in seen) and any("/api/v1/session" in u for u in seen), (
        "vstup sa musí dopovedať až po sedenie, nie skončiť na presmerovaní"
    )


def test_a_refused_ticket_is_caught_too(tmp_path, monkeypatch) -> None:
    _door(monkeypatch, tmp_path, launch=_Reply(401))

    opens, detail = uat_launch.uat_door_opens("acme", "demo-app", "https://uat-acme-app.isnex.eu", subject="zoltan")

    assert opens is False
    assert "odmietla" in detail


def test_an_app_without_a_session_endpoint_is_not_accused(tmp_path, monkeypatch) -> None:
    """Not every app answers on that path. Not being able to finish the sentence is not evidence the door is
    shut — and an alarm that cries on ignorance is one people learn to ignore."""
    _door(monkeypatch, tmp_path, launch=_Reply(303, {"session": "x"}), session=_Reply(404))

    opens, _ = uat_launch.uat_door_opens("acme", "demo-app", "https://uat-acme-app.isnex.eu", subject="zoltan")

    assert opens is None


def test_an_unreachable_app_is_not_accused(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(uat_provisioner, "UAT_ROOT", tmp_path)
    _write_env(
        tmp_path / "acme" / "demo-app",
        MANAGER_LAUNCH_SIGNING_KEY="test-launch-signing-key-0123456789",
        MANAGER_MODULE_SLUG="demo-app",
        MANAGER_DEPLOY_SLUG="uat-acme",
    )

    class _Boom:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, data=None, **kwargs):
            raise uat_launch.httpx.ConnectError("nedostupná")

        def get(self, url, cookies=None):
            raise uat_launch.httpx.ConnectError("nedostupná")

    monkeypatch.setattr(uat_launch.httpx, "Client", _Boom)

    opens, _ = uat_launch.uat_door_opens("acme", "demo-app", "https://uat-acme-app.isnex.eu", subject="zoltan")

    assert opens is None


# ── Kontrola dverí skúša ten tvar, ktorý sa reálne používa (ICCINT-92) ────────
#
# NEX Manager 1.1.0 prestáva posielať vstupenku v adrese a posiela ju v tele požiadavky — lístok
# v adrese totiž končí aj v histórii prehliadača a v záložkách. Appky majú prechodne prijímať oba tvary.
#
# ⚠️ Tu je tá zákernosť: keby kontrola ďalej skúšala IBA starý tvar, svietila by nazeleno aj vtedy, keby
# ten skutočne používaný tvar nefungoval. Zelená stráž, ktorá neskúša cestu, po ktorej sa naozaj chodí,
# je horšia než červená — presne to našla nezávislá previerka Návrhu NEX Manager 1.1.0.


class _ShapeRecorder:
    """Zapamätá si, čo kontrola poslala, a odpovie podľa zadaného predpisu."""

    def __init__(self, post_status: int, get_status: int = 200, session_status: int = 200):
        self.post_status = post_status
        self.get_status = get_status
        self.session_status = session_status
        self.calls: list[tuple[str, str]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, data=None, **kw):
        self.calls.append(("POST", url))
        assert data == {"lt": _TOKEN}, "vstupenka sa neposlala v tele požiadavky"
        return _Answer(self.post_status)

    def get(self, url, **kw):
        self.calls.append(("GET", url))
        if url.endswith("/api/v1/session"):
            return _Answer(self.session_status)
        return _Answer(self.get_status)


class _Answer:
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.cookies = {"session": "x"} if status_code < 400 else {}


_TOKEN = "razena-vstupenka"


def _shape_probe(monkeypatch, client: "_ShapeRecorder") -> None:
    monkeypatch.setattr(uat_launch, "_mint_launch_token", lambda *a, **k: _TOKEN)
    monkeypatch.setattr(uat_launch.httpx, "Client", lambda **kw: client)


def test_the_door_is_tried_with_the_shape_the_manager_actually_uses(monkeypatch) -> None:
    """⚠️ Jadro ICCINT-92: prvý pokus musí ísť novým tvarom, nie starým."""
    client = _ShapeRecorder(post_status=200)
    _shape_probe(monkeypatch, client)

    opens, detail = uat_launch.uat_door_opens("zak", "app", "https://uat.test", subject="kto")

    assert opens is True
    assert client.calls[0][0] == "POST", "kontrola skúsila najprv starý tvar"
    assert "STARÝM" not in detail


def test_an_app_that_does_not_know_the_new_shape_still_opens_but_says_so(monkeypatch) -> None:
    """Náhrada je v poriadku — appka funguje. Ale zamlčať sa nesmie: overené je niečo iné než to,
    čo NEX Manager reálne používa, a to má Manažér vedieť."""
    client = _ShapeRecorder(post_status=405)
    _shape_probe(monkeypatch, client)

    opens, detail = uat_launch.uat_door_opens("zak", "app", "https://uat.test", subject="kto")

    assert opens is True
    assert [c[0] for c in client.calls[:2]] == ["POST", "GET"], "náhradný tvar sa neskúsil"
    assert "STARÝM" in detail, "zelená sa tvári, že sa overil používaný tvar"


def test_a_real_refusal_is_not_papered_over_by_the_fallback(monkeypatch) -> None:
    """⚠️ Náhrada platí len pre „o tomto tvare neviem“ (404/405). Keď appka vstupenku ODMIETNE, je to
    porucha — a druhý pokus iným tvarom by ju len zakryl."""
    client = _ShapeRecorder(post_status=401)
    _shape_probe(monkeypatch, client)

    opens, detail = uat_launch.uat_door_opens("zak", "app", "https://uat.test", subject="kto")

    assert opens is False
    assert [c[0] for c in client.calls] == ["POST"], "po odmietnutí sa skúšalo znova iným tvarom"
    assert "novým tvarom" in detail


def test_the_session_follow_through_still_decides(monkeypatch) -> None:
    """Poistka pôvodnej kontroly zostáva: prijatá vstupenka nestačí, sedenie musí prežiť."""
    client = _ShapeRecorder(post_status=200, session_status=401)
    _shape_probe(monkeypatch, client)

    opens, detail = uat_launch.uat_door_opens("zak", "app", "https://uat.test", subject="kto")

    assert opens is False
    assert "sedenie hneď nato skončilo" in detail
