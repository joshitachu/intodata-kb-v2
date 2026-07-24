from prometheus_client import Counter, Histogram

http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status_code"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "path"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

agent_chat_turns_total = Counter(
    "agent_chat_turns_total",
    "Authoritative chat turns by transport and outcome",
    ["transport", "outcome"],
)

agent_runtime_canary_turns_total = Counter(
    "agent_runtime_canary_turns_total",
    "Authoritative turns by finite canary stage, runtime cohort, transport, and outcome",
    ["stage", "cohort", "transport", "outcome"],
)

agent_runtime_canary_latency_seconds = Histogram(
    "agent_runtime_canary_latency_seconds",
    "Canary turn latency by finite stage, runtime cohort, and transport",
    ["stage", "cohort", "transport"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0],
)

agent_runtime_canary_cost_usd = Histogram(
    "agent_runtime_canary_cost_usd",
    "Canary turn cost by finite stage and runtime cohort",
    ["stage", "cohort"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.25],
)

agent_runtime_canary_guard_total = Counter(
    "agent_runtime_canary_guard_total",
    "Canary guard observations by finite stage, cohort, guard, and result",
    ["stage", "cohort", "guard", "result"],
)

agent_chat_guard_total = Counter(
    "agent_chat_guard_total",
    "Agent chat guard observations with finite labels",
    ["guard", "result"],
)

agent_chat_latency_seconds = Histogram(
    "agent_chat_latency_seconds",
    "Authoritative agent chat turn latency",
    ["transport"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 30.0],
)

agent_chat_cost_usd = Histogram(
    "agent_chat_cost_usd",
    "Authoritative agent chat cost in USD",
    [],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.25],
)

agent_chat_tool_calls = Histogram(
    "agent_chat_tool_calls",
    "Registered capability calls per agent turn",
    [],
    buckets=[0, 1, 2, 3, 4, 6, 8, 12],
)

agent_run_public_events_total = Counter(
    "agent_run_public_events_total",
    "Persisted sanitized Agent Run public events",
    ["event_type"],
)

agent_run_stops_total = Counter(
    "agent_run_stops_total",
    "Terminal Agent Runs by status and bounded stop reason",
    ["status", "stop_reason"],
)

agent_run_model_calls = Histogram(
    "agent_run_model_calls",
    "Standard-runtime model calls per durable Agent Run",
    [],
    buckets=[0, 1, 2, 3, 4, 6, 8, 12, 16],
)

agent_run_tool_calls = Histogram(
    "agent_run_tool_calls",
    "Standard-runtime tool calls per durable Agent Run",
    [],
    buckets=[0, 1, 2, 3, 4, 6, 8, 12, 16],
)
