# Integration Agent — PHASE 2 (DEFERRED)

## Status
Not built in Phase 1. Manual file upload only for MVP.
Will be implemented when company tooling is known (Slack, Salesforce, SharePoint, etc.).

## Design Principle
Pluggable connector interface. Each integration implements BaseConnector.

## Interface (designed, not built)
```python
from abc import ABC, abstractmethod
from datetime import datetime

class BaseConnector(ABC):
    @abstractmethod
    async def list_documents(self) -> list[DocumentRef]:
        """List available documents from the source."""
        ...

    @abstractmethod
    async def download(self, ref: DocumentRef) -> bytes:
        """Download a specific document."""
        ...

    @abstractmethod
    async def sync_new(self, since: datetime) -> list[DocumentRef]:
        """Find documents created/modified since the given datetime."""
        ...
```

## Files (Phase 2)
- `backend/src/integrations/base.py` — Abstract BaseConnector
- `backend/src/integrations/connectors/` — One module per integration
- `backend/src/integrations/scheduler.py` — Daily batch trigger

## Acceptance Criteria (Phase 2)
- New connector implements BaseConnector
- Scheduler calls sync_new() daily
- Documents flow through existing ingestion pipeline
