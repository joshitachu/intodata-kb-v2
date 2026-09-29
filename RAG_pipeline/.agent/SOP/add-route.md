# SOP: Add a New API Route

Follow these steps to add a new FastAPI endpoint end-to-end.

---

## Step 1: Create the route file

```
backend/src/api/routes/<domain>.py
```

Use an existing route file as a template (e.g., `documents.py` or `feedback.py`).
Standard structure:

```python
from fastapi import APIRouter

from src.api.dependencies import CurrentUser, DBSession
from src.schemas import MyRequestSchema, MyResponseSchema

router = APIRouter(prefix="/<domain>", tags=["<domain>"])  # /api/v1 is added in main.py


@router.get("", response_model=list[MyResponseSchema])
async def list_items(current_user: CurrentUser, db: DBSession) -> list[MyResponseSchema]:
    ...
```

Rules:
- All I/O must be `async`
- All inputs validated via Pydantic schemas (in `src/schemas.py` or a route-local schema)
- All errors: specific exceptions, logged with `exc_info=True`, re-raised as `HTTPException`
- RBAC: apply project/permission filter on any data access (see `src/rbac/permissions.py`)
- Never `any` — use typed returns
- A route that spends LLM tokens must use `ChatEntitledUser` / `ChatEntitled`
  (`src/api/dependencies.py`); `tests/test_api/test_route_inventory.py` fails for a
  mutating route with no gate that is not on the reviewed allowlist

---

## Step 2: Register the router

In `backend/src/api/main.py`, add the import and `app.include_router()` call:

```python
from src.api.routes import <domain> as <domain>_routes
app.include_router(<domain>_routes.router, prefix="/api/v1")
```

---

## Step 3: Add schemas

Add request/response Pydantic models to `backend/src/schemas.py` (or a dedicated
`src/api/routes/<domain>_schemas.py` if the models are complex):

```python
class MyRequestSchema(BaseModel):
    field: str = Field(..., min_length=1)

class MyResponseSchema(BaseModel):
    id: int
    field: str
    created_at: datetime
```

---

## Step 4: Add database model (if needed)

If the route needs a new table:

1. Create `backend/src/db/models/<domain>.py` with the SQLAlchemy model
2. Import it in `backend/src/db/models/__init__.py`
3. Generate migration: `alembic revision --autogenerate -m "add <domain> table"`
4. Review the generated migration file — autogenerate is not perfect
5. Apply: `alembic upgrade head`

---

## Step 5: Write tests

Create `backend/tests/test_api/test_<domain>.py`. There are no shared `client` /
`auth_headers` fixtures; follow `tests/test_api/test_runtime_status.py`, which overrides
`get_db` and `get_current_user` via `app.dependency_overrides` and calls the app through
`httpx.AsyncClient(transport=ASGITransport(app=app))`:

```python
from httpx import ASGITransport, AsyncClient

from src.api.main import app


async def test_list_items(mock_deps) -> None:  # fixture sets dependency_overrides
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/<domain>")
    assert response.status_code == 200
```

Run: `pytest tests/test_api/test_<domain>.py -v`

---

## Step 6: Add a Next.js route handler (BFF)

Client components must not call FastAPI directly. Add a route handler:

```
frontend/app/api/<domain>/route.ts
```

```typescript
import { fastapiFetch } from '@/lib/fastapi';
import { MyResponseSchema } from '@/types';

export async function GET(request: Request) {
  try {
    const data = await fastapiFetch<unknown>('/api/v1/<domain>');
    return Response.json(MyResponseSchema.array().parse(data));
  } catch (error) {
    console.error('<domain> fetch failed:', error);
    return Response.json({ error: 'Failed to fetch' }, { status: 500 });
  }
}
```

---

## Step 7: Update documentation

1. Add the new endpoint to `RAG_pipeline/.agent/system/api-reference.md`
2. If it's a significant new domain, add a section to `system/subsystems.md`
3. Update `RAG_pipeline/CLAUDE.md` routes list if the domain is new
