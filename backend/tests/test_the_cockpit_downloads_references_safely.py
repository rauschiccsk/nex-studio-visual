"""DEV-511 — the cockpit downloads the documents a new project lists (``<podklady>``) into ``docs/reference/`` itself —
safely: the address comes from the Projektant, who reads pages of strangers, so nothing may lead the cockpit into the
machine's own network, and a login or error page must not be stored as the document.

Measured 25.11.2026 from ANDROS for NEX DualBook: 7 of 8 documents came at once (3× XSD, 3× PDF, DOCX); the bank's
manual answered 403 with an HTML page, and as a browser 200 with the PDF.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest

from backend.config.settings import settings
from backend.services import reference_docs as R
from backend.services.poradca.references import Reference

PDF = b"%PDF-1.7\n%stuff\n"
XSD = b'<?xml version="1.0" encoding="UTF-8"?>\n<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"/>\n'
DOCX = b"PK\x03\x04rest-of-the-zip"
LOGIN = b"<!DOCTYPE html><html><head><title>Request Failed</title></head><body>no</body></html>"

PUBLIC = {
    "ekr.financnasprava.sk": ["194.1.4.55"],
    "www.unicreditbank.sk": ["193.85.205.10"],
    "cdn.example.sk": ["8.8.8.8"],
}


@pytest.fixture
def web(monkeypatch):
    """The internet as the cockpit meets it: names resolved by ``PUBLIC``, answers by ``pages`` keyed by the URL the
    cockpit asked (the IP it connected to) — and every request kept."""
    pages: dict[str, httpx.Response] = {}
    asked: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return pages.get(str(request.url), httpx.Response(404))

    monkeypatch.setattr(R, "_addresses", lambda host: PUBLIC.get(host) or (_ for _ in ()).throw(OSError(host)))
    monkeypatch.setattr(R, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    return pages, asked


def _ok(content: bytes, **headers) -> httpx.Response:
    return httpx.Response(200, content=content, headers=headers)


# ── 1. where the cockpit may go ─────────────────────────────────────────────────────────────────────────────────────


def test_it_connects_to_the_checked_address_and_names_the_server_for_the_certificate(web) -> None:
    pages, asked = web
    pages["https://194.1.4.55/Formulare/XSD/dph2025.xsd"] = _ok(XSD)

    assert R.download("https://ekr.financnasprava.sk/Formulare/XSD/dph2025.xsd") == XSD
    (request,) = asked
    assert request.headers["host"] == "ekr.financnasprava.sk"
    assert request.extensions["sni_hostname"] == "ekr.financnasprava.sk"
    assert request.headers["user-agent"] == settings.reference_user_agent


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.0.0.5", "172.26.0.4", "192.168.1.1", "169.254.169.254", "100.109.5.21", "::1", "::ffff:10.0.0.5"],
)
def test_a_name_that_leads_into_a_private_network_is_refused_before_any_request(web, monkeypatch, address) -> None:
    _pages, asked = web
    monkeypatch.setattr(R, "_addresses", lambda host: [address])

    with pytest.raises(R.Refused) as refused:
        R.download("https://intranet.example.sk/a.pdf")

    assert str(refused.value) == "adresa intranet.example.sk vedie do neverejnej siete — kokpit z nej nesťahuje"
    assert asked == []


def test_one_private_address_among_public_ones_is_enough_to_refuse(web, monkeypatch) -> None:
    _pages, asked = web
    monkeypatch.setattr(R, "_addresses", lambda host: ["8.8.8.8", "10.0.0.5"])

    with pytest.raises(R.Refused) as refused:
        R.download("https://mixed.example.sk/a.pdf")

    assert str(refused.value) == "adresa mixed.example.sk vedie do neverejnej siete — kokpit z nej nesťahuje"
    assert asked == []


def test_an_unreachable_address_gives_way_to_the_next_checked_one_ipv4_first(web, monkeypatch) -> None:
    """25.11.2026: www.sbaonline.sk has an IPv6 and an IPv4 address; ANDROS has no IPv6 route („Network is
    unreachable“) — the document came only from the IPv4 one."""
    pages, asked = web
    monkeypatch.setattr(R, "_addresses", lambda host: ["2a00:4b40:aaaa:2012::6", "37.9.175.220"])
    pages["https://37.9.175.220/a.docx"] = _ok(DOCX)

    assert R.download("https://www.sbaonline.sk/a.docx") == DOCX
    assert [r.url.host for r in asked] == ["37.9.175.220"]


def test_when_no_address_answers_the_connection_failure_is_said(web, monkeypatch) -> None:
    def unreachable(request):
        raise httpx.ConnectError("Network is unreachable")

    monkeypatch.setattr(R, "_addresses", lambda host: ["37.9.175.220", "2a00:4b40:aaaa:2012::6"])
    tried: list[str] = []

    def client():
        return httpx.Client(transport=httpx.MockTransport(lambda r: tried.append(r.url.host) or unreachable(r)))

    monkeypatch.setattr(R, "_client", client)

    with pytest.raises(R.Refused) as refused:
        R.download("https://www.sbaonline.sk/a.docx")

    assert str(refused.value) == "spojenie zlyhalo (ConnectError)"
    assert tried == ["37.9.175.220", "2a00:4b40:aaaa:2012::6"]


def test_a_name_that_does_not_exist_is_said(web) -> None:
    with pytest.raises(R.Refused) as refused:
        R.download("https://nowhere.example.sk/a.pdf")

    assert str(refused.value) == "server nowhere.example.sk sa nedá nájsť"


@pytest.mark.parametrize(
    "url", ["http://ekr.financnasprava.sk/a.xsd", "https://ekr.financnasprava.sk:8443/a.xsd", "ftp://x/a.pdf"]
)
def test_only_https_on_its_port(web, url) -> None:
    _pages, asked = web
    with pytest.raises(R.Refused) as refused:
        R.download(url)

    assert str(refused.value) == "adresa nie je https na štandardnom porte"
    assert asked == []


def test_a_redirect_is_checked_like_the_first_address(web) -> None:
    pages, asked = web
    pages["https://193.85.205.10/a.pdf"] = httpx.Response(302, headers={"location": "https://cdn.example.sk/x/a.pdf"})
    pages["https://8.8.8.8/x/a.pdf"] = _ok(PDF)

    assert R.download("https://www.unicreditbank.sk/a.pdf") == PDF
    assert [r.headers["host"] for r in asked] == ["www.unicreditbank.sk", "cdn.example.sk"]


def test_a_redirect_into_a_private_network_is_refused(web, monkeypatch) -> None:
    pages, asked = web
    pages["https://193.85.205.10/a.pdf"] = httpx.Response(302, headers={"location": "https://inside.example.sk/a.pdf"})
    monkeypatch.setattr(R, "_addresses", lambda host: PUBLIC.get(host) or ["10.0.0.5"])

    with pytest.raises(R.Refused) as refused:
        R.download("https://www.unicreditbank.sk/a.pdf")

    assert "inside.example.sk vedie do neverejnej siete" in str(refused.value)
    assert len(asked) == 1


def test_a_redirect_to_plain_http_is_refused(web) -> None:
    pages, _asked = web
    pages["https://193.85.205.10/a.pdf"] = httpx.Response(
        301, headers={"location": "http://www.unicreditbank.sk/a.pdf"}
    )

    with pytest.raises(R.Refused) as refused:
        R.download("https://www.unicreditbank.sk/a.pdf")

    assert str(refused.value) == "adresa nie je https na štandardnom porte"


def test_endless_redirects_end(web, monkeypatch) -> None:
    pages, asked = web
    pages["https://193.85.205.10/a.pdf"] = httpx.Response(302, headers={"location": "/a.pdf"})

    with pytest.raises(R.Refused) as refused:
        R.download("https://www.unicreditbank.sk/a.pdf")

    assert str(refused.value) == "príliš veľa presmerovaní"
    assert len(asked) == settings.reference_max_redirects + 1


# ── 2. what may come back ─────────────────────────────────────────────────────────────────────────────────────────


def test_an_error_answer_is_said_with_its_code(web) -> None:
    pages, _asked = web
    pages["https://193.85.205.10/a.pdf"] = httpx.Response(403, content=LOGIN)

    with pytest.raises(R.Refused) as refused:
        R.download("https://www.unicreditbank.sk/a.pdf")

    assert str(refused.value) == "web odpovedal 403"


def test_a_file_over_the_cap_is_not_kept(web, monkeypatch) -> None:
    pages, _asked = web
    monkeypatch.setattr(settings, "reference_max_bytes", 10)
    pages["https://194.1.4.55/a.xsd"] = _ok(XSD)

    with pytest.raises(R.Refused) as refused:
        R.download("https://ekr.financnasprava.sk/a.xsd")

    assert str(refused.value) == "súbor je väčší ako 0 MB"


def test_a_broken_connection_is_said(web, monkeypatch) -> None:
    def broken(request):
        raise httpx.ConnectTimeout("slow")

    monkeypatch.setattr(R, "_client", lambda: httpx.Client(transport=httpx.MockTransport(broken)))

    with pytest.raises(R.Refused) as refused:
        R.download("https://ekr.financnasprava.sk/a.xsd")

    assert str(refused.value) == "spojenie zlyhalo (ConnectTimeout)"


@pytest.mark.parametrize(
    ("path", "content", "said"),
    [
        ("docs/reference/a.pdf", PDF, None),
        ("docs/reference/a.pdf", LOGIN, "namiesto súboru PDF prišla webová stránka (prihlásenie alebo chyba)"),
        ("docs/reference/a.pdf", b"GIF89a", "obsah nie je PDF"),
        ("docs/reference/a.xsd", XSD, None),
        ("docs/reference/a.xsd", b"\xef\xbb\xbf  " + XSD, None),
        ("docs/reference/a.xsd", LOGIN, "namiesto súboru XSD prišla webová stránka (prihlásenie alebo chyba)"),
        ("docs/reference/a.xsd", b"plain words", "obsah nie je XSD"),
        ("docs/reference/a.docx", DOCX, None),
        ("docs/reference/a.docx", LOGIN, "namiesto súboru DOCX prišla webová stránka (prihlásenie alebo chyba)"),
        ("docs/reference/a.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest", None),
        ("docs/reference/a.csv", b"a;b\n1;2\n", None),
        ("docs/reference/a.csv", LOGIN, "namiesto súboru CSV prišla webová stránka (prihlásenie alebo chyba)"),
        ("docs/reference/a.html", LOGIN, None),
        ("docs/reference/a.pdf", b"", "súbor je prázdny"),
    ],
)
def test_the_content_is_what_the_name_says(path, content, said) -> None:
    assert R.content_problem(path, content) == said


# ── 3. into the project ───────────────────────────────────────────────────────────────────────────────────────────

XSD_REF = Reference("docs/reference/dph/dph2025.xsd", "https://ekr.financnasprava.sk/a/dph2025.xsd", "Schéma DPH")
BANK_REF = Reference("docs/reference/banka/prirucka.pdf", "https://www.unicreditbank.sk/b/prirucka.pdf", "Príručka")


def test_the_documents_land_in_the_project_with_their_sources(web, tmp_path) -> None:
    pages, _asked = web
    pages["https://194.1.4.55/a/dph2025.xsd"] = _ok(XSD)
    pages["https://193.85.205.10/b/prirucka.pdf"] = httpx.Response(403, content=LOGIN)

    done = R.fetch_into(tmp_path, [XSD_REF, BANK_REF])

    assert (tmp_path / "docs/reference/dph/dph2025.xsd").read_bytes() == XSD
    assert not (tmp_path / "docs/reference/banka/prirucka.pdf").exists()
    assert [(d.path, d.ok, d.said) for d in done] == [
        ("docs/reference/dph/dph2025.xsd", True, None),
        ("docs/reference/banka/prirucka.pdf", False, "web odpovedal 403"),
    ]
    assert R.state(tmp_path) == done
    stored = json.loads((tmp_path / R.STATE_FILE).read_text(encoding="utf-8"))
    assert [row["path"] for row in stored["references"]] == [XSD_REF.path, BANK_REF.path]
    sources = (tmp_path / R.SOURCES_FILE).read_text(encoding="utf-8")
    assert "| `dph/dph2025.xsd` | Schéma DPH | https://ekr.financnasprava.sk/a/dph2025.xsd |" in sources
    assert (
        "| `banka/prirucka.pdf` | Príručka | https://www.unicreditbank.sk/b/prirucka.pdf | "
        "nestiahnuté: web odpovedal 403 |" in sources
    )


def test_a_retry_fetches_only_what_failed(web, tmp_path) -> None:
    pages, asked = web
    pages["https://194.1.4.55/a/dph2025.xsd"] = _ok(XSD)
    pages["https://193.85.205.10/b/prirucka.pdf"] = httpx.Response(403, content=LOGIN)
    R.fetch_into(tmp_path, [XSD_REF, BANK_REF])
    pages["https://193.85.205.10/b/prirucka.pdf"] = _ok(PDF)
    asked.clear()

    done = R.retry_failed(tmp_path)

    assert [r.headers["host"] for r in asked] == ["www.unicreditbank.sk"]
    assert [(d.path, d.ok) for d in done] == [(XSD_REF.path, True), (BANK_REF.path, True)]
    assert (tmp_path / BANK_REF.path).read_bytes() == PDF


def test_a_project_without_a_list_has_no_state(tmp_path) -> None:
    assert R.state(tmp_path) == []


def test_a_path_that_would_leave_the_folder_is_never_written(web, tmp_path) -> None:
    pages, _asked = web
    pages["https://194.1.4.55/a/dph2025.xsd"] = _ok(XSD)
    evil = Reference("docs/reference/../../outside.xsd", XSD_REF.url, "Von")

    (done,) = R.fetch_into(tmp_path, [evil])

    assert not done.ok and done.said == "cesta vedie mimo priečinka docs/reference/"
    assert not (tmp_path.parent / "outside.xsd").exists() and not (tmp_path / "outside.xsd").exists()


def test_a_second_run_for_the_same_project_waits_its_turn(tmp_path) -> None:
    lock = R._lock_of(tmp_path)
    lock.acquire()
    try:
        assert R.running(tmp_path) is True
        assert R.start(tmp_path, [XSD_REF]) is False
    finally:
        lock.release()
    assert R.running(tmp_path) is False


def test_the_founding_downloads_only_into_a_project_that_has_nothing_yet(web, tmp_path) -> None:
    pages, asked = web
    pages["https://194.1.4.55/a/dph2025.xsd"] = _ok(XSD)

    assert R.start_once(tmp_path, [XSD_REF]) is True
    assert R.start_once(tmp_path, [XSD_REF]) is False
    assert R.start_once(tmp_path / "other", []) is False
    assert len(asked) == 1


def test_a_project_without_its_folder_downloads_nothing(web, tmp_path) -> None:
    _pages, asked = web

    assert R.start(tmp_path / "missing", [XSD_REF]) is False
    assert asked == []


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def test_the_documents_are_committed_and_nothing_else_of_the_project(web, tmp_path) -> None:
    pages, _asked = web
    pages["https://194.1.4.55/a/dph2025.xsd"] = _ok(XSD)
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "work.txt").write_text("the Manažér's own unsaved work", encoding="utf-8")

    R.fetch_into(tmp_path, [XSD_REF])

    assert _git(tmp_path, "log", "-1", "--format=%s").strip() == R.COMMIT_MESSAGE
    assert sorted(_git(tmp_path, "show", "--name-only", "--format=", "HEAD").split()) == [
        "docs/reference/ZDROJE.md",
        "docs/reference/dph/dph2025.xsd",
        "docs/reference/zdroje.json",
    ]
    assert _git(tmp_path, "status", "--porcelain") == "?? work.txt\n"


def test_without_a_repository_the_documents_stay(web, tmp_path) -> None:
    pages, _asked = web
    pages["https://194.1.4.55/a/dph2025.xsd"] = _ok(XSD)

    (done,) = R.fetch_into(tmp_path, [XSD_REF])

    assert done.ok and (tmp_path / XSD_REF.path).read_bytes() == XSD
