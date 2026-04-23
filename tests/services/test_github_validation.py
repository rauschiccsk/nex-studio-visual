"""Tests for GitHub repository validation service."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.services.github_validation import create_github_repo, validate_github_repo


class TestValidateGithubRepo:
    """Tests for validate_github_repo()."""

    @patch("backend.services.github_validation.httpx.get")
    def test_existing_repo_returns_true(self, mock_get: MagicMock) -> None:
        """A 200 response means the repository exists."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        result = validate_github_repo("octocat/Hello-World")

        assert result is True
        mock_get.assert_called_once()
        call_url = mock_get.call_args[0][0]
        assert call_url == "https://api.github.com/repos/octocat/Hello-World"

    @patch("backend.services.github_validation.httpx.get")
    def test_nonexistent_repo_returns_false(self, mock_get: MagicMock) -> None:
        """A 404 response means the repository does not exist."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        result = validate_github_repo("octocat/nonexistent-repo-xyz")

        assert result is False

    @patch("backend.services.github_validation.httpx.get")
    def test_api_error_raises_runtime_error(self, mock_get: MagicMock) -> None:
        """Non-200/404 status codes raise RuntimeError."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_get.return_value = mock_response

        with pytest.raises(RuntimeError, match="unexpected status 500"):
            validate_github_repo("octocat/Hello-World")

    @patch("backend.services.github_validation.httpx.get")
    def test_rate_limited_raises_runtime_error(self, mock_get: MagicMock) -> None:
        """A 403 (rate limit) raises RuntimeError."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "rate limit exceeded"
        mock_get.return_value = mock_response

        with pytest.raises(RuntimeError, match="unexpected status 403"):
            validate_github_repo("octocat/Hello-World")

    def test_invalid_format_no_slash(self) -> None:
        """Repo without '/' raises ValueError."""
        with pytest.raises(ValueError, match="Invalid repository format"):
            validate_github_repo("octocat")

    def test_invalid_format_multiple_slashes(self) -> None:
        """Repo with multiple '/' raises ValueError."""
        with pytest.raises(ValueError, match="Invalid repository format"):
            validate_github_repo("a/b/c")

    def test_invalid_format_empty_string(self) -> None:
        """Empty string raises ValueError."""
        with pytest.raises(ValueError, match="Invalid repository format"):
            validate_github_repo("")

    def test_invalid_format_empty_owner(self) -> None:
        """Empty owner part raises ValueError."""
        with pytest.raises(ValueError, match="Owner and repo name must not be empty"):
            validate_github_repo("/repo")

    def test_invalid_format_empty_name(self) -> None:
        """Empty repo name part raises ValueError."""
        with pytest.raises(ValueError, match="Owner and repo name must not be empty"):
            validate_github_repo("owner/")

    @patch("backend.services.github_validation.httpx.get")
    def test_auth_header_included_when_token_set(self, mock_get: MagicMock) -> None:
        """Authorization header is set when github_token is configured."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        with patch("backend.services.github_validation.settings") as mock_settings:
            mock_settings.github_token = "ghp_test_token_123"
            validate_github_repo("octocat/Hello-World")

        call_headers = mock_get.call_args[1]["headers"]
        assert call_headers["Authorization"] == "Bearer ghp_test_token_123"

    @patch("backend.services.github_validation.httpx.get")
    def test_timeout_from_settings(self, mock_get: MagicMock) -> None:
        """Timeout is read from settings.github_api_timeout."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        with patch("backend.services.github_validation.settings") as mock_settings:
            mock_settings.github_token = "ghp_test"
            mock_settings.github_api_timeout = 30.0
            validate_github_repo("octocat/Hello-World")

        call_timeout = mock_get.call_args[1]["timeout"]
        assert call_timeout == 30.0


class TestCreateGithubRepo:
    """Tests for create_github_repo()."""

    @patch("backend.services.github_validation.httpx.post")
    def test_successful_create_returns_true(self, mock_post: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_post.return_value = mock_response

        with patch("backend.services.github_validation.settings") as mock_settings:
            mock_settings.github_token = "ghp_test"
            mock_settings.github_api_timeout = 10.0
            result = create_github_repo("rauschiccsk/nex-test")

        assert result is True
        url = mock_post.call_args[0][0]
        assert url == "https://api.github.com/orgs/rauschiccsk/repos"
        body = mock_post.call_args[1]["json"]
        assert body["name"] == "nex-test"
        assert body["private"] is True
        assert body["auto_init"] is False
        headers = mock_post.call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer ghp_test"

    @patch("backend.services.github_validation.httpx.post")
    def test_already_exists_returns_false(self, mock_post: MagicMock) -> None:
        """422 with 'already exists' is treated as success (re-use)."""
        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.json.return_value = {
            "errors": [{"message": "name already exists on this account"}]
        }
        mock_post.return_value = mock_response

        with patch("backend.services.github_validation.settings") as mock_settings:
            mock_settings.github_token = "ghp_test"
            mock_settings.github_api_timeout = 10.0
            result = create_github_repo("rauschiccsk/nex-test")

        assert result is False

    @patch("backend.services.github_validation.httpx.post")
    def test_422_without_already_exists_raises_runtime(
        self, mock_post: MagicMock
    ) -> None:
        """Any other 422 payload is a genuine validation error."""
        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.json.return_value = {"errors": [{"message": "some other problem"}]}
        mock_response.text = '{"errors":[{"message":"some other problem"}]}'
        mock_post.return_value = mock_response

        with patch("backend.services.github_validation.settings") as mock_settings:
            mock_settings.github_token = "ghp_test"
            mock_settings.github_api_timeout = 10.0
            with pytest.raises(RuntimeError, match="rejected repository creation"):
                create_github_repo("rauschiccsk/nex-test")

    @patch("backend.services.github_validation.httpx.post")
    def test_401_raises_runtime_token_error(self, mock_post: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Bad credentials"
        mock_post.return_value = mock_response

        with patch("backend.services.github_validation.settings") as mock_settings:
            mock_settings.github_token = "ghp_test"
            mock_settings.github_api_timeout = 10.0
            with pytest.raises(RuntimeError, match="token missing or insufficient scope"):
                create_github_repo("rauschiccsk/nex-test")

    @patch("backend.services.github_validation.httpx.post")
    def test_403_raises_runtime_token_error(self, mock_post: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "Resource not accessible by integration"
        mock_post.return_value = mock_response

        with patch("backend.services.github_validation.settings") as mock_settings:
            mock_settings.github_token = "ghp_test"
            mock_settings.github_api_timeout = 10.0
            with pytest.raises(RuntimeError, match="token missing or insufficient scope"):
                create_github_repo("rauschiccsk/nex-test")

    @patch("backend.services.github_validation.httpx.post")
    def test_404_org_missing_raises_value_error(self, mock_post: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        mock_post.return_value = mock_response

        with patch("backend.services.github_validation.settings") as mock_settings:
            mock_settings.github_token = "ghp_test"
            mock_settings.github_api_timeout = 10.0
            with pytest.raises(ValueError, match="organisation 'nowhere-org' not found"):
                create_github_repo("nowhere-org/some-repo")

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
    def test_description_and_private_false_forwarded(
        self, mock_post: MagicMock
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_post.return_value = mock_response

        with patch("backend.services.github_validation.settings") as mock_settings:
            mock_settings.github_token = "ghp_test"
            mock_settings.github_api_timeout = 10.0
            create_github_repo(
                "rauschiccsk/nex-test",
                description="A test project",
                private=False,
            )

        body = mock_post.call_args[1]["json"]
        assert body["description"] == "A test project"
        assert body["private"] is False
