# Module 2: Repository Analysis Agent

LangChain-based agent for analyzing git repositories to identify applications, technology stacks, and AWS infrastructure requirements.

## Overview

This module demonstrates the **Module 2 framework approach** using LangChain and LangGraph, complementing the Module 1 AWS Infrastructure Agent (Strands-based). Together, they form the foundation for multi-agent DevOps automation.

## What This Agent Does

1. **Scans** local git repositories to understand structure
2. **Detects** distinct applications/services (monorepo support)
3. **Analyzes** technology stacks (languages, frameworks, dependencies)
4. **Maps** dependencies to AWS infrastructure requirements
5. **Generates** structured analysis reports for deployment planning

## Quick Start

```bash
# Mock mode (no real repository needed)
AGENT_MOCK_REPO=true python demos/module2_demo.py

# Analyze a real repository
python demos/module2_demo.py --repo /path/to/your/repo

# Run specific demo section
AGENT_MOCK_REPO=true python demos/module2_demo.py --section 4

# Run tests
AGENT_MOCK_REPO=true pytest tests/test_repo_tools.py -v
```

## Architecture

### Framework: LangChain + LangGraph

**Model Interface**: `ChatBedrock` (LangChain wrapper for Amazon Bedrock)
**Execution Loop**: `AgentExecutor` or LangGraph state machine
**Tools**: 5 repository analysis tools
**Observability**: LangSmith tracing integration

### Five Repository Analysis Tools

1. **`scan_repository_structure`** - List files/directories with git awareness
2. **`read_file_content`** - Read specific files (package.json, requirements.txt, etc.)
3. **`detect_applications`** - Identify distinct apps/services in repository
4. **`analyze_dependencies`** - Parse dependency files and extract libraries
5. **`map_aws_services`** - Map dependencies to AWS services (RDS, ElastiCache, etc.)

## Framework Comparison: Module 1 vs Module 2

| Aspect | Module 1 (Strands) | Module 2 (LangChain) |
|--------|-------------------|---------------------|
| **Framework** | AWS Strands | LangChain + LangGraph |
| **Model Interface** | `BedrockModel` | `ChatBedrock` |
| **Agent Pattern** | `Agent` class | `AgentExecutor` or LangGraph |
| **Memory** | `SlidingWindowConversationManager` | `ConversationBufferMemory` |
| **Observability** | Callback handlers | LangSmith tracing |
| **Use Case** | AWS infrastructure management | Repository analysis |
| **Tools** | AWS API calls (ECS, EC2, RDS) | Local git operations |

Both use the same Amazon Bedrock model and implement the think-act-observe loop.

## Example Output

```json
{
  "repository": "/path/to/repo",
  "applications": [
    {
      "name": "api-service",
      "path": "services/api",
      "stack": {
        "language": "Node.js",
        "runtime": "18.x",
        "framework": "Express",
        "dependencies": ["pg", "redis", "aws-sdk"]
      },
      "aws_requirements": {
        "compute": "ECS Fargate or Lambda",
        "database": "RDS PostgreSQL",
        "cache": "ElastiCache Redis",
        "storage": "S3",
        "networking": "VPC, ALB"
      }
    }
  ],
  "summary": {
    "total_applications": 1,
    "languages": ["Node.js"],
    "aws_services_needed": ["ECS", "RDS", "ElastiCache", "S3", "VPC", "ALB"]
  }
}
```

## Usage

### Python API

```python
from module2.agent import create_agent, analyze_repository

# Simple approach
agent = create_agent(verbose=True)
result = agent.invoke({"input": "Analyze repository at /path/to/repo"})
print(result["output"])

# Convenience function
results = analyze_repository("/path/to/repo")
print(results["analysis"])
```

### HTTP Server

```bash
# Start server
python module2/app.py

# Analyze repository
curl -X POST http://localhost:8081/analyze \
  -H "Content-Type: application/json" \
  -d '{"repo_path": "/path/to/repo"}'
```

### HTTP Security Hardening

The Module 2 HTTP server now supports basic hardening controls.

Environment variables:

- `AGENT_API_KEY`: if set, requests must include `X-API-Key` or `Authorization: Bearer <key>`
- `AGENT_ALLOWED_REPO_ROOTS`: path-separated allowlist for repositories (defaults to project root)
- `AGENT_MAX_BODY_BYTES`: max JSON request size in bytes (default `32768`)
- `AGENT_RATE_LIMIT_REQUESTS`: max requests per IP in window (default `20`)
- `AGENT_RATE_LIMIT_WINDOW_SECONDS`: rate-limit window length in seconds (default `60`)
- `AGENT_ALLOW_VERBOSE`: allow per-request verbose mode when `true` (default `false`)

Optional JWT/OIDC bearer validation:

- `AGENT_JWT_ISSUER`: expected token issuer (for `iss` claim)
- `AGENT_JWT_AUDIENCE`: expected token audience (for `aud` claim)
- `AGENT_JWT_JWKS_URL`: JWKS endpoint URL for signature verification
- `AGENT_JWT_ALGORITHMS`: comma-separated accepted algorithms (default `RS256`)
- `AGENT_JWT_LEEWAY_SECONDS`: clock-skew tolerance in seconds (default `30`)
- `AGENT_JWT_REQUIRED_SCOPES`: comma-separated scopes that must all be present
- `AGENT_JWT_REQUIRED_ROLES`: comma-separated roles/groups that must all be present
- `AGENT_JWT_SCOPE_CLAIMS`: comma-separated claim names to read scopes from (default `scope,scp`)
- `AGENT_JWT_ROLE_CLAIMS`: comma-separated claim names to read roles from (default `roles,cognito:groups,groups`)

