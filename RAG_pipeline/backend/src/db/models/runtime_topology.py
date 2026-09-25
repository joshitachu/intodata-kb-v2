"""Persisted, scanner-derived topology facts used for runtime correlation.

The scanner remains the authority for static structure.  This module stores a
bounded, revisioned copy of only the facts needed to correlate a runtime
execution; it never stores raw source files or credentials.
"""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class RuntimeStaticTopologySnapshotRow(Base):
    """One immutable scanner topology snapshot for a project revision."""

    __tablename__ = "runtime_static_topology_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "scan_revision",
            name="uq_runtime_static_topology_project_revision",
        ),
        UniqueConstraint(
            "project_id", "source_sequence",
            name="uq_runtime_static_topology_project_source_sequence",
        ),
        Index("ix_runtime_static_topology_project_sequence", "project_id", "source_sequence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    scan_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    source_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    platform: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    received_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RuntimeJobScanMapRow(Base):
    """Explicit mapping from a provider task identifier to one static artifact.

    A static artifact may legitimately be deployed as several runtime tasks, so
    uniqueness is intentionally on the runtime-side identity only.
    """

    __tablename__ = "runtime_job_scan_maps"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "source_key", "runtime_job_id",
            name="uq_runtime_job_scan_map_project_source_runtime_job",
        ),
        Index("ix_runtime_job_scan_maps_project", "project_id"),
        Index("ix_runtime_job_scan_maps_static_job", "project_id", "static_job_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_key: Mapped[str] = mapped_column(String(100), nullable=False)
    runtime_job_id: Mapped[str] = mapped_column(String(255), nullable=False)
    static_job_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # Empty means the mapping is valid for every revision where static_job_id
    # still exists.  A publisher can set this to pin a mapping deliberately.
    scan_revision: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # How this row came to exist.  Two values, and the difference is a rule
    # rather than a label: ``manual`` is a human's decision and the automatic
    # resolver must never overwrite it, while ``static_ref:<field>`` records
    # which provider-declared field resolved it and is therefore free to be
    # recomputed on every sync.  Without it the resolver cannot tell its own
    # output from a correction someone made by hand, and re-running would
    # silently undo the correction.
    resolved_by: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="manual"
    )
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
