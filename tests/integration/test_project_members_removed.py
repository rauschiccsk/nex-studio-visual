"""Integration test — Project Members endpoints are not implemented.

E2E scenario:
    1. GET /api/v1/projects/{id}/members → 404 (endpoint not found)
    2. POST /api/v1/projects/{id}/members → 404 (endpoint not found)
    3. Verify project_members table does not exist in the database
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestProjectMembersRemoved:
    """Verify project_members endpoints and table do not exist."""

    def test_get_project_members_returns_404(self, integration_client, project):
        """GET /api/v1/projects/{id}/members → 404 (no such endpoint)."""
        resp = integration_client.get(f"/api/v1/projects/{project.id}/members")
        assert resp.status_code == 404, f"Expected 404 for GET /projects/{project.id}/members, got {resp.status_code}"

    def test_post_project_members_returns_404(self, integration_client, project, ri_user):
        """POST /api/v1/projects/{id}/members → 404 (no such endpoint)."""
        resp = integration_client.post(
            f"/api/v1/projects/{project.id}/members",
            json={"user_id": str(ri_user.id), "role": "member"},
        )
        assert resp.status_code in (404, 405), (
            f"Expected 404/405 for POST /projects/{project.id}/members, got {resp.status_code}"
        )

    def test_project_members_table_does_not_exist(self, db_session):
        """Query project_members table → exception (table does not exist)."""
        with pytest.raises((ProgrammingError, IntegrityError)):
            db_session.execute(text("SELECT count(*) FROM project_members"))
            db_session.flush()