Install JWT dependencies before enabling JWT auth:

```bash
pip install "pyjwt[crypto]"
```

Example (recommended for non-demo use):

```bash
export AGENT_API_KEY="change-me"
export AGENT_ALLOWED_REPO_ROOTS="/Users/you/repos:/opt/work/repos"

python module2/app.py

curl -X POST http://localhost:8081/analyze \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"repo_path": "/Users/you/repos/my-app"}'
```

JWT-only example:

```bash
export AGENT_JWT_ISSUER="https://your-idp.example.com/"
export AGENT_JWT_AUDIENCE="repo-analysis-api"
export AGENT_JWT_JWKS_URL="https://your-idp.example.com/.well-known/jwks.json"
export AGENT_JWT_REQUIRED_SCOPES="repo:analyze"
export AGENT_JWT_REQUIRED_ROLES="devops"

python module2/app.py

curl -X POST http://localhost:8081/analyze \
  -H "Authorization: Bearer <jwt-token>" \
  -H "Content-Type: application/json" \
  -d '{"repo_path": "/Users/you/repos/my-app"}'
```

RBAC behavior notes:

- Scope and role checks are optional; they are enforced only when the corresponding required lists are set.
- All required scopes must be present.
- All required roles must be present.
- Scope claims support either space-delimited strings or arrays.
- Role claims support either arrays or whitespace-delimited strings.

### Agent and Model Guardrails

Security controls now also apply when using Python APIs directly (not only HTTP):

- `analyze_repository(...)` validates `repo_path` is absolute, exists, is a git repo, and is under `AGENT_ALLOWED_REPO_ROOTS`
- `create_agent(...)` enforces `max_iterations >= 1` and applies a recursion limit to bound tool loops
- `get_chat_bedrock_model(...)` validates region format, temperature range, and max token range
- model IDs are restricted by allowlist

Model policy environment variable:

- `AGENT_ALLOWED_MODEL_IDS`: comma-separated Bedrock model IDs allowed by policy

If `AGENT_ALLOWED_MODEL_IDS` is not set, defaults are:

- `us.anthropic.claude-sonnet-4-20250514-v1:0`
- `anthropic.claude-sonnet-4-20250514-v1:0`

### Tool Telemetry Envelope

Every Module 2 tool call now emits a standardized envelope with:

- `tool` (tool name)
- `input` (input parameters, with long string values truncated for log safety)
- `status` (`success` or `error`)
- `latency_ms` (execution time in milliseconds)
- `correlation_id` (request/trace identifier)

Correlation ID behavior:

- Incoming `X-Correlation-ID` is accepted when provided.
- If missing, a UUID is generated.
- The server returns `X-Correlation-ID` on responses.
- The same value is propagated into downstream tool telemetry for request-level traceability.

## LangSmith Tracing

Enable LangSmith for detailed observability:

```bash
export LANGCHAIN_TRACING_V2=true
export LANGCHAIN_API_KEY=<your-key>
export LANGCHAIN_PROJECT=repo-analysis-agent

python demos/module2_demo.py
```

View traces at: https://smith.langchain.com/

## Dependency Mapping

The agent maps common libraries to AWS services:

| Dependency | AWS Service | Engine |
|------------|-------------|--------|
| `pg`, `psycopg2` | RDS | PostgreSQL |
| `mysql`, `mysql2` | RDS | MySQL |
| `mongodb`, `pymongo` | DocumentDB | MongoDB |
| `redis`, `ioredis` | ElastiCache | Redis |
| `boto3`, `aws-sdk` | S3 | Object Storage |
| `celery` | SQS | Message Queue |
| `express`, `fastapi` | ECS/Lambda | Web Framework |

See `module2/tools/repo_tools.py` for the complete mapping.

## Multi-Agent Integration (Future)

Module 2 is designed to work with Module 1 in a multi-agent workflow:

1. **Module 2 Agent** analyzes repository → identifies infrastructure needs
2. **Module 1 Agent** checks existing AWS resources → identifies gaps
3. **Orchestrator** coordinates both agents → generates deployment plan

This multi-agent pattern will be covered in Module 4.

## Project Structure

```
module2/
├── agent.py              # Agent factory and main logic
├── app.py                # HTTP server entrypoint
├── config/
│   └── models.py         # ChatBedrock configuration
├── tools/
│   └── repo_tools.py     # 5 repository analysis tools
├── workflows/
│   └── analysis_graph.py # LangGraph state machine
└── prompts/
    └── system_prompts.py # System prompts for each stage
```

## Testing

```bash
# Run all tests
AGENT_MOCK_REPO=true pytest tests/test_repo_tools.py -v

# Run specific test
AGENT_MOCK_REPO=true pytest tests/test_repo_tools.py::test_scan_repository_structure_mock -v

# Test with real repository (requires git repo)
pytest tests/test_repo_tools.py::test_full_analysis_workflow -v
```

## Demo Sections

Run `AGENT_MOCK_REPO=true python demos/module2_demo.py --section N`:

| # | Title | Key Concept |
|---|-------|-------------|
| 1 | Framework comparison | LangChain vs Strands architecture |
| 2 | Repository scan | File structure analysis |
| 3 | Application detection | Multi-app/monorepo identification |
| 4 | Dependency analysis | Stack and AWS service mapping |
| 5 | LangSmith tracing | Observability and debugging |
| 6 | Full workflow | Complete analysis pipeline |

## Next Steps

- **Module 3**: Evaluation and routing patterns
- **Module 4**: Multi-agent orchestration (Module 1 + Module 2 working together)
- **Module 7**: Long-term memory with DynamoDB and vector stores

## License

Part of the AI Agent Learning Series on AWS.
