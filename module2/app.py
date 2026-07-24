"""
module2/app.py
==============
Standalone HTTP server for Module 2 Repository Analysis Agent.

This provides a simple HTTP interface for repository analysis without
requiring AgentCore deployment. Useful for local development and testing.

ENDPOINTS
---------
POST /analyze  - Analyze a repository
GET  /ping     - Health check

USAGE
-----
  # Start the server
  python module2/app.py

  # Or with mock mode
  AGENT_MOCK_REPO=true python module2/app.py

  # Analyze a repository
  curl -X POST http://localhost:8081/analyze \\
    -H "Content-Type: application/json" \\
    -d '{"repo_path": "/path/to/repo"}'
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Lock
from typing import Any

try:
    import jwt
    from jwt import PyJWKClient
    from jwt.exceptions import InvalidTokenError
except Exception:  # pragma: no cover - optional dependency
    jwt = None
    PyJWKClient = None
    InvalidTokenError = Exception

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from module2.agent import create_agent


# ---------------------------------------------------------------------------
# Security Configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAX_BODY_BYTES = int(os.getenv("AGENT_MAX_BODY_BYTES", "32768"))
RATE_LIMIT_REQUESTS = int(os.getenv("AGENT_RATE_LIMIT_REQUESTS", "20"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("AGENT_RATE_LIMIT_WINDOW_SECONDS", "60"))
ALLOW_VERBOSE = os.getenv("AGENT_ALLOW_VERBOSE", "false").lower() == "true"
JWT_ISSUER = os.getenv("AGENT_JWT_ISSUER", "").strip()
JWT_AUDIENCE = os.getenv("AGENT_JWT_AUDIENCE", "").strip()
JWT_JWKS_URL = os.getenv("AGENT_JWT_JWKS_URL", "").strip()
JWT_LEEWAY_SECONDS = int(os.getenv("AGENT_JWT_LEEWAY_SECONDS", "30"))
JWT_ALGORITHMS = tuple(
    algo.strip()
    for algo in os.getenv("AGENT_JWT_ALGORITHMS", "RS256").split(",")
    if algo.strip()
)
JWT_REQUIRED_SCOPES = {
    scope.strip()
    for scope in os.getenv("AGENT_JWT_REQUIRED_SCOPES", "").split(",")
    if scope.strip()
}
JWT_REQUIRED_ROLES = {
    role.strip()
    for role in os.getenv("AGENT_JWT_REQUIRED_ROLES", "").split(",")
    if role.strip()
}
JWT_SCOPE_CLAIMS = tuple(
    claim.strip()
    for claim in os.getenv("AGENT_JWT_SCOPE_CLAIMS", "scope,scp").split(",")
    if claim.strip()
)
JWT_ROLE_CLAIMS = tuple(
    claim.strip()
    for claim in os.getenv("AGENT_JWT_ROLE_CLAIMS", "roles,cognito:groups,groups").split(",")
    if claim.strip()
)

JWT_ENABLED = bool(JWT_ISSUER and JWT_AUDIENCE and JWT_JWKS_URL)

if JWT_ENABLED and PyJWKClient is not None:
    _JWKS_CLIENT = PyJWKClient(JWT_JWKS_URL)
else:
    _JWKS_CLIENT = None

SECURITY_RESPONSE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'",
}

_RATE_LIMIT_LOCK = Lock()
_REQUEST_LOG: dict[str, deque[float]] = defaultdict(deque)


def _parse_allowed_roots() -> list[Path]:
    """Parse and validate allowed repository roots from environment."""
    raw = os.getenv("AGENT_ALLOWED_REPO_ROOTS", "")
    if not raw.strip():
        # Secure default: only allow repositories under this project root.
        return [PROJECT_ROOT]

    roots: list[Path] = []
    for root in raw.split(os.pathsep):
        value = root.strip()
        if not value:
            continue
        path_obj = Path(value).expanduser().resolve()
        if path_obj.exists() and path_obj.is_dir():
            roots.append(path_obj)

    return roots if roots else [PROJECT_ROOT]


ALLOWED_REPO_ROOTS = _parse_allowed_roots()


# ---------------------------------------------------------------------------
# Shared agent instance
# ---------------------------------------------------------------------------

print("\n  Initializing Module 2 Repository Analysis Agent...")
_agent = create_agent(verbose=False)
print("  Agent ready.\n")


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

def _handle_analysis(payload: dict) -> dict:
    """
    Process a repository analysis request.

    Expected payload
    ----------------
    {
      "repo_path": str   — required: absolute path to git repository
      "verbose": bool    — optional: print agent steps (default: false)
    }

    Returns
    -------
    dict
        Analysis results with applications, stacks, and AWS requirements.
    """
    repo_path = payload.get("repo_path", "").strip()
    if not repo_path:
        return {"error": "Missing required field: 'repo_path'"}

    repo = Path(repo_path).expanduser()
    if not repo.is_absolute():
        return {"error": "repo_path must be an absolute path"}

    try:
        resolved_repo = repo.resolve()
    except Exception:
        return {"error": "Invalid repo_path"}

    if not resolved_repo.exists() or not resolved_repo.is_dir():
        return {"error": "Repository path does not exist or is not a directory"}

    if not (resolved_repo / ".git").exists():
        return {"error": "Not a git repository (missing .git directory)"}

    allowed = any(
        resolved_repo == root or root in resolved_repo.parents
        for root in ALLOWED_REPO_ROOTS
    )
    if not allowed:
        return {
            "error": "Repository path is outside allowed roots",
            "allowed_roots": [str(root) for root in ALLOWED_REPO_ROOTS],
        }

    verbose_requested = bool(payload.get("verbose"))
    if verbose_requested and not ALLOW_VERBOSE:
        return {"error": "Verbose mode is disabled by server policy"}

    # Use verbose agent for local testing if requested
    if verbose_requested:
        local_agent = create_agent(verbose=True)
        result = local_agent.invoke({
            "input": f"Analyze the git repository at: {str(resolved_repo)}"
        })
    else:
        result = _agent.invoke({
            "input": f"Analyze the git repository at: {str(resolved_repo)}"
        })

    return {
        "repo_path": str(resolved_repo),
        "analysis": result.get("output", ""),
        "mock_mode": os.getenv("AGENT_MOCK_REPO", "false").lower() == "true",
        "framework": "langchain",
    }


def _extract_bearer_token(auth_header: str) -> str | None:
    """Extract bearer token from Authorization header."""
    prefix = "Bearer "
    if auth_header.startswith(prefix):
        token = auth_header[len(prefix):].strip()
        return token if token else None
    return None


def _claim_values(payload: dict[str, Any], claim_names: tuple[str, ...]) -> set[str]:
    """Extract normalized string values from one or more claims."""
    values: set[str] = set()
    for claim_name in claim_names:
        raw = payload.get(claim_name)
        if raw is None:
            continue
        if isinstance(raw, str):
            values.update(part for part in raw.split() if part)
            continue
        if isinstance(raw, (list, tuple, set)):
            for item in raw:
                if isinstance(item, str) and item.strip():
                    values.add(item.strip())

    return values


def _passes_rbac_claim_checks(payload: dict[str, Any]) -> bool:
    """Apply optional scope and role claim checks for RBAC."""
    if JWT_REQUIRED_SCOPES:
        token_scopes = _claim_values(payload, JWT_SCOPE_CLAIMS)
        if not JWT_REQUIRED_SCOPES.issubset(token_scopes):
            return False

    if JWT_REQUIRED_ROLES:
        token_roles = _claim_values(payload, JWT_ROLE_CLAIMS)
        if not JWT_REQUIRED_ROLES.issubset(token_roles):
            return False

    return True


def _validate_jwt_bearer(token: str) -> bool:
    """Validate JWT bearer token against issuer/audience and JWKS."""
    if not JWT_ENABLED:
        return False

    if jwt is None or _JWKS_CLIENT is None:
        # Fail closed when JWT policy is enabled but JWT dependencies are missing.
        return False

    try:
        signing_key = _JWKS_CLIENT.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=list(JWT_ALGORITHMS),
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
            leeway=JWT_LEEWAY_SECONDS,
            options={"require": ["exp"]},
        )
        return _passes_rbac_claim_checks(payload)
    except InvalidTokenError:
        return False
    except Exception:
        return False


def _is_authorized(headers: Any) -> bool:
    """Check API key and/or JWT bearer authorization based on server policy."""
    configured_key = os.getenv("AGENT_API_KEY", "").strip()
    if not configured_key and not JWT_ENABLED:
        # Local-dev compatibility mode if key is not configured.
        return True

    provided_key = headers.get("X-API-Key", "").strip()
    if provided_key and configured_key and secrets.compare_digest(provided_key, configured_key):
        return True

    auth_header = headers.get("Authorization", "").strip()
    bearer = _extract_bearer_token(auth_header) if auth_header else None
    if not bearer:
        return False

    if configured_key and secrets.compare_digest(bearer, configured_key):
        return True

    return _validate_jwt_bearer(bearer)


def _is_rate_limited(client_ip: str) -> bool:
    """Apply sliding-window rate limiting by client IP."""
    if RATE_LIMIT_REQUESTS <= 0 or RATE_LIMIT_WINDOW_SECONDS <= 0:
        return False

    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS

    with _RATE_LIMIT_LOCK:
        entries = _REQUEST_LOG[client_ip]
        while entries and entries[0] < cutoff:
            entries.popleft()

        if len(entries) >= RATE_LIMIT_REQUESTS:
            return True

        entries.append(now)
        return False


# ---------------------------------------------------------------------------
# HTTP Server
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    """HTTP request handler for repository analysis API."""

    def log_message(self, *_: object) -> None:
        """Suppress default access log."""
        pass

    def do_GET(self) -> None:
        """Handle GET requests."""
        if self.path == "/ping":
            self._respond(200, {"status": "ok", "service": "module2-repo-analysis"})
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self) -> None:
        """Handle POST requests."""
        if self.path != "/analyze":
            self._respond(404, {"error": "not found"})
            return

        if not _is_authorized(self.headers):
            extra_headers = {}
            if JWT_ENABLED:
                extra_headers["WWW-Authenticate"] = 'Bearer realm="module2-repo-analysis", error="invalid_token"'
            self._respond(401, {"error": "unauthorized"}, extra_headers=extra_headers)
            return

        client_ip = self.client_address[0] if self.client_address else "unknown"
        if _is_rate_limited(client_ip):
            self._respond(429, {"error": "rate limit exceeded"})
            return

        content_type = self.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            self._respond(415, {"error": "Content-Type must be application/json"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._respond(400, {"error": "invalid Content-Length"})
            return

        if length <= 0:
            self._respond(400, {"error": "request body is required"})
            return

        if length > MAX_BODY_BYTES:
            self._respond(413, {"error": "request body too large"})
            return

        try:
            body = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            self._respond(400, {"error": "invalid JSON"})
            return

        if not isinstance(body, dict):
            self._respond(400, {"error": "JSON body must be an object"})
            return

        try:
            self._respond(200, _handle_analysis(body))
        except Exception:
            # Avoid leaking internals over the API.
            self._respond(500, {"error": "internal server error"})

    def _respond(self, code: int, data: dict, *, extra_headers: dict[str, str] | None = None) -> None:
        """Send JSON response."""
        payload = json.dumps(data, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for header_name, header_value in SECURITY_RESPONSE_HEADERS.items():
            self.send_header(header_name, header_value)
        if extra_headers:
            for header_name, header_value in extra_headers.items():
                self.send_header(header_name, header_value)
        self.end_headers()
        self.wfile.write(payload)


def run_server(host: str = "127.0.0.1", port: int = 8081) -> None:
    """
    Run the HTTP server.

    Parameters
    ----------
    host : str
        Host to bind to. Default: 127.0.0.1 (localhost only).
        Set to 0.0.0.0 explicitly to bind to all interfaces.
    port : int
        Port to listen on. Default: 8081 (different from Module 1's 8080)
    """
    mock = os.getenv("AGENT_MOCK_REPO", "false").lower() == "true"
    key_required = bool(os.getenv("AGENT_API_KEY", "").strip())
    jwt_required = JWT_ENABLED
    
    print(f"  🚀  Module 2 Repository Analysis Agent HTTP Server")
    print(f"      http://{host}:{port}/analyze  (POST)")
    print(f"      http://{host}:{port}/ping     (GET)")
    print(f"      Mock mode : {'ON — using fixture data' if mock else 'OFF — analyzing real repos'}")
    if key_required and jwt_required:
        auth_mode = "ON — API key or JWT bearer accepted"
    elif key_required:
        auth_mode = "ON — API key required"
    elif jwt_required:
        auth_mode = "ON — JWT bearer required"
    else:
        auth_mode = "OFF — local-dev compatibility mode"
    print(f"      Auth      : {auth_mode}")

    if jwt_required:
        jwt_status = "configured" if (_JWKS_CLIENT is not None and jwt is not None) else "misconfigured (missing pyjwt[crypto])"
        print(f"      JWT       : {jwt_status}")
        print(f"      Issuer    : {JWT_ISSUER}")
        print(f"      Audience  : {JWT_AUDIENCE}")
        print(f"      Algorithms: {', '.join(JWT_ALGORITHMS) if JWT_ALGORITHMS else 'none'}")
        print(f"      Scope req : {', '.join(sorted(JWT_REQUIRED_SCOPES)) if JWT_REQUIRED_SCOPES else 'none'}")
        print(f"      Role req  : {', '.join(sorted(JWT_REQUIRED_ROLES)) if JWT_REQUIRED_ROLES else 'none'}")

    print(f"      Max body  : {MAX_BODY_BYTES} bytes")
    print(f"      Rate limit: {RATE_LIMIT_REQUESTS} requests / {RATE_LIMIT_WINDOW_SECONDS}s per IP")
    print(f"      Allowed roots:")
    for root in ALLOWED_REPO_ROOTS:
        print(f"        - {root}")
    print(f"\n  Example:")
    print(f"    curl -X POST http://localhost:{port}/analyze \\")
    if key_required:
        print(f"      -H 'X-API-Key: <your-key>' \\")
    if jwt_required and not key_required:
        print(f"      -H 'Authorization: Bearer <jwt-token>' \\")
    print(f"      -H 'Content-Type: application/json' \\")
    print(f"      -d '{{\"repo_path\": \"/path/to/repo\"}}'")
    print(f"\n  Ctrl-C to stop.\n")

    server = HTTPServer((host, port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    run_server()
