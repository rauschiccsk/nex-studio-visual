"""Tests for the /health endpoint."""

from unittest.mock import patch


def test_health_check(client):
    """Health endpoint returns status ok with version."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"


def test_health_check_claude_fields_present(client):
    """Health endpoint returns claude_cli_available and claude_config_mounted."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "claude_cli_available" in data
    assert "claude_config_mounted" in data
    assert isinstance(data["claude_cli_available"], bool)
    assert isinstance(data["claude_config_mounted"], bool)


@patch("backend.api.routes.health._check_claude_cli_available", return_value=True)
@patch("backend.api.routes.health._check_claude_config_mounted", return_value=True)
def test_health_check_claude_available(mock_config, mock_cli, client):
    """Health endpoint reports Claude CLI available when mocked."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["claude_cli_available"] is True
    assert data["claude_config_mounted"] is True


@patch("backend.api.routes.health._check_claude_cli_available", return_value=False)
@patch("backend.api.routes.health._check_claude_config_mounted", return_value=False)
def test_health_check_claude_unavailable(mock_config, mock_cli, client):
    """Health endpoint reports Claude CLI unavailable when mocked."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["claude_cli_available"] is False
    assert data["claude_config_mounted"] is False
