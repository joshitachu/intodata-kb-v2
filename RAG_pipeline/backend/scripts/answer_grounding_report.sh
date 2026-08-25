#!/usr/bin/env bash
#
# Answer grounding report -- how often does the assistant answer without reading anything?
#
# An answer is UNGROUNDED when the turn completed with stop_reason='answered' but the
# assistant message carries no primary_source_layer, i.e. no document, no topology
# artifact and no runtime data was read before answering. Some ungrounded answers are
# correct by design (follow-ups the context already covers, refusals, safety probes),
# so this is a number to WATCH and explain, not a number to drive to zero.
#
# Reported per chat surface, because the two surfaces share one assistant and one tool
# set: conversation_messages.page_scope->>'surface' is 'topology' for the docked canvas
# copilot and NULL for /chat. page_scope is client-supplied and untrusted -- fine as a
# reporting dimension, never an authority.
#
# Usage:
#   ./scripts/answer_grounding_report.sh            # last 7 days
#   ./scripts/answer_grounding_report.sh 30         # last 30 days
#   ./scripts/answer_grounding_report.sh 7 --sample # also list the ungrounded questions
#
# Overridable:
#   PG_CONTAINER (default intodata-kb-v2-postgres-1), PG_USER (admin), PG_DB (intodata_kb)

set -euo pipefail

DAYS="${1:-7}"
SAMPLE="${2:-}"
PG_CONTAINER="${PG_CONTAINER:-intodata-kb-v2-postgres-1}"
PG_USER="${PG_USER:-admin}"
PG_DB="${PG_DB:-intodata_kb}"

if ! [[ "$DAYS" =~ ^[0-9]+$ ]]; then
  echo "First argument must be a number of days (got: $DAYS)" >&2
  exit 2
fi

psql_run() {
  docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1 "$@"
}

# Every query below builds on this shape, so it is defined once and inlined.
# tool_calls excludes nexus_turn_assess: that is the runtime's own bookkeeping
# call, not a read the model chose to make, and counting it would mask exactly
# the turns this report exists to find.
read -r -d '' TURNS_CTE <<SQL || true
with turns as (
  select
    r.id,
    r.runtime_cohort,
    r.started_at,
    coalesce(um.page_scope->>'surface', 'chat') as surface,
    am.primary_source_layer,
    am.id as assistant_message_id,
    um.content as question,
    count(s.id) filter (
      where s.semantic_name is not null and s.semantic_name <> 'nexus_turn_assess'
    ) as tool_calls
  from harness_agent_runs r
  join conversation_messages um on um.id = r.user_message_id
  left join conversation_messages am on am.id = r.assistant_message_id
  left join harness_agent_steps s on s.agent_run_id = r.id
  where r.started_at >= now() - interval '${DAYS} days'
    and r.stop_reason = 'answered'
    and r.status = 'completed'
    and am.deleted_at is null
  group by r.id, r.runtime_cohort, r.started_at,
           coalesce(um.page_scope->>'surface', 'chat'),
           am.primary_source_layer, am.id, um.content
)
SQL

echo
echo "=== Answer grounding -- last ${DAYS} days ==============================="
echo

# primary_source_layer is written by src/harness/turn_provenance.py and only began
# landing on 2026-08-24. Rows older than that are NULL because nothing wrote them,
# not because the answer was ungrounded. Reporting across that boundary silently
# returns ~100% ungrounded, which reads as a catastrophe and is an artefact. Refuse
# to average across it: find the real start and say so.
METRIC_START=$(psql_run -tA <<'SQL'
select coalesce(min(created_at)::date::text, 'never')
from conversation_messages
where primary_source_layer is not null;
SQL
)

if [[ "$METRIC_START" == "never" ]]; then
  echo "primary_source_layer has never been populated in this database." >&2
  echo "Nothing to report -- check src/harness/turn_provenance.py is on this build." >&2
  exit 1
fi

WINDOW_START=$(psql_run -tA -c "select (now() - interval '${DAYS} days')::date;")
echo "  metric first recorded: ${METRIC_START}"
echo "  window starts:         ${WINDOW_START}"
if [[ "$WINDOW_START" < "$METRIC_START" ]]; then
  echo
  echo "  WARNING: this window starts before the metric existed. Rows before"
  echo "  ${METRIC_START} are NULL because nothing wrote them, so the percentages"
  echo "  below are understated as grounded / overstated as ungrounded."
  echo "  For a trustworthy number, use a window starting on or after ${METRIC_START}."
fi
echo

psql_run <<SQL
${TURNS_CTE}
select
  surface,
  count(*)                                              as answers,
  count(*) filter (where primary_source_layer is null)  as ungrounded,
  round(100.0 * count(*) filter (where primary_source_layer is null)
        / nullif(count(*), 0), 1)                       as pct_ungrounded,
  count(*) filter (where tool_calls = 0)                as zero_tool,
  round(avg(tool_calls), 2)                             as avg_tool_calls
from turns
group by surface
order by answers desc;
SQL

echo "--- Weekly trend (is it getting better or worse?) ----------------------"
psql_run <<SQL
${TURNS_CTE}
select
  date_trunc('week', started_at)::date                  as week,
  surface,
  count(*)                                              as answers,
  count(*) filter (where primary_source_layer is null)  as ungrounded,
  round(100.0 * count(*) filter (where primary_source_layer is null)
        / nullif(count(*), 0), 1)                       as pct
from turns
group by 1, 2
order by 1 desc, 3 desc;
SQL

echo "--- What DID get read, when something was read -------------------------"
psql_run <<SQL
${TURNS_CTE}
select
  surface,
  coalesce(primary_source_layer, '(nothing read)')      as source_layer,
  count(*)                                              as answers
from turns
group by 1, 2
order by 1, 3 desc;
SQL

if [[ "$SAMPLE" == "--sample" ]]; then
  echo "--- Ungrounded questions (truncated; eyeball for false positives) ------"
  echo "    Expect some legitimate ones: follow-ups, refusals, safety probes."
  psql_run <<SQL
${TURNS_CTE}
select
  surface,
  tool_calls,
  left(regexp_replace(question, '\s+', ' ', 'g'), 90) as question
from turns
where primary_source_layer is null
order by started_at desc
limit 40;
SQL
fi

echo
echo "Baseline recorded 2026-08-25 (7-day window): chat 33 ungrounded / 76 answers,"
echo "topology 33 / 102. Compare against that when judging whether a change helped."
echo
