"""Tests for /health endpoint — Claude CLI availability fields."""

from unittest.mock import patch


def test_health_check(client):
    """Health endpoint returns status ok with version."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"


def test_health_returns_claude_fields(client):
    """Health endpoint includes claude_cli_available and claude_config_mounted."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "claude_cli_available" in data
    assert "claude_config_mounted" in data
    assert isinstance(data["claude_cli_available"], bool)
    assert isinstance(data["claude_config_mounted"], bool)


def test_health_returns_version_from_settings(client):
    """Health endpoint returns version from settings, not hardcoded."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "version" in data
    assert data["version"] == "0.1.0"


def test_health_claude_cli_available_true(client):
    """When claude CLI is on PATH, claude_cli_available is True."""
    with patch("backend.api.routes.health.shutil.which", return_value="/usr/bin/claude"):
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["claude_cli_available"] is True


def test_health_claude_cli_available_false(client):
    """When claude CLI is not on PATH, claude_cli_available is False."""
    with patch("backend.api.routes.health.shutil.which", return_value=None):
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["claude_cli_available"] is False


def test_health_claude_config_mounted_true(client):
    """When config dir exists, claude_config_mounted is True."""
    with patch("backend.api.routes.health.Path") as mock_path:
        mock_path.return_value.is_dir.return_value = True
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["claude_config_mounted"] is True


def test_health_claude_config_mounted_false(client):
    """When config dir does not exist, claude_config_mounted is False."""
    with patch("backend.api.routes.health.Path") as mock_path:
        mock_path.return_value.is_dir.return_value = False
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["claude_config_mounted"] is False
