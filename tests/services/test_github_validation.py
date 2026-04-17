"""Tests for GitHub repository validation service."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.services.github_validation import validate_github_repo


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
