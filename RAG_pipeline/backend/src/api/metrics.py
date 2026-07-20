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
