---
name: fastapi-patterns
description: Structural patterns for the FastAPI services here - app factory and lifespan, pydantic-settings config, Pydantic v2 schema boundaries, async dependency injection, thin routes over a service layer, and httpx tests. Use when adding or restructuring routes, schemas, or dependencies.
---

# FastAPI Patterns

Applies to both Python services: `RAG_pipeline/backend` (port 8000) and
`knowledge_base/mcp-server`. Both are `fastapi>=0.115`, async SQLAlchemy,
`pydantic-settings`, **PyJWT** and **bcrypt**.

Read the existing code before following any snippet here. Where this file and the code
disagree, the code wins and this file is stale -- say so rather than "fixing" the code
to match a document.

## Layering

```
routers/     HTTP only: parse, validate, delegate, shape the response
services/    business logic, transaction boundaries, no FastAPI imports
db/          models and session management
schemas/     Pydantic v2 request/response contracts
```

The rule that keeps this honest: **a service must not import from `fastapi`.** If it
needs `HTTPException`, it is doing HTTP's job. Raise a domain error and let the router
translate it.

## Lifespan, not startup events

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()          # acquire: pools, clients, warm caches
    yield
    await close_db()         # release: always, even on failure

app = FastAPI(lifespan=lifespan)
```

Anything acquired here must be released after the `yield`. A leaked Qdrant or httpx
client survives reload and exhausts connections slowly, which presents as unrelated
timeouts hours later.

## Config

`pydantic-settings`, one `Settings`, imported everywhere. Never read `os.environ`
directly in a route or service -- it bypasses validation and defaults.

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str
    llm_max_output_tokens: int = Field(default=8192, ge=256, le=32768)
```

Give every numeric setting a validated range. An unbounded int from the environment is
a production incident waiting for a typo.

## Schemas: the leak to watch for

Separate the model from what leaves the process. This is the highest-frequency real bug
in a FastAPI codebase:

```python
class UserPublic(BaseModel):            # what callers see
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: EmailStr

class UserInDB(UserPublic):             # never a response_model
    password_hash: str
    totp_secret: str | None
```

Always set `response_model` explicitly. Returning an ORM object directly serialises
whatever the model has, including columns added later -- so a future migration becomes
a data leak with no code change.

## Dependency injection

```python
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session

SessionDep = Annotated[AsyncSession, Depends(get_session)]
```

Auth with PyJWT (not python-jose -- that is a different library with a different API):

```python
import jwt   # PyJWT

async def current_user(token: Annotated[str, Depends(oauth2_scheme)],
                       session: SessionDep) -> User:
    try:
        payload = jwt.decode(token, settings.secret_key,
                             algorithms=[settings.algorithm])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "token expired")
    except jwt.InvalidTokenError:            # covers signature, malformed, claims
        raise HTTPException(401, "invalid token")
    ...
```

Catch `InvalidTokenError` as the base case. Catching bare `Exception` around
`jwt.decode` turns a forged token into a 500 and hides the attempt.

Password hashing is bcrypt directly:

```python
import bcrypt
bcrypt.hashpw(pw.encode(), bcrypt.gensalt())
bcrypt.checkpw(pw.encode(), stored_hash)
```

## Routes stay thin

```python
@router.post("/users", response_model=UserPublic, status_code=201)
async def create_user(payload: UserCreate, session: SessionDep):
    try:
        return await user_service.create(session, payload)
    except EmailAlreadyExists:
        raise HTTPException(409, "email already registered")
```

The router maps domain errors to status codes. That mapping is the router's only real
logic.

## Testing

```python
@pytest_asyncio.fixture
async def client(session):
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
```

Override the **same callable** the route depends on. Overriding a wrapper that the
route does not actually use is the classic silent no-op: tests pass against the real
database and you do not notice until they are slow or destructive. Always clear
overrides in teardown.

## Anti-patterns

```python
# Sync driver in an async route -- blocks the whole event loop.
@router.get("/x")
async def x():
    return requests.get(url).json()          # use httpx.AsyncClient

# Session built inside the handler -- unclosed on exception, untestable.
async def handler():
    session = async_session_maker()          # use the dependency

# Credentialed wildcard CORS -- the browser rejects it and it is unsafe anyway.
CORSMiddleware(allow_origins=["*"], allow_credentials=True)

# No timeout: one slow upstream exhausts the pool.
httpx.AsyncClient()                          # AsyncClient(timeout=30.0)

# Unbounded list endpoint.
return await session.scalars(select(Job))    # paginate
```

## Before committing a route change

`knowledge_base/mcp-server/src/mcp_server/api/` is a critical area -- run `inspect`,
and consider the `fastapi-reviewer` agent for async and schema-leak depth.
