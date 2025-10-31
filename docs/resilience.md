# Hardening & Resilience

This codebase includes resilience features to improve latency and stability under load spikes (e.g., 2× traffic):

- Outbound HTTP calls use timeouts, retries with exponential backoff, and circuit breakers (Telegram, webhook, automation).
- OpenAI calls have a per‑model circuit breaker and quick fallback to keep P95 latency under 1.8s.
- Metrics endpoints are cached in‑memory for 60 seconds to reduce server load during spikes.

## Configuration

Environment variables (defaults in `app/config.py`):

- `HTTP_TIMEOUT_SEC` (default 8)
- `HTTP_MAX_RETRIES` (default 2)
- `HTTP_BACKOFF_BASE_MS` (default 120)
- `HTTP_CIRCUIT_FAIL_THRESHOLD` (default 5)
- `HTTP_CIRCUIT_RESET_SEC` (default 30)
- `AI_MAX_RETRIES` (default 1)
- `AI_CIRCUIT_FAIL_THRESHOLD` (default 3)
- `AI_CIRCUIT_RESET_SEC` (default 20)
- `METRICS_CACHE_TTL_SECONDS` (default 60)

## k6 Quick Test

Run a quick k6 spike test to validate P95 < 1.8s and low failure rate:

```
k6 run -e BASE_URL=http://localhost:8000 scripts/k6_quick_test.js
```

Thresholds:

- `http_req_duration` p(95) < 1800 ms
- `http_req_failed` < 1%
- `checks` > 99%

