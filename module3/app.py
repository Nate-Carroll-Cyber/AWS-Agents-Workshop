"""
module3/app.py
==============
HTTP server for Module 3 CDK Infrastructure Generation Agent.

Provides REST API endpoints for CDK generation and validation.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Lock
from typing import Any
from uuid import UUID, uuid4

try:
    import jwt
    from jwt import PyJWKClient
    from jwt.exceptions import InvalidTokenError
except Exception:  # pragma: no cover - optional dependency
    jwt = None
    PyJWKClient = None
    InvalidTokenError = Exception

from module3.agent import create_agent, generate_infrastructure, validate_cdk_code


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PORT = int(os.getenv("MODULE3_PORT", "8082"))
HOST = os.getenv("MODULE3_HOST", "127.0.0.1")
VERBOSE = os.getenv("MODULE3_VERBOSE", "false").lower() == "true"
MAX_BODY_BYTES = int(os.getenv("MODULE3_MAX_BODY_BYTES", "32768"))
RATE_LIMIT_REQUESTS = int(os.getenv("MODULE3_RATE_LIMIT_REQUESTS", "20"))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv("MODULE3_RATE_LIMIT_WINDOW_SECONDS", "60"))

JWT_ISSUER = os.getenv("MODULE3_JWT_ISSUER", "").strip()
JWT_AUDIENCE = os.getenv("MODULE3_JWT_AUDIENCE", "").strip()
JWT_JWKS_URL = os.getenv("MODULE3_JWT_JWKS_URL", "").strip()
JWT_LEEWAY_SECONDS = int(os.getenv("MODULE3_JWT_LEEWAY_SECONDS", "30"))
JWT_ALGORITHMS = tuple(
    algo.strip()
    for algo in os.getenv("MODULE3_JWT_ALGORITHMS", "RS256").split(",")
    if algo.strip()
)

JWT_REQUIRED_SCOPES = {
    scope.strip()
    for scope in os.getenv("MODULE3_JWT_REQUIRED_SCOPES", "").split(",")
    if scope.strip()
}
JWT_REQUIRED_ROLES = {
    role.strip()
    for role in os.getenv("MODULE3_JWT_REQUIRED_ROLES", "").split(",")
    if role.strip()
}
JWT_SCOPE_CLAIMS = tuple(
    claim.strip()
    for claim in os.getenv("MODULE3_JWT_SCOPE_CLAIMS", "scope,scp").split(",")
    if claim.strip()
)
JWT_ROLE_CLAIMS = tuple(
    claim.strip()
    for claim in os.getenv("MODULE3_JWT_ROLE_CLAIMS", "roles,cognito:groups,groups").split(",")
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


def _error_payload(
    *,
    error_code: str,
    message: str,
    retry_with: str,
    escalate: bool,
    correlation_id: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a consistent error response payload for API callers."""
    payload: dict[str, Any] = {
        "error_code": error_code,
        "error": message,
        "retry_with": retry_with,
        "escalate": escalate,
        "correlation_id": correlation_id,
    }
    if extra:
        payload.update(extra)
    return payload


def _request_correlation_id(headers: Any) -> str:
    """Resolve request correlation ID from header or generate one."""
    incoming = headers.get("X-Correlation-ID", "").strip()
    if incoming and len(incoming) <= 128:
        try:
            return str(UUID(incoming))
        except ValueError:
            pass
    return str(uuid4())


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
    configured_key = os.getenv("MODULE3_API_KEY", "").strip()
    if not configured_key and not JWT_ENABLED:
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
# HTTP Request Handler
# ---------------------------------------------------------------------------

