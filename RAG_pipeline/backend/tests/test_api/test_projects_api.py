from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes.projects_api import list_user_projects


@pytest.mark.asyncio
async def test_list_user_projects_includes_empty_projects_for_upload() -> None:
    user = MagicMock()
    db = AsyncMock()
    rows = MagicMock()
    rows.fetchall.return_value = [("new-project", "New project", "manual", 0)]
    db.execute.return_value = rows

    with patch("src.api.routes.projects_api.get_user_project_ids", return_value=None):
        result = await list_user_projects(user, db)

    assert result == {
        "projects": [
            {
                "id": "new-project",
                "display_name": "New project",
                "platform": "manual",
                "doc_count": 0,
            }
        ]
    }
    query = str(db.execute.await_args.args[0])
    assert "LEFT JOIN documents" in query
    assert "WHERE EXISTS" not in query
