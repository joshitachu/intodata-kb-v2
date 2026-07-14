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

harness_planner_decisions_total = Counter(
    "harness_planner_decisions_total",
    "Harness chat routing decisions by decision source and resulting action",
    ["source", "action"],
)

harness_planner_fallback_total = Counter(
    "harness_planner_fallback_total",
    "Harness planner fallbacks to deterministic routing, by reason",
    ["reason"],
)