class Module3Handler(BaseHTTPRequestHandler):
    """HTTP request handler for Module 3 CDK generation endpoints."""

    def _send_json_response(
        self,
        data: dict[str, Any],
        status: int = 200,
        *,
        correlation_id: str,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        """Send JSON response."""
        payload = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Correlation-ID", correlation_id)
        for header_name, header_value in SECURITY_RESPONSE_HEADERS.items():
            self.send_header(header_name, header_value)
        cors_origin = os.getenv("MODULE3_CORS_ALLOW_ORIGIN", "").strip()
        if cors_origin:
            self.send_header("Access-Control-Allow-Origin", cors_origin)
        if extra_headers:
            for header_name, header_value in extra_headers.items():
                self.send_header(header_name, header_value)
        self.end_headers()
        self.wfile.write(payload)

    def _send_error_response(self, payload: dict[str, Any], status: int, *, correlation_id: str) -> None:
        """Send error response."""
        self._send_json_response(payload, status, correlation_id=correlation_id)

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight requests."""
        correlation_id = _request_correlation_id(self.headers)
        self.send_response(200)
        self.send_header("X-Correlation-ID", correlation_id)
        cors_origin = os.getenv("MODULE3_CORS_ALLOW_ORIGIN", "").strip()
        if cors_origin:
            self.send_header("Access-Control-Allow-Origin", cors_origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-API-Key, X-Correlation-ID")
        for header_name, header_value in SECURITY_RESPONSE_HEADERS.items():
            self.send_header(header_name, header_value)
        self.end_headers()

    def do_GET(self) -> None:
        """Handle GET requests."""
        correlation_id = _request_correlation_id(self.headers)
        if self.path == "/ping":
            self._send_json_response({
                "status": "healthy",
                "service": "module3-cdk-agent",
                "version": "0.1.0",
            }, correlation_id=correlation_id)
        else:
            self._send_error_response(
                _error_payload(
                    error_code="NOT_FOUND",
                    message="not found",
                    retry_with="Use /ping (GET) or /analyze, /generate, /validate (POST).",
                    escalate=False,
                    correlation_id=correlation_id,
                ),
                404,
                correlation_id=correlation_id,
            )

    def do_POST(self) -> None:
        """Handle POST requests."""
        correlation_id = _request_correlation_id(self.headers)
        try:
            if self.path not in {"/generate", "/validate", "/analyze"}:
                self._send_error_response(
                    _error_payload(
                        error_code="NOT_FOUND",
                        message="not found",
                        retry_with="Use POST /analyze, /generate, or /validate.",
                        escalate=False,
                        correlation_id=correlation_id,
                    ),
                    404,
                    correlation_id=correlation_id,
                )
                return

            if not _is_authorized(self.headers):
                extra_headers = {}
                if JWT_ENABLED:
                    extra_headers["WWW-Authenticate"] = 'Bearer realm="module3-cdk-agent", error="invalid_token"'
                self._send_json_response(
                    _error_payload(
                        error_code="UNAUTHORIZED",
                        message="unauthorized",
                        retry_with="Provide a valid X-API-Key or Authorization Bearer token.",
                        escalate=False,
                        correlation_id=correlation_id,
                    ),
                    401,
                    correlation_id=correlation_id,
                    extra_headers=extra_headers,
                )
                return

            client_ip = self.client_address[0] if self.client_address else "unknown"
            if _is_rate_limited(client_ip):
                self._send_json_response(
                    _error_payload(
                        error_code="RATE_LIMIT_EXCEEDED",
                        message="rate limit exceeded",
                        retry_with="Retry after the rate-limit window or reduce request frequency.",
                        escalate=False,
                        correlation_id=correlation_id,
                    ),
                    429,
                    correlation_id=correlation_id,
                )
                return

            content_type = self.headers.get("Content-Type", "")
            if "application/json" not in content_type:
                self._send_error_response(
                    _error_payload(
                        error_code="UNSUPPORTED_MEDIA_TYPE",
                        message="Content-Type must be application/json",
                        retry_with="Set Content-Type: application/json and resend the request.",
                        escalate=False,
                        correlation_id=correlation_id,
                    ),
                    415,
                    correlation_id=correlation_id,
                )
                return

            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send_error_response(
                    _error_payload(
                        error_code="INVALID_CONTENT_LENGTH",
                        message="invalid Content-Length",
                        retry_with="Send a numeric Content-Length header.",
                        escalate=False,
                        correlation_id=correlation_id,
                    ),
                    400,
                    correlation_id=correlation_id,
                )
                return

            if content_length <= 0:
                self._send_error_response(
                    _error_payload(
                        error_code="EMPTY_REQUEST_BODY",
                        message="request body is required",
                        retry_with="Send a non-empty JSON request body.",
                        escalate=False,
                        correlation_id=correlation_id,
                    ),
                    400,
                    correlation_id=correlation_id,
                )
                return

            if content_length > MAX_BODY_BYTES:
                self._send_error_response(
                    _error_payload(
                        error_code="REQUEST_BODY_TOO_LARGE",
                        message="request body too large",
                        retry_with="Reduce payload size or increase MODULE3_MAX_BODY_BYTES if policy permits.",
                        escalate=False,
                        correlation_id=correlation_id,
                    ),
                    413,
                    correlation_id=correlation_id,
                )
                return

            body = self.rfile.read(content_length)

            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                self._send_error_response(
                    _error_payload(
                        error_code="INVALID_JSON",
                        message="invalid JSON",
                        retry_with="Submit valid JSON with double-quoted keys and values where required.",
                        escalate=False,
                        correlation_id=correlation_id,
                    ),
                    400,
                    correlation_id=correlation_id,
                )
                return

            if not isinstance(data, dict):
                self._send_error_response(
                    _error_payload(
                        error_code="INVALID_REQUEST_SHAPE",
                        message="JSON body must be an object",
                        retry_with="Send a top-level JSON object for request payload.",
                        escalate=False,
                        correlation_id=correlation_id,
                    ),
                    400,
                    correlation_id=correlation_id,
                )
                return

            data["_correlation_id"] = correlation_id

            if self.path == "/generate":
                self._handle_generate(data)
            elif self.path == "/validate":
                self._handle_validate(data)
            elif self.path == "/analyze":
                self._handle_analyze(data)

        except Exception as e:
            if VERBOSE:
                print(f"[Module3] Internal error: {e}")
            self._send_error_response(
                _error_payload(
                    error_code="INTERNAL_SERVER_ERROR",
                    message="internal server error",
                    retry_with="Retry once; if the issue persists, escalate with correlation_id for support.",
                    escalate=True,
                    correlation_id=correlation_id,
                ),
                500,
                correlation_id=correlation_id,
            )

    def _handle_generate(self, data: dict[str, Any]) -> None:
        """
        Handle /generate endpoint.
        
        Request body:
        {
            "requirements": {...} or "text requirements",
            "region": "us-east-1",
            "environment": "dev"
        }
        """
        requirements = data.get("requirements")
        correlation_id = data.get("_correlation_id") or str(uuid4())
        if not requirements:
            self._send_error_response(
                _error_payload(
                    error_code="MISSING_REQUIREMENTS",
                    message="Missing 'requirements' in request body",
                    retry_with="Provide requirements as an object or string in the requirements field.",
                    escalate=False,
                    correlation_id=correlation_id,
                ),
                400,
                correlation_id=correlation_id,
            )
            return

        region = data.get("region", "us-east-1")
        environment = data.get("environment", "dev")

        try:
            result = generate_infrastructure(
                requirements=requirements,
                region=region,
                environment=environment,
                verbose=VERBOSE,
            )
            
            self._send_json_response({
                "status": "success",
                "region": region,
                "environment": environment,
                "output": result["output"],
            }, correlation_id=correlation_id)
        except Exception as e:
            if VERBOSE:
                print(f"[Module3] Generation error: {e}")
            self._send_error_response(
                _error_payload(
                    error_code="GENERATION_FAILED",
                    message="generation failed",
                    retry_with="Validate requirements, then retry. If repeated, escalate with correlation_id.",
                    escalate=True,
                    correlation_id=correlation_id,
                ),
                500,
                correlation_id=correlation_id,
            )

    def _handle_validate(self, data: dict[str, Any]) -> None:
        """
        Handle /validate endpoint.
        
        Request body:
        {
            "cdk_code": "from aws_cdk import ..."
        }
        """
        cdk_code = data.get("cdk_code")
        correlation_id = data.get("_correlation_id") or str(uuid4())
        if not cdk_code:
            self._send_error_response(
                _error_payload(
                    error_code="MISSING_CDK_CODE",
                    message="Missing 'cdk_code' in request body",
                    retry_with="Provide CDK code as a string in the cdk_code field.",
                    escalate=False,
                    correlation_id=correlation_id,
                ),
                400,
                correlation_id=correlation_id,
            )
            return

        try:
            result = validate_cdk_code(cdk_code=cdk_code, verbose=VERBOSE)
            
            self._send_json_response({
                "status": "success",
                "validation_output": result["validation_output"],
            }, correlation_id=correlation_id)
        except Exception as e:
            if VERBOSE:
                print(f"[Module3] Validation error: {e}")
            self._send_error_response(
                _error_payload(
                    error_code="VALIDATION_FAILED",
                    message="validation failed",
                    retry_with="Provide syntactically valid CDK Python code and retry.",
                    escalate=True,
                    correlation_id=correlation_id,
                ),
                500,
                correlation_id=correlation_id,
            )

    def _handle_analyze(self, data: dict[str, Any]) -> None:
        """
        Handle /analyze endpoint - analyze requirements without generating code.
        
        Request body:
        {
            "requirements": {...} or "text requirements"
        }
        """
        requirements = data.get("requirements")
        correlation_id = data.get("_correlation_id") or str(uuid4())
        if not requirements:
            self._send_error_response(
                _error_payload(
                    error_code="MISSING_REQUIREMENTS",
                    message="Missing 'requirements' in request body",
                    retry_with="Provide requirements as an object or string in the requirements field.",
                    escalate=False,
                    correlation_id=correlation_id,
                ),
                400,
                correlation_id=correlation_id,
            )
            return

        try:
            agent = create_agent(verbose=VERBOSE)
            
            # Format requirements
            if isinstance(requirements, dict):
                req_str = json.dumps(requirements, indent=2)
            else:
                req_str = str(requirements)
            
            query = f"""Analyze these infrastructure requirements and provide:
1. Parsed requirements (structured format)
2. Recommended CDK stacks
3. Any clarifying questions needed

Requirements:
{req_str}

Do NOT generate code yet, just analyze and provide recommendations.
"""
            
            result = agent.invoke({"messages": [("user", query)]})
            messages = result.get("messages", [])
            final_output = messages[-1].content if messages else ""
            
            self._send_json_response({
                "status": "success",
                "analysis": final_output,
            }, correlation_id=correlation_id)
        except Exception as e:
            if VERBOSE:
                print(f"[Module3] Analysis error: {e}")
            self._send_error_response(
                _error_payload(
                    error_code="ANALYSIS_FAILED",
                    message="analysis failed",
                    retry_with="Validate requirements payload and retry.",
                    escalate=True,
                    correlation_id=correlation_id,
                ),
                500,
                correlation_id=correlation_id,
            )

    def log_message(self, format: str, *args: Any) -> None:
        """Override to customize logging."""
        if VERBOSE:
            print(f"[Module3] {format % args}")


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

def run_server(port: int = PORT, host: str = HOST) -> None:
    """
    Run the Module 3 HTTP server.

    Parameters
    ----------
    port : int
        Port to listen on. Default 8082.
    host : str
        Host to bind to. Default "127.0.0.1" (localhost only).
        Set MODULE3_HOST=0.0.0.0 to bind to all interfaces.
    """
    server = HTTPServer((host, port), Module3Handler)
    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║  Module 3: CDK Infrastructure Generation Agent                  ║
╚══════════════════════════════════════════════════════════════════╝

  Server running on http://{host}:{port}

  Endpoints:
    GET  /ping              - Health check
    POST /analyze           - Analyze infrastructure requirements
    POST /generate          - Generate CDK infrastructure code
    POST /validate          - Validate CDK code

  Example:
    curl -X POST http://localhost:{port}/generate \\
      -H "Content-Type: application/json" \\
      -d '{{"requirements": {{"compute": "ECS", "database": "RDS"}}, "region": "us-east-1"}}'

  Press Ctrl+C to stop
""")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\nShutting down server...")
        server.shutdown()


if __name__ == "__main__":
    run_server()
