"""DEV-443 — the agent's working copy in ``private/`` goes as one folder, not 190 lines.

20.11.2026, Orthodox Hermenia 0.2.0: the Staviteľ unpacked the sources of nex-app-kit 0.17.0 into ``private/kitsrc/``
to read them; „Súbory pre Staviteľa“ listed 196 lines for three documents the Manažér had attached. Director: „Je tam
zobrazený zoznam súborov 196 riadkov čo nepatrí sem.“ The screen groups the agent's files by folder (frontend test) and
the Manažér removes such a folder in one step — only a folder inside ``private/``, never ``private/`` itself, a link or
anything outside; the build history says who removed it and how many files went.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.services import project_files
from tests import test_the_manager_attaches_a_file_for_the_agent as attach
from tests.test_the_manager_attaches_a_file_for_the_agent import EML, _messages, _repo, _user, _version

#: The client over the files routes, acting as the user it is given (the attaching tests' fixture).
as_user = attach.as_user


def _working_copy(root, folder="kitsrc", count=3):
    base = root / "private" / folder / "src"
    base.mkdir(parents=True)
    for n in range(count):
        (base / f"f{n}.ts").write_text("x", encoding="utf-8")


def test_a_folder_inside_private_goes_with_everything_in_it_and_says_what_it_held(tmp_path):
    root = _repo(tmp_path / "proj")
    project_files.save(root, "irmologion.docx", EML, max_bytes=1024)
    _working_copy(root)

    held = project_files.delete_folder(root, "private/kitsrc")

    assert [f.path for f in held] == [f"private/kitsrc/src/f{n}.ts" for n in range(3)]
    assert not (root / "private" / "kitsrc").exists()
    assert [f.path for f in project_files.list_files(root)] == ["private/irmologion.docx"]


@pytest.mark.parametrize(
    "rel", ["private", "private/", "private/..", "private/../private", "secret", "private/missing"]
)
def test_private_itself_and_anything_outside_are_refused_untouched(tmp_path, rel):
    root = _repo(tmp_path / "proj")
    project_files.save(root, "irmologion.docx", EML, max_bytes=1024)
    (root / "secret").mkdir()
    (root / "secret" / "keep.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(project_files.PrivateFileRefused, match="nie je čo zmazať"):
        project_files.delete_folder(root, rel)

    assert (root / "private" / "irmologion.docx").exists()
    assert (root / "secret" / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_a_link_to_a_folder_outside_is_refused_and_what_it_points_at_stays(tmp_path):
    root = _repo(tmp_path / "proj")
    project_files.save(root, "irmologion.docx", EML, max_bytes=1024)
    (root / "secret").mkdir()
    (root / "secret" / "keep.txt").write_text("keep", encoding="utf-8")
    (root / "private" / "outside").symlink_to(root / "secret", target_is_directory=True)

    with pytest.raises(project_files.PrivateFileRefused):
        project_files.delete_folder(root, "private/outside")

    assert (root / "secret" / "keep.txt").exists()


def test_a_link_inside_the_folder_goes_as_a_link_never_followed(tmp_path):
    root = _repo(tmp_path / "proj")
    _working_copy(root, count=1)
    (root / "secret.txt").write_text("keep", encoding="utf-8")
    (root / "private" / "kitsrc" / "link.txt").symlink_to(root / "secret.txt")

    project_files.delete_folder(root, "private/kitsrc")

    assert (root / "secret.txt").read_text(encoding="utf-8") == "keep"


def test_the_manager_removes_the_folder_and_the_history_says_who_and_how_many(db_session, as_user):
    owner = _user(db_session)
    version, root = _version(db_session, owner)
    client = as_user(owner)
    client.post(f"/api/v1/pipeline/{version.id}/files", files={"file": ("irmologion.docx", EML)})
    _working_copy(root)

    r = client.delete(f"/api/v1/pipeline/{version.id}/files/folder", params={"path": "private/kitsrc"})

    assert r.status_code == 200
    assert [f["path"] for f in r.json()["files"]] == ["private/irmologion.docx"]
    last = _messages(db_session, version.id)[-1]
    assert last.content == f"{owner.username} zmazal priečinok private/kitsrc/ z priečinka projektu (počet súborov: 3)."
    assert last.payload["private_file"] == {
        "action": "deleted",
        "path": "private/kitsrc/",
        "files": 3,
        "by": owner.username,
    }


def test_a_missing_folder_is_said_as_a_sentence(db_session, as_user):
    owner = _user(db_session)
    version, _root = _version(db_session, owner)
    client = as_user(owner)
    client.post(f"/api/v1/pipeline/{version.id}/files", files={"file": ("irmologion.docx", EML)})

    r = client.delete(f"/api/v1/pipeline/{version.id}/files/folder", params={"path": "private/kitsrc"})

    assert r.status_code == 409
    assert r.json()["detail"] == "Priečinok private/kitsrc v private/ nie je — nie je čo zmazať."


def test_a_file_written_again_into_a_removed_folder_is_nobodys_upload(db_session, as_user):
    owner = _user(db_session)
    version, root = _version(db_session, owner)
    client = as_user(owner)
    _working_copy(root, count=1)
    client.delete(f"/api/v1/pipeline/{version.id}/files/folder", params={"path": "private/kitsrc"})
    (root / "private" / "kitsrc").mkdir()
    (root / "private" / "kitsrc" / "again.txt").write_text("the agent's own", encoding="utf-8")

    body = client.get(f"/api/v1/pipeline/{version.id}/files").json()

    assert [(f["path"], f["uploaded_by"]) for f in body["files"]] == [("private/kitsrc/again.txt", None)]


CHARTER = Path(__file__).resolve().parents[1] / "templates" / "ai-agent-charter.md"


def test_the_charter_gives_the_agent_one_place_for_its_working_copies_and_has_it_cleaned():
    text = " ".join(CHARTER.read_text(encoding="utf-8").split())

    assert "`private/agent-work/<účel>/`" in text
    assert "po použití priečinok zmaž, najneskôr keď fáza skončí" in text
