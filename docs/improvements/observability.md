# 1. Observability stack

## Problem

Structured logs exist (`app/core/logging.py`) but there's no way to answer questions like:

- "Why is account #17's farming tick taking 40 s today vs 12 s yesterday?"
- "How many 403s are we seeing across the proxy fleet this hour?"
- "Which controller is burning the most CPU on this pod?"

At two accounts that's fine — grep the one log stream. At fifty accounts across three worker pods, greps don't cut it, and when your first customer reports "my bot stopped raiding 20 minutes ago," you don't have time to reconstruct why from raw JSON.

## Design

Four-piece standard stack. None of it is novel; the value is picking a single opinionated layout and sticking to it.

### Metrics — Prometheus + per-process exporter

Add `prometheus-client` to the Python deps. Expose `/metrics` on the API pod (already a FastAPI app) and on each worker pod (new tiny aiohttp sidecar on port 9000).

Metric taxonomy:

```
travianbot_reconcile_duration_seconds{controller, account_id}        histogram
travianbot_reconcile_errors_total{controller, account_id}            counter
travianbot_proxy_errors_total{proxy_id, kind}                        counter
travianbot_playwright_context_bytes{account_id}                      gauge
travianbot_account_lease_age_seconds{account_id}                     gauge
travianbot_http_requests_total{method, path, status}                 counter
travianbot_raid_outcome_total{account_id, outcome}                   counter
travianbot_controller_last_success_seconds{controller, account_id}   gauge (now - last ok)
```

The `account_id` label is the important one — it makes per-tenant dashboards trivially composable later.

### Logs — Loki or CloudWatch

Already structured via structlog. All that's needed:

- Consistent top-level keys across every log line: `ts`, `level`, `event`, `account_id`, `controller`, `trace_id`.
- Ship via Promtail (Loki) or Fluent Bit (CloudWatch) sidecar on each pod.
- Retain 14 days hot, 90 days cold.

### Dashboards — Grafana

Three dashboards to start:

1. **Fleet overview** — total active accounts, active leases, controller error rate, p95 reconcile latency, proxy health distribution.
2. **Per-account** — variable `$account_id`, all the gauges/counters filtered by that label, most recent 50 log lines.
3. **Platform** — DB connections, pod CPU/memory, Playwright context memory, lease table churn.

### Alerting — Alertmanager → PagerDuty / Discord

Minimum rule set:

| Alert | Condition | Severity |
| --- | --- | --- |
| Account silent | `travianbot_controller_last_success_seconds{controller="troops"} > 1800` | warning |
| Lease starvation | `active_leases < active_accounts * 0.95 for 5m` | page |
| Proxy fleet degraded | `proxies_healthy / proxies_total < 0.8` | page |
| Ban event | rate of `account.status = 'banned'` > 0 | page |
| Stripe webhook retries exhausted | any occurrence | page |
| Reconcile error spike | controller error rate > 5× baseline | warning |

Route pages to PagerDuty if we're on call 24/7; a single `#ops` Discord channel is fine until there are actual paying customers.

## Integration points

- `app/core/reconciler.py:Controller.run_once` — wrap `reconcile()` in a histogram timer, bump error counter on exception.
- `app/core/account_manager.py:AccountWorker` — emit `travianbot_account_lease_age_seconds` from heartbeat.
- `app/browser/session.py` — emit `playwright_context_bytes` on context close.
- `app/api/main.py` — add `prometheus_fastapi_instrumentator` middleware for HTTP metrics.
- New `app/core/metrics.py` — central registry, one module any file can import.

## Tradeoffs / open questions

- **Self-hosted vs. managed.** Grafana Cloud free tier covers ~3 users and 10k metrics — probably enough through phase 3. Self-hosted is cheaper at scale but adds three containers to babysit. Start managed.
- **Per-account cardinality.** `account_id` as a label can blow up at 10k+ accounts. Well below that now. If/when it matters, move to per-tenant labels and drop per-account from counters.
- **Trace IDs.** FastAPI middleware can inject a per-request trace ID; harder to propagate into the async AccountWorker loops. A simple `contextvars`-based scheme works — document before implementing.

## Effort

~3 days for a competent engineer, most of it wiring and dashboard JSON. Pays back the first incident.
