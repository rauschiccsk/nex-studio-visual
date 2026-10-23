"""Tests for GitHub repository validation service."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.services.github_validation import (
    create_github_repo,
    delete_github_repo,
)


def _mock_get_factory(
    *,
    owner_type: str | None = "Organization",
    owner_404: bool = False,
    owner_login: str | None = None,
    token_login: str | None = None,
):
    """Build a side-effect for httpx.get that answers the probe + /user calls.

    ``/users/{owner}`` returns ``{type: owner_type, login: owner_login}``
    or 404 when ``owner_404`` is True. ``/user`` returns
    ``{login: token_login}``. Raises AssertionError on any unexpected
    URL so tests surface call-mismatch quickly.
    """

    def _side_effect(url, **_kwargs):
        resp = MagicMock()
        if url.startswith("https://api.github.com/users/"):
            if owner_404:
                resp.status_code = 404
                resp.text = "Not Found"
                return resp
            resp.status_code = 200
            resp.json.return_value = {
                "type": owner_type,
                "login": owner_login or url.rsplit("/", 1)[-1],
            }
            return resp
        if url == "https://api.github.com/user":
            resp.status_code = 200
            resp.json.return_value = {"login": token_login}
            return resp
        raise AssertionError(f"Unexpected httpx.get URL in test: {url}")

    return _side_effect


class TestCreateGithubRepo:
    """Tests for create_github_repo()."""

    @patch("backend.services.github_validation.httpx.post")
    @patch("backend.services.github_validation.httpx.get")
    def test_successful_create_org(self, mock_get: MagicMock, mock_post: MagicMock) -> None:
        mock_get.side_effect = _mock_get_factory(owner_type="Organization")
        post_resp = MagicMock()
        post_resp.status_code = 201
        mock_post.return_value = post_resp

        with patch("backend.services.github_validation.settings") as mock_settings:
            mock_settings.github_token = "ghp_test"
            mock_settings.github_api_timeout = 10.0
            result = create_github_repo("icc-dev/nex-test")

        assert result is True
        url = mock_post.call_args[0][0]
        assert url == "https://api.github.com/orgs/icc-dev/repos"
        body = mock_post.call_args[1]["json"]
        assert body["name"] == "nex-test"
        assert body["private"] is True
        # CR-NS-015: repo must be created EMPTY so the scaffold push fast-forwards.
        assert body["auto_init"] is False
        headers = mock_post.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer ghp_test"

    @patch("backend.services.github_validation.httpx.post")
    @patch("backend.services.github_validation.httpx.get")
    def test_successful_create_user_when_token_matches(self, mock_get: MagicMock, mock_post: MagicMock) -> None:
        """Owner is a User account that matches the token owner → /user/repos."""
        mock_get.side_effect = _mock_get_factory(
            owner_type="User",
            owner_login="rauschiccsk",
            token_login="rauschiccsk",
        )
        post_resp = MagicMock()
        post_resp.status_code = 201
        mock_post.return_value = post_resp

        with patch("backend.services.github_validation.settings") as mock_settings:
            mock_settings.github_token = "ghp_test"
            mock_settings.github_api_timeout = 10.0
            result = create_github_repo("rauschiccsk/nex-test")

        assert result is True
        url = mock_post.call_args[0][0]
        assert url == "https://api.github.com/user/repos"
        body = mock_post.call_args[1]["json"]
        assert body["name"] == "nex-test"

    @patch("backend.services.github_validation.httpx.post")
    @patch("backend.services.github_validation.httpx.get")
    def test_user_owner_token_mismatch_raises_value_error(self, mock_get: MagicMock, mock_post: MagicMock) -> None:
        """Owner is a User but the token belongs to someone else → ValueError."""
        mock_get.side_effect = _mock_get_factory(
            owner_type="User",
            owner_login="someone-else",
            token_login="rauschiccsk",
        )

        with patch("backend.services.github_validation.settings") as mock_settings:
            mock_settings.github_token = "ghp_test"
            mock_settings.github_api_timeout = 10.0
            with pytest.raises(ValueError, match="github_token belongs to 'rauschiccsk'"):
                create_github_repo("someone-else/nex-test")

        mock_post.assert_not_called()

    @patch("backend.services.github_validation.httpx.post")
    @patch("backend.services.github_validation.httpx.get")
    def test_owner_not_found_raises_value_error(self, mock_get: MagicMock, mock_post: MagicMock) -> None:
        mock_get.side_effect = _mock_get_factory(owner_404=True)

        with patch("backend.services.github_validation.settings") as mock_settings:
            mock_settings.github_token = "ghp_test"
            mock_settings.github_api_timeout = 10.0
            with pytest.raises(ValueError, match="account 'nowhere' not found"):
                create_github_repo("nowhere/some-repo")

        mock_post.assert_not_called()

    @patch("backend.services.github_validation.httpx.post")
    @patch("backend.services.github_validation.httpx.get")
    def test_already_exists_returns_false(self, mock_get: MagicMock, mock_post: MagicMock) -> None:
        """422 with 'already exists' is treated as success (re-use)."""
        mock_get.side_effect = _mock_get_factory(owner_type="Organization")
        post_resp = MagicMock()
        post_resp.status_code = 422
        post_resp.json.return_value = {"errors": [{"message": "name already exists on this account"}]}
        mock_post.return_value = post_resp

        with patch("backend.services.github_validation.settings") as mock_settings:
            mock_settings.github_token = "ghp_test"
            mock_settings.github_api_timeout = 10.0
            result = create_github_repo("icc-dev/nex-test")

        assert result is False

    @patch("backend.services.github_validation.httpx.post")
    @patch("backend.services.github_validation.httpx.get")
    def test_422_without_already_exists_raises_runtime(self, mock_get: MagicMock, mock_post: MagicMock) -> None:
        """Any other 422 payload is a genuine validation error."""
        mock_get.side_effect = _mock_get_factory(owner_type="Organization")
        post_resp = MagicMock()
        post_resp.status_code = 422
        post_resp.json.return_value = {"errors": [{"message": "some other problem"}]}
        post_resp.text = '{"errors":[{"message":"some other problem"}]}'
        mock_post.return_value = post_resp

        with patch("backend.services.github_validation.settings") as mock_settings:
            mock_settings.github_token = "ghp_test"
            mock_settings.github_api_timeout = 10.0
            with pytest.raises(RuntimeError, match="rejected repository creation"):
                create_github_repo("icc-dev/nex-test")

    @patch("backend.services.github_validation.httpx.post")
    @patch("backend.services.github_validation.httpx.get")
    def test_401_raises_runtime_token_error(self, mock_get: MagicMock, mock_post: MagicMock) -> None:
        mock_get.side_effect = _mock_get_factory(owner_type="Organization")
        post_resp = MagicMock()
        post_resp.status_code = 401
        post_resp.text = "Bad credentials"
        mock_post.return_value = post_resp

        with patch("backend.services.github_validation.settings") as mock_settings:
            mock_settings.github_token = "ghp_test"
            mock_settings.github_api_timeout = 10.0
            with pytest.raises(RuntimeError, match="token missing or insufficient scope"):
                create_github_repo("icc-dev/nex-test")

    @patch("backend.services.github_validation.httpx.post")
    @patch("backend.services.github_validation.httpx.get")
    def test_403_raises_runtime_token_error(self, mock_get: MagicMock, mock_post: MagicMock) -> None:
        mock_get.side_effect = _mock_get_factory(owner_type="Organization")
        post_resp = MagicMock()
        post_resp.status_code = 403
        post_resp.text = "Resource not accessible by integration"
        mock_post.return_value = post_resp

        with patch("backend.services.github_validation.settings") as mock_settings:
            mock_settings.github_token = "ghp_test"
            mock_settings.github_api_timeout = 10.0
            with pytest.raises(RuntimeError, match="token missing or insufficient scope"):
                create_github_repo("icc-dev/nex-test")

    def test_missing_token_raises_runtime(self) -> None:
        with patch("backend.services.github_validation.settings") as mock_settings:
            mock_settings.github_token = ""
            with pytest.raises(RuntimeError, match="no github_token configured"):
                create_github_repo("rauschiccsk/nex-test")

    def test_invalid_format_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid repository format"):
            create_github_repo("missing-slash")

    def test_empty_string_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid repository format"):
            create_github_repo("")

    @patch("backend.services.github_validation.httpx.post")
    @patch("backend.services.github_validation.httpx.get")
    def test_description_and_private_false_forwarded(self, mock_get: MagicMock, mock_post: MagicMock) -> None:
        mock_get.side_effect = _mock_get_factory(owner_type="Organization")
        post_resp = MagicMock()
        post_resp.status_code = 201
        mock_post.return_value = post_resp

        with patch("backend.services.github_validation.settings") as mock_settings:
            mock_settings.github_token = "ghp_test"
            mock_settings.github_api_timeout = 10.0
            create_github_repo(
                "icc-dev/nex-test",
                description="A test project",
                private=False,
            )

        body = mock_post.call_args[1]["json"]
        assert body["description"] == "A test project"
        assert body["private"] is False


class TestDeleteGithubRepo:
    """Tests for delete_github_repo()."""

    @patch("backend.services.github_validation.httpx.delete")
    def test_successful_delete_returns_true(self, mock_delete: MagicMock) -> None:
        resp = MagicMock()
        resp.status_code = 204
        mock_delete.return_value = resp

        with patch("backend.services.github_validation.settings") as mock_settings:
            mock_settings.github_token = "ghp_test"
            mock_settings.github_api_timeout = 10.0
            result = delete_github_repo("rauschiccsk/nex-test")

        assert result is True
        url = mock_delete.call_args[0][0]
        assert url == "https://api.github.com/repos/rauschiccsk/nex-test"

    @patch("backend.services.github_validation.httpx.delete")
    def test_nonexistent_repo_returns_false(self, mock_delete: MagicMock) -> None:
        """404 is treated as success — the repo is gone, our goal."""
        resp = MagicMock()
        resp.status_code = 404
        mock_delete.return_value = resp

        with patch("backend.services.github_validation.settings") as mock_settings:
            mock_settings.github_token = "ghp_test"
            mock_settings.github_api_timeout = 10.0
            assert delete_github_repo("rauschiccsk/already-gone") is False

    @patch("backend.services.github_validation.httpx.delete")
    def test_403_raises_runtime_scope_error(self, mock_delete: MagicMock) -> None:
        resp = MagicMock()
        resp.status_code = 403
        resp.text = "Must have admin rights"
        mock_delete.return_value = resp

        with patch("backend.services.github_validation.settings") as mock_settings:
            mock_settings.github_token = "ghp_test"
            mock_settings.github_api_timeout = 10.0
            with pytest.raises(RuntimeError, match="delete_repo"):
                delete_github_repo("rauschiccsk/nex-test")

    def test_missing_token_raises(self) -> None:
        with patch("backend.services.github_validation.settings") as mock_settings:
            mock_settings.github_token = ""
            with pytest.raises(RuntimeError, match="no github_token configured"):
                delete_github_repo("rauschiccsk/nex-test")

    def test_invalid_format_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid repository format"):
            delete_github_repo("no-slash")
