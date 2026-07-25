# Security and Consistency Recommendations

This document summarizes consistent security and operational gaps across the repository and provides concrete fix patterns.

## Scope Reviewed

- module1
- module2
- module3

## Current State Summary

- Module 2: Strong baseline for API hardening and model guardrails.
- Module 3: Recently aligned with Module 2 for API, model, evaluator, tool, and template hardening.
- Module 1: Main outlier. It still uses a minimal fallback HTTP server and looser guardrails.

## Priority 1 Recommendations

### 1) Align Module 1 HTTP Security with Module 2/3

Gap:

- module1/app.py fallback server has no authentication.
- Missing response security headers.
- No request body size limit.
- No rate limiting.
- No correlation ID propagation.
- Error responses are not standardized.

Why it matters:

- Any exposed endpoint without auth/rate limits is vulnerable to abuse.
- Inconsistent error and tracing behavior increases incident-response time.

Example fix pattern:

```python
# module1/app.py
MAX_BODY_BYTES = int(os.getenv("MODULE1_MAX_BODY_BYTES", "32768"))
RATE_LIMIT_REQUESTS = int(os.getenv("MODULE1_RATE_LIMIT_REQUESTS", "20"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("MODULE1_RATE_LIMIT_WINDOW_SECONDS", "60"))
API_KEY = os.getenv("MODULE1_API_KEY", "").strip()

SECURITY_RESPONSE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'",
}

def error_payload(error_code: str, message: str, retry_with: str, escalate: bool, correlation_id: str) -> dict:
    return {
        "error_code": error_code,
        "error": message,
        "retry_with": retry_with,
        "escalate": escalate,
        "correlation_id": correlation_id,
    }
```

Also apply:

- Authorization check via X-API-Key and/or Bearer token.
- UUID correlation ID acceptance in X-Correlation-ID.
- Sliding-window rate limiting by client IP.
- Content-Type and Content-Length validation.

### 2) Standardize Auth Modes Across Modules

Gap:

- Auth behavior and env-var naming differ by module.
- Module 1 does not currently have policy parity.

Why it matters:

- Teams misconfigure production when policies differ between services.

Recommendation:

- Keep per-module variable prefixes, but standardize semantics.
- Use one shared policy contract in docs:
  - API key optional in local-dev, required in non-dev.
  - Optional JWT mode with issuer/audience/JWKS.
  - Optional RBAC scopes/roles.

Example policy matrix:

- No key + no JWT: local-dev only
- API key set: key auth required
- JWT configured: bearer required
- Both configured: either accepted

### 3) Add Uniform Security Header Baseline to Every HTTP Path

Gap:

- Module 2/3 have this now; Module 1 fallback path does not.

Recommendation:

- Add the same SECURITY_RESPONSE_HEADERS dict and response helper to all handlers.
- Ensure GET/POST/OPTIONS all include headers.

Example:

```python
def send_json(self, status: int, payload: dict, correlation_id: str) -> None:
    body = json.dumps(payload).encode()
    self.send_response(status)
    self.send_header("Content-Type", "application/json")
    self.send_header("Content-Length", str(len(body)))
    self.send_header("X-Correlation-ID", correlation_id)
    for k, v in SECURITY_RESPONSE_HEADERS.items():
        self.send_header(k, v)
    self.end_headers()
    self.wfile.write(body)
```

## Priority 2 Recommendations

### 4) Unify Model Guardrails in Module 1

Gap:

- module1/config/models.py currently does not validate:
  - region format
  - temperature range
  - max_tokens limits
  - model ID allowlist

Why it matters:

- Prevents accidental high-cost prompts and unauthorized model usage.

Example fix pattern:

```python
_REGION_RE = re.compile(r"^[a-z]{2}-[a-z]+-\d+$")
_MAX_TOKENS_LIMIT = 8192

ALLOWED_MODEL_IDS = set(
    m.strip() for m in os.getenv("MODULE1_ALLOWED_MODEL_IDS", "").split(",") if m.strip()
) or {
    "us.anthropic.claude-sonnet-4-20250514-v1:0",
    "anthropic.claude-sonnet-4-20250514-v1:0",
}
```

