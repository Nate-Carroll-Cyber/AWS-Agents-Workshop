"""
app.py
======
Amazon Bedrock AgentCore Runtime entrypoint.

This file wraps the Strands agent in a BedrockAgentCoreApp, converting it
into a production-ready HTTP service with:
  - POST /invocations  : invoke the agent
  - GET  /ping         : health check
  - Session isolation  : each user session runs in a dedicated microVM
  - Auto-scaling       : handles thousands of concurrent sessions
  - CloudWatch logging : built-in observability

MODULE 1 CONCEPT: The Deployment-Logic Separation
---------------------------------------------------
Notice how little code is here:

    from bedrock_agentcore import BedrockAgentCoreApp
    app = BedrockAgentCoreApp()

    @app.entrypoint
    def invoke(payload):
        return agent(payload["prompt"])

    app.run()

The agent logic (agent.py) is completely unchanged when deploying to
AgentCore. This is the same abstraction arc the Module 1 slides describe:
your agent IS the application; AgentCore is the managed runtime platform.

DEPLOYMENT STEPS
-----------------
  # 1. Install the starter toolkit CLI
  pip install bedrock-agentcore-starter-toolkit

  # 2. Configure deployment (run once)
  agentcore configure --entrypoint app.py --disable-memory

  # 3. Test locally (no Docker needed — serves on http://localhost:8080)
  python app.py

  # 4. Test the local server
  curl -X POST http://localhost:8080/invocations \\
    -H "Content-Type: application/json" \\
    -d '{"prompt": "Give me a health summary of us-east-1"}'

  # 5. Deploy to AWS AgentCore Runtime (CodeBuild, no Docker needed)
  agentcore launch

  # 6. Invoke the deployed agent
  agentcore invoke '{"prompt": "List ECS services in us-east-1"}'

  # 7. Invoke via boto3 (for integration with other services)
  #    See: scripts/invoke_agentcore.py

MOCK MODE
----------
Set AGENT_MOCK_AWS=true to run without real AWS credentials.
The agent responds with realistic simulated data — good for demos.

  AGENT_MOCK_AWS=true python app.py
"""

from __future__ import annotations

import json
import os
import sys

# Ensure project root is on the path when running directly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from module1.agent import create_agent

# ---------------------------------------------------------------------------
# AgentCore import with graceful fallback
# ---------------------------------------------------------------------------
# The fallback ensures the file runs (with a plain HTTP server) even if
# bedrock-agentcore hasn't been installed yet — useful for initial setup.

try:
    from bedrock_agentcore import BedrockAgentCoreApp
    _AGENTCORE = True
except ImportError:
    _AGENTCORE = False
    print("⚠  bedrock-agentcore not installed — running plain HTTP fallback.")
    print("   Install: pip install bedrock-agentcore bedrock-agentcore-starter-toolkit")


# ---------------------------------------------------------------------------
# Shared agent instance (created once at startup, reused across requests)
# ---------------------------------------------------------------------------

_hf_arn = os.getenv("HF_ENDPOINT_ARN")

print("\n  Initialising AWS Infrastructure Agent...")
_agent = create_agent(
    hf_endpoint_arn=_hf_arn if _hf_arn else None,
    verbose=False,   # suppress loop steps in server mode (use CloudWatch instead)
)
print("  Agent ready.\n")


# ---------------------------------------------------------------------------
# Request handler (shared between AgentCore and fallback)
# ---------------------------------------------------------------------------

def _handle(payload: dict) -> dict:
    """
    Process an incoming agent request.

    Expected payload
    ----------------
    {
      "prompt"  : str   — required: the user's instruction
      "region"  : str   — optional: override AWS region for this request
      "verbose" : bool  — optional: print loop steps to console (local dev)
    }
    """
    prompt = payload.get("prompt", "").strip()
    if not prompt:
        return {"error": "Missing required field: 'prompt'"}

    region = payload.get("region")
    if region:
        os.environ["AWS_REGION"] = region

    # Use a verbose agent for local testing if requested
    if payload.get("verbose"):
        local_agent = create_agent(
            hf_endpoint_arn=_hf_arn if _hf_arn else None,
            verbose=True,
        )
        response = local_agent(prompt)
    else:
        response = _agent(prompt)

    return {
        "result": str(response),
        "region": os.getenv("AWS_REGION", "us-east-1"),
        "mock_mode": os.getenv("AGENT_MOCK_AWS", "false").lower() == "true",
        "model": "huggingface-bedrock-marketplace" if _hf_arn else "claude-sonnet-4-bedrock",
    }


# ---------------------------------------------------------------------------
# AgentCore entrypoint
# ---------------------------------------------------------------------------

if _AGENTCORE:
    app = BedrockAgentCoreApp()

    @app.entrypoint
    def invoke(payload: dict) -> dict:
        """
        AgentCore entrypoint — called for every POST /invocations request.

        AgentCore deserialises the JSON body, calls this function, and
        serialises the return value back to JSON. The only AgentCore-specific
        code in this project is these ~4 lines.
        """
        return _handle(payload)


# ---------------------------------------------------------------------------
# Fallback: minimal HTTP server (no AgentCore installed)
# ---------------------------------------------------------------------------

def _run_fallback(host: str = "127.0.0.1", port: int = 8080) -> None:
    """Plain HTTP server that mirrors the AgentCore /invocations interface."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: object) -> None:
            pass  # suppress default access log

        def do_GET(self) -> None:
            if self.path == "/ping":
                self._respond(200, {"status": "ok"})
            else:
                self._respond(404, {"error": "not found"})

        def do_POST(self) -> None:
            if self.path != "/invocations":
                self._respond(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length))
            except json.JSONDecodeError:
                self._respond(400, {"error": "invalid JSON"})
                return
            self._respond(200, _handle(body))

        def _respond(self, code: int, data: dict) -> None:
            payload = json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    mock = os.getenv("AGENT_MOCK_AWS", "false").lower() == "true"
    print(f"  🚀  Infrastructure Agent HTTP server")
    print(f"      http://{host}:{port}/invocations  (POST)")
    print(f"      http://{host}:{port}/ping         (GET)")
    print(f"      Mock mode : {'ON — no AWS credentials needed' if mock else 'OFF — using live AWS'}")
    print(f"\n  Example:")
    print(f"    curl -X POST http://localhost:{port}/invocations \\")
    print(f"      -H 'Content-Type: application/json' \\")
    print(f"      -d '{{\"prompt\": \"Give me a health summary of us-east-1\"}}'")
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
    if _AGENTCORE:
        print("  🚀  Starting via Bedrock AgentCore Runtime...")
        app.run()
    else:
        _run_fallback()
