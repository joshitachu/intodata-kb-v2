"""Persist the runtime's own diagnostic sentence and its extraction provenance.

The insight layer composed every user-facing text from extracted tokens
(counts + error code + exception class + component) and discarded the sentence
the runtime itself wrote to explain the fault. That sentence is frequently the
whole diagnosis — "Invalid dataset ID \"test_ zaf_del_clean\"" or
"No such column 'X' on entity 'User'" — and no amount of token recomposition
reconstructs it. These columns store it (credential-redacted) alongside the
existing structured text, which is retained unchanged.

Columns added:

  runtime_execution_summaries.causal_message  the diagnostic sentence for the
      execution's terminal fault.
  runtime_incidents.causal_message            the same sentence carried onto the
      incident, so the root-cause surface can quote evidence instead of a template.
  runtime_error_signatures.causal_message     a representative sentence for the
      recurring signature, whose summary_text was previously only
      "<code> / <exception> / <component>".
  runtime_enriched_events.dialect             which log dialect claimed the line.
      Not cosmetic: a population of NULL dialect is the measurable signal that a
      runtime's log shape is unrecognised. The parser previously matched only the
      seeded fixture dialect and silently extracted nothing from real Talend
      logs, and nothing in the schema could reveal that.
  runtime_enriched_events.job_name            the Talend job name, which the TMC
      execution-status dialect states explicitly. It is the join key toward
      runtime_job_scan_maps.static_job_id (runtime fault -> scanned code).

Revision ID: 081_runtime_causal_meaning
Revises: 080_runtime_duration_bigint
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "081_runtime_causal_meaning"
down_revision: str | Sequence[str] | None = "080_runtime_duration_bigint"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CAUSAL_MESSAGE_TABLES = (
    "runtime_execution_summaries",
    "runtime_incidents",
    "runtime_error_signatures",
)


def upgrade() -> None:
    for table_name in _CAUSAL_MESSAGE_TABLES:
        op.add_column(table_name, sa.Column("causal_message", sa.Text(), nullable=True))
    op.add_column(
        "runtime_enriched_events", sa.Column("dialect", sa.String(length=40), nullable=True)
    )
    op.add_column(
        "runtime_enriched_events", sa.Column("job_name", sa.String(length=255), nullable=True)
    )
    # Coverage queries ("how many rows did no dialect claim?") scan by dialect
    # within a project; without this they are a full scan of the largest
    # insight table.
    op.create_index(
        "ix_runtime_enriched_events_project_dialect",
        "runtime_enriched_events",
        ["project_id", "dialect"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_runtime_enriched_events_project_dialect", table_name="runtime_enriched_events"
    )
    op.drop_column("runtime_enriched_events", "job_name")
    op.drop_column("runtime_enriched_events", "dialect")
    for table_name in reversed(_CAUSAL_MESSAGE_TABLES):
        op.drop_column(table_name, "causal_message")
