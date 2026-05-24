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
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db, CurrentUser
from ...schemas import MyRequestSchema, MyResponseSchema

router = APIRouter(prefix="/api/v1/<domain>", tags=["<domain>"])


@router.get("/", response_model=list[MyResponseSchema])
async def list_items(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> list[MyResponseSchema]:
    ...
```

Rules:
- All I/O must be `async`
- All inputs validated via Pydantic schemas (in `src/schemas.py` or a route-local schema)
- All errors: specific exceptions, logged with `exc_info=True`, re-raised as `HTTPException`
- RBAC: apply project/permission filter on any data access (see `src/rbac/permissions.py`)
- Never `any` — use typed returns

---

## Step 2: Register the router

In `backend/src/api/main.py`, add the import and `app.include_router()` call:

```python
from .routes.<domain> import router as <domain>_router
app.include_router(<domain>_router)
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

Create `backend/tests/test_<domain>.py`:

```python
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_list_items(client: AsyncClient, auth_headers: dict) -> None:
    response = await client.get("/api/v1/<domain>/", headers=auth_headers)
    assert response.status_code == 200
    assert isinstance(response.json(), list)
```

Run: `pytest tests/test_<domain>.py -v`

---

## Step 6: Add a Next.js route handler (BFF)

Client components must not call FastAPI directly. Add a route handler:

```
frontend/app/api/<domain>/route.ts
```

```typescript
import { fastapiClient } from '@/lib/fastapi';
import { MyResponseSchema } from '@/types';

export async function GET(request: Request) {
  try {
    const data = await fastapiClient.get('/api/v1/<domain>/');
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
4. Run `/doc-gap` to check nothing is missed
