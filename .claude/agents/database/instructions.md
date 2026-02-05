# Database Agent

## Responsibilities
- PostgreSQL schema design
- SQLAlchemy async models (User, Role, Document, QueryLog, IngestionJob)
- Alembic migration setup (async)
- Database session management

## Files Owned
- `backend/src/db/` — All database code
- `backend/alembic/` — Migrations
- `backend/alembic.ini`
- `backend/tests/test_db/`

## Models

### User
- id, username, email, created_at
- M2M relationship with Role via user_roles

### Role (Group)
- id, name, description
- Used for RBAC — maps to Qdrant `allowed_groups` payload field

### Document
- id, filename, file_type, source, author, department, uploaded_by (FK User)
- chunk_count, created_at, updated_at
- Tracks metadata — actual vectors are in Qdrant

### QueryLog (Audit)
- id, user_id (FK User), query_text, response_text
- sources (JSON), latency_ms, created_at

### IngestionJob
- id, document_id (FK Document), status (pending/processing/completed/failed)
- chunks_created, error_message, started_at, completed_at

## Acceptance Criteria
- `alembic upgrade head` creates all tables without errors
- User ↔ Role M2M works correctly
- Document CRUD operations pass
- All models have created_at/updated_at timestamps
