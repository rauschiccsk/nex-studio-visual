"""The Nasadiť dropdown must default to the NEWEST verified version (Director obs 2026-07-11).

``list_verified_versions`` feeds the deploy matrix; the FE defaults the dropdown to ``verified_versions[0]``.
version_number mixes ``v1.0.0`` (the graduated first-PROD, stored with a leading ``v``) and ``1.1.0`` — a
STRING descending sort put the OLDER ``v1.0.0`` first (``'v'`` > ``'1'`` in ASCII), so the dropdown defaulted to
an OLD version (accidental old-version deploy risk on UAT + PROD). ``_semver_sort_key`` is v-prefix-agnostic +
numeric, so the newest is genuinely first.
"""

from __future__ import annotations

from backend.services.deploy import _semver_sort_key


def test_semver_key_is_numeric_and_v_agnostic() -> None:
    assert _semver_sort_key("v1.2.0") == (1, 2, 0)
    assert _semver_sort_key("1.2.0") == (1, 2, 0)  # same as with the 'v'
    assert _semver_sort_key("1.10.0") > _semver_sort_key("1.9.0")  # numeric, not string ('10' > '9')


def test_newest_first_regardless_of_v_prefix() -> None:
    versions = ["v1.0.0", "1.1.0", "1.2.0", "v0.9.0"]
    assert sorted(versions, key=_semver_sort_key, reverse=True) == ["1.2.0", "1.1.0", "v1.0.0", "v0.9.0"]


def test_reported_case_new_beats_graduated_v_prefixed() -> None:
    # The exact bug: 1.1.0 (deployed) must sort ahead of the older graduated 'v1.0.0'.
    assert sorted(["v1.0.0", "1.1.0"], key=_semver_sort_key, reverse=True)[0] == "1.1.0"
