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
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from module2.agent import create_agent


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

    # Use verbose agent for local testing if requested
    if payload.get("verbose"):
        local_agent = create_agent(verbose=True)
        result = local_agent.invoke({
            "input": f"Analyze the git repository at: {repo_path}"
        })
    else:
        result = _agent.invoke({
            "input": f"Analyze the git repository at: {repo_path}"
        })

    return {
        "repo_path": repo_path,
        "analysis": result.get("output", ""),
        "mock_mode": os.getenv("AGENT_MOCK_REPO", "false").lower() == "true",
        "framework": "langchain",
    }


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

        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            self._respond(400, {"error": "invalid JSON"})
            return

        self._respond(200, _handle_analysis(body))

    def _respond(self, code: int, data: dict) -> None:
        """Send JSON response."""
        payload = json.dumps(data, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
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
    
    print(f"  🚀  Module 2 Repository Analysis Agent HTTP Server")
    print(f"      http://{host}:{port}/analyze  (POST)")
    print(f"      http://{host}:{port}/ping     (GET)")
    print(f"      Mock mode : {'ON — using fixture data' if mock else 'OFF — analyzing real repos'}")
    print(f"\n  Example:")
    print(f"    curl -X POST http://localhost:{port}/analyze \\")
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
