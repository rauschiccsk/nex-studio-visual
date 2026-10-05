"""Filter tajomstiev Poradcu (ICCINT-167, návrh §4.4). Všetky hodnoty sú UMELÉ — nikdy nie skutočné."""

from __future__ import annotations

import pytest

from backend.services.poradca.secrets_filter import REDACTED, SecretFilter

FAKE_TOKEN = "fake-oauth-token-0123456789abcdef"


def test_known_value_is_replaced_wherever_it_appears():
    f = SecretFilter([FAKE_TOKEN])
    out = f(f"log: started with {FAKE_TOKEN} and again {FAKE_TOKEN}.")
    assert FAKE_TOKEN not in out
    assert out.count(REDACTED) == 2


def test_short_known_values_are_not_hunted_they_would_blank_ordinary_words():
    f = SecretFilter(["abc", "admin"])
    assert f("admin opened abc") == "admin opened abc"
    assert f.known_count == 0


def test_longer_known_value_wins_over_its_prefix():
    f = SecretFilter(["fake-secret-1234", "fake-secret-1234-extended"])
    assert f("x fake-secret-1234-extended y") == f"x {REDACTED} y"


@pytest.mark.parametrize(
    "text,leak",
    [
        ("DATABASE_URL=postgresql+pg8000://app:fakepass99@db:5432/app", "fakepass99"),
        ("connect redis://user:fakeredispw@cache:6379", "fakeredispw"),
        ("Authorization: Bearer fakebearertoken123456", "fakebearertoken123456"),
        ("GET /ws?token=fakewstoken987&x=1", "fakewstoken987"),
        ("SECRET_KEY=fakesecretkey00", "fakesecretkey00"),
        ('{"password": "fakepw-json"}', "fakepw-json"),
        ("DB_PASSWORD: fakeyamlpw", "fakeyamlpw"),
        ("api_key=fakeapikey777", "fakeapikey777"),
        ("jwt eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4", "SflKxwRJSMeKKF2QT4"),
        ("ghp_FAKEFAKEFAKEFAKEFAKEFAKE1234", "ghp_FAKEFAKEFAKEFAKEFAKEFAKE1234"),
        ("github_pat_FAKE_FAKEFAKEFAKEFAKEFAKE12", "github_pat_FAKE_FAKEFAKEFAKEFAKEFAKE12"),
        ("key sk-ant-oat01-FAKEFAKEFAKEFAKE", "sk-ant-oat01-FAKEFAKEFAKEFAKE"),
        (
            "-----BEGIN OPENSSH PRIVATE KEY-----\nFAKEKEYMATERIAL\n-----END OPENSSH PRIVATE KEY-----",
            "FAKEKEYMATERIAL",
        ),
    ],
)
def test_shapes_hide_secrets_the_backend_does_not_know(text, leak):
    out = SecretFilter()(text)
    assert leak not in out, out
    assert REDACTED in out


def test_ordinary_text_passes_untouched():
    text = "Agent stojí, lebo test_login zlyhal: očakávané 200, prišlo 401. Súbor backend/auth.py:42."
    assert SecretFilter()(text) == text


def test_filter_is_idempotent():
    f = SecretFilter([FAKE_TOKEN])
    once = f(f"password={FAKE_TOKEN}")
    assert f(once) == once