Then enforce validation in get_bedrock_model and get_hf_bedrock_model.

### 5) Standardize Tool Output Contract and Telemetry

Gap:

- Tool output envelopes are different between modules.
- Module 2 tools include richer telemetry fields (status, latency, correlation_id).

Recommendation:

- Define one envelope schema used by all tool modules:
  - tool
  - input
  - status
  - latency_ms
  - correlation_id
  - timestamp
  - mock_mode
  - data

Example schema:

```json
{
  "tool": "list_aws_resources",
  "input": {"service_type": "ecs", "region": "us-east-1"},
  "status": "success",
  "latency_ms": 42,
  "correlation_id": "550e8400-e29b-41d4-a716-446655440000",
  "timestamp": "2026-07-25T12:00:00+00:00",
  "mock_mode": false,
  "data": {"count": 3}
}
```

### 6) Add Output Redaction and Safe Logging Rules

Gap:

- Tool outputs and errors may include verbose values that could expose internals.

Recommendation:

- Redact known sensitive fields before logging or returning debug payloads:
  - authorization, token, api_key, secret, password
- Truncate oversized string fields in logs.
- Return generic 500 messages to clients, detailed stack traces only in server logs.

## Priority 3 Recommendations

### 7) Add Security Regression Tests

Gap:

- Security controls exist, but regression tests are not comprehensive across modules.

Recommendation:

- Add HTTP tests for each module app:
  - unauthorized request returns 401
  - wrong content type returns 415
  - oversized body returns 413
  - invalid JSON returns 400
  - correlation ID returned in response
  - expected security headers always present

Example pytest shape:

```python
def test_rejects_unauthorized(client):
    resp = client.post("/analyze", json={"requirements": {"compute": "ECS"}})
    assert resp.status_code == 401
    body = resp.json()
    assert body["error_code"] == "UNAUTHORIZED"
```

### 8) Create a Shared Security Utility Module

Gap:

- Similar logic is duplicated (auth, JWT, headers, correlation IDs).

Recommendation:

- Create shared/security.py and reuse in module1/module2/module3:
  - request ID parsing
  - auth checks
  - rate limiting
  - error payload creation
  - response headers

Benefits:

- Lower maintenance cost.
- Fewer drift bugs.
- Consistent behavior by default.

## Suggested Rollout Plan

1. Bring Module 1 fallback server to Module 2/3 parity first.
2. Refactor repeated security helpers into a shared utility.
3. Add module-level security regression tests.
4. Add CI gate to run only security test subset on pull requests.

## Environment Variable Reference (Recommended Standard)

- MODULE1_API_KEY / AGENT_API_KEY / MODULE3_API_KEY
- MODULE1_MAX_BODY_BYTES / AGENT_MAX_BODY_BYTES / MODULE3_MAX_BODY_BYTES
- MODULE1_RATE_LIMIT_REQUESTS / AGENT_RATE_LIMIT_REQUESTS / MODULE3_RATE_LIMIT_REQUESTS
- MODULE1_RATE_LIMIT_WINDOW_SECONDS / AGENT_RATE_LIMIT_WINDOW_SECONDS / MODULE3_RATE_LIMIT_WINDOW_SECONDS
- MODULE1_JWT_ISSUER / AGENT_JWT_ISSUER / MODULE3_JWT_ISSUER
- MODULE1_JWT_AUDIENCE / AGENT_JWT_AUDIENCE / MODULE3_JWT_AUDIENCE
- MODULE1_JWT_JWKS_URL / AGENT_JWT_JWKS_URL / MODULE3_JWT_JWKS_URL

If preferred, migrate all modules to one canonical prefix over time (for example, AGENT_*) with backward-compatible aliases during transition.
