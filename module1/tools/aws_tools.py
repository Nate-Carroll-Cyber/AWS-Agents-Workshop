"""
tools/aws_tools.py
==================
Module 1 tool set for the AWS Infrastructure Agent.

DESIGN PRINCIPLES FOR MODULE 1
--------------------------------
All tools in this module are READ-ONLY. No infrastructure is created,
modified, or destroyed. This is intentional and reflects the Phase 1
(Assist) adoption pattern from the slides:

    Phase 1 — Assist  : Agent observes and recommends. Human acts.  ← we are here
    Phase 2 — Automate: Agent acts within guardrails.
    Phase 3 — Orchestrate: Full autonomous workflows.

The only "action" available is request_human_review, which creates a
structured escalation record for a human engineer to act on. This is the
Human-in-the-Loop pattern shown in the Architecture section of Module 1.

MOCK MODE
---------
Set AGENT_MOCK_AWS=true to run without real AWS credentials.
All tools return realistic simulated data so the agent behaves identically
to a real AWS environment — perfect for live demos and workshops.

WHAT EACH TOOL DEMONSTRATES (tie-back to slide content)
--------------------------------------------------------
  list_aws_resources    → Tools connect the agent to external systems
  describe_resource     → Grounding: agent gets current state, not training knowledge
  check_resource_health → Observation: synthesized health assessment
  get_environment_summary → Multi-source data aggregation
  request_human_review  → Human-in-the-loop pattern (explicit escalation path)
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from strands import tool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MOCK = os.getenv("AGENT_MOCK_AWS", "false").lower() == "true"


def _region() -> str:
    return os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"


def _client(service: str, region: str | None = None) -> Any:
    try:
        return boto3.client(service, region_name=region or _region())
    except NoCredentialsError:
        raise RuntimeError(
            "AWS credentials not found.\n"
            "Configure with: aws configure\n"
            "Or set AGENT_MOCK_AWS=true for demo mode."
        )


def _wrap(data: Any, tool_name: str) -> str:
    """
    Wrap tool output in a consistent JSON envelope.

    The agent reads tool output as text in its context window.
    Consistent structure + ISO timestamps help the model parse
    and cite results accurately.
    """
    return json.dumps(
        {
            "tool": tool_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "region": _region(),
            "mock_mode": _MOCK,
            "data": data,
        },
        indent=2,
        default=str,
    )


# ---------------------------------------------------------------------------
# Realistic mock data
# Mirrors what you'd see in a real AWS account running microservices.
# ---------------------------------------------------------------------------

_MOCK_ECS = {
    "us-east-1": [
        {
            "name": "api-gateway-svc",
            "cluster": "prod-cluster",
            "status": "ACTIVE",
            "running": 3,
            "desired": 3,
            "task_def": "api-gateway:42",
            "launch_type": "FARGATE",
        },
        {
            "name": "auth-service",
            "cluster": "prod-cluster",
            "status": "ACTIVE",
            "running": 2,
            "desired": 2,
            "task_def": "auth-service:15",
            "launch_type": "FARGATE",
        },
        {
            "name": "inventory-svc",
            "cluster": "prod-cluster",
            "status": "ACTIVE",
            "running": 5,
            "desired": 5,
            "task_def": "inventory-svc:8",
            "launch_type": "FARGATE",
        },
        {
            "name": "notification-svc",
            "cluster": "prod-cluster",
            "status": "ACTIVE",
            "running": 1,
            "desired": 2,
            "task_def": "notification-svc:3",
            "launch_type": "FARGATE",
            "note": "Running below desired — possible container crash",
        },
    ],
    "us-west-2": [
        {
            "name": "api-gateway-svc",
            "cluster": "dr-cluster",
            "status": "ACTIVE",
            "running": 2,
            "desired": 2,
            "task_def": "api-gateway:41",
            "launch_type": "FARGATE",
        },
    ],
}

_MOCK_EC2 = {
    "us-east-1": [
        {"id": "i-0abc000001", "type": "m5.large",  "state": "running", "name": "eks-node-01"},
        {"id": "i-0abc000002", "type": "m5.large",  "state": "running", "name": "eks-node-02"},
        {"id": "i-0abc000003", "type": "t3.medium", "state": "running", "name": "bastion-host"},
        {"id": "i-0abc000004", "type": "t3.micro",  "state": "stopped", "name": "dev-sandbox"},
    ],
}

_MOCK_RDS = {
    "us-east-1": [
        {
            "id": "prod-postgres-01",
            "engine": "postgres",
            "version": "15.4",
            "status": "available",
            "class": "db.r6g.large",
            "multi_az": True,
            "storage_gb": 500,
        },
        {
            "id": "reporting-mysql",
            "engine": "mysql",
            "version": "8.0.35",
            "status": "available",
            "class": "db.t3.medium",
            "multi_az": False,
            "storage_gb": 100,
            "note": "Single-AZ — no automatic failover",
        },
    ],
}

_MOCK_LAMBDA = {
    "us-east-1": [
        {"name": "process-events",      "runtime": "python3.12", "state": "Active"},
        {"name": "send-notifications",  "runtime": "nodejs20.x", "state": "Active"},
        {"name": "cleanup-expired-data","runtime": "python3.12", "state": "Active"},
    ],
}

_MOCK_DESCRIBE = {
    "ecs/notification-svc": {
        "name": "notification-svc",
        "cluster": "prod-cluster",
        "status": "ACTIVE",
        "running": 1,
        "desired": 2,
        "task_def": "notification-svc:3",
        "launch_type": "FARGATE",
        "cpu": "256",
        "memory": "512",
        "recent_events": [
            {
                "time": "2025-02-20T14:05:00Z",
                "message": "(service notification-svc) failed to launch a task: EssentialContainerExited.",
            },
            {
                "time": "2025-02-20T14:04:00Z",
                "message": "(service notification-svc) has stopped 1 running tasks: task abc123.",
            },
        ],
    },
    "ecs/api-gateway-svc": {
        "name": "api-gateway-svc",
        "cluster": "prod-cluster",
        "status": "ACTIVE",
        "running": 3,
        "desired": 3,
        "task_def": "api-gateway:42",
        "launch_type": "FARGATE",
        "cpu": "512",
        "memory": "1024",
        "recent_events": [],
    },
}


# ---------------------------------------------------------------------------
# TOOL 1 — list_aws_resources
# ---------------------------------------------------------------------------

@tool
def list_aws_resources(service_type: str, region: str = "us-east-1") -> str:
    """
    List running AWS resources of the specified type in a region.

    Use this tool whenever you need to know what is currently deployed.
    Never guess or rely on prior knowledge — always call this tool first.

    Parameters
    ----------
    service_type : str
        One of: "ecs", "ec2", "rds", "lambda"
    region : str
        AWS region to query. Default: "us-east-1"

    Returns
    -------
    str
        JSON list of resources with status information.
    """
    svc = service_type.lower().strip()

    if _MOCK:
        mock_map = {"ecs": _MOCK_ECS, "ec2": _MOCK_EC2, "rds": _MOCK_RDS, "lambda": _MOCK_LAMBDA}
        if svc not in mock_map:
            return _wrap({"error": f"Unknown service_type '{svc}'", "supported": list(mock_map)}, "list_aws_resources")
        items = mock_map[svc].get(region, mock_map[svc].get("us-east-1", []))
        return _wrap({"service_type": svc, "region": region, "count": len(items), "resources": items}, "list_aws_resources")

    # --- live AWS ---
    try:
        if svc == "ecs":
            return _live_list_ecs(region)
        elif svc == "ec2":
            return _live_list_ec2(region)
        elif svc == "rds":
            return _live_list_rds(region)
        elif svc == "lambda":
            return _live_list_lambda(region)
        else:
            return _wrap({"error": f"Unsupported service_type: '{svc}'"}, "list_aws_resources")
    except ClientError as exc:
        return _wrap({"error": str(exc)}, "list_aws_resources")


def _live_list_ecs(region: str) -> str:
    ecs = _client("ecs", region)
    cluster_arns = ecs.list_clusters().get("clusterArns", [])
    resources: list[dict] = []
    for arn in cluster_arns:
        cluster = arn.split("/")[-1]
        for svc_arn in ecs.list_services(cluster=arn).get("serviceArns", []):
            for svc in ecs.describe_services(cluster=arn, services=[svc_arn]).get("services", []):
                resources.append({
                    "name": svc["serviceName"], "cluster": cluster,
                    "status": svc["status"],
                    "running": svc["runningCount"], "desired": svc["desiredCount"],
                    "task_def": svc["taskDefinition"].split("/")[-1],
                })
    return _wrap({"service_type": "ecs", "region": region, "count": len(resources), "resources": resources}, "list_aws_resources")


def _live_list_ec2(region: str) -> str:
    ec2 = _client("ec2", region)
    resources = []
    for r in ec2.describe_instances().get("Reservations", []):
        for inst in r.get("Instances", []):
            name = next((t["Value"] for t in inst.get("Tags", []) if t["Key"] == "Name"), "")
            resources.append({
                "id": inst["InstanceId"], "type": inst["InstanceType"],
                "state": inst["State"]["Name"], "name": name,
            })
    return _wrap({"service_type": "ec2", "region": region, "count": len(resources), "resources": resources}, "list_aws_resources")


def _live_list_rds(region: str) -> str:
    rds = _client("rds", region)
    resources = [
        {
            "id": db["DBInstanceIdentifier"], "engine": db["Engine"],
            "version": db["EngineVersion"], "status": db["DBInstanceStatus"],
            "class": db["DBInstanceClass"], "multi_az": db["MultiAZ"],
            "storage_gb": db["AllocatedStorage"],
        }
        for db in rds.describe_db_instances().get("DBInstances", [])
    ]
    return _wrap({"service_type": "rds", "region": region, "count": len(resources), "resources": resources}, "list_aws_resources")


def _live_list_lambda(region: str) -> str:
    lam = _client("lambda", region)
    resources = [
        {"name": fn["FunctionName"], "runtime": fn.get("Runtime", "N/A"), "state": fn.get("State", "Unknown")}
        for fn in lam.list_functions().get("Functions", [])
    ]
    return _wrap({"service_type": "lambda", "region": region, "count": len(resources), "resources": resources}, "list_aws_resources")


# ---------------------------------------------------------------------------
# TOOL 2 — describe_resource
# ---------------------------------------------------------------------------

@tool
def describe_resource(service_type: str, resource_name: str, region: str = "us-east-1") -> str:
    """
    Get detailed configuration and recent events for a specific AWS resource.

    Use this after list_aws_resources identifies something worth investigating.
    Provides much more detail than the listing — including recent events,
    deployment history, and configuration parameters.

    Parameters
    ----------
    service_type : str
        One of: "ecs", "ec2", "rds"
    resource_name : str
        Service name (ECS), instance ID (EC2), or DB identifier (RDS).
    region : str
        AWS region. Default: "us-east-1"

    Returns
    -------
    str
        JSON with full resource details, recent events, and configuration.
    """
    if _MOCK:
        key = f"{service_type.lower()}/{resource_name}"
        data = _MOCK_DESCRIBE.get(key)
        if data:
            return _wrap(data, "describe_resource")
        return _wrap(
            {"error": f"Resource '{resource_name}' ({service_type}) not found in {region}",
             "hint": "Use list_aws_resources to discover available resources."},
            "describe_resource",
        )

    # --- live AWS ---
    try:
        if service_type.lower() == "ecs":
            return _live_describe_ecs(resource_name, region)
        elif service_type.lower() == "ec2":
            return _live_describe_ec2(resource_name, region)
        elif service_type.lower() == "rds":
            return _live_describe_rds(resource_name, region)
        else:
            return _wrap({"error": f"Unsupported service_type: '{service_type}'"}, "describe_resource")
    except ClientError as exc:
        return _wrap({"error": str(exc)}, "describe_resource")


def _live_describe_ecs(service_name: str, region: str) -> str:
    ecs = _client("ecs", region)
    for cluster_arn in ecs.list_clusters().get("clusterArns", []):
        svcs = ecs.describe_services(cluster=cluster_arn, services=[service_name]).get("services", [])
        if svcs and svcs[0]["status"] != "INACTIVE":
            s = svcs[0]
            return _wrap({
                "name": s["serviceName"], "cluster": cluster_arn.split("/")[-1],
                "status": s["status"], "running": s["runningCount"], "desired": s["desiredCount"],
                "task_def": s["taskDefinition"], "launch_type": s.get("launchType"),
                "recent_events": s.get("events", [])[:5],
                "deployments": [
                    {"status": d["status"], "running": d["runningCount"], "desired": d["desiredCount"]}
                    for d in s.get("deployments", [])
                ],
            }, "describe_resource")
    return _wrap({"error": f"ECS service '{service_name}' not found in any cluster in {region}"}, "describe_resource")


def _live_describe_ec2(instance_id: str, region: str) -> str:
    ec2 = _client("ec2", region)
    resp = ec2.describe_instances(InstanceIds=[instance_id])
    if not resp["Reservations"]:
        return _wrap({"error": f"Instance '{instance_id}' not found"}, "describe_resource")
    inst = resp["Reservations"][0]["Instances"][0]
    return _wrap({
        "id": inst["InstanceId"], "type": inst["InstanceType"], "state": inst["State"]["Name"],
        "name": next((t["Value"] for t in inst.get("Tags", []) if t["Key"] == "Name"), ""),
        "private_ip": inst.get("PrivateIpAddress"), "public_ip": inst.get("PublicIpAddress"),
        "vpc_id": inst.get("VpcId"), "subnet_id": inst.get("SubnetId"),
        "security_groups": [sg["GroupName"] for sg in inst.get("SecurityGroups", [])],
    }, "describe_resource")


def _live_describe_rds(db_id: str, region: str) -> str:
    rds = _client("rds", region)
    db = rds.describe_db_instances(DBInstanceIdentifier=db_id)["DBInstances"][0]
    return _wrap({
        "id": db["DBInstanceIdentifier"], "engine": db["Engine"], "version": db["EngineVersion"],
        "status": db["DBInstanceStatus"], "class": db["DBInstanceClass"], "multi_az": db["MultiAZ"],
        "storage_gb": db["AllocatedStorage"], "endpoint": db.get("Endpoint", {}),
        "deletion_protection": db.get("DeletionProtection"),
        "backup_retention_days": db.get("BackupRetentionPeriod"),
    }, "describe_resource")


# ---------------------------------------------------------------------------
# TOOL 3 — check_resource_health
# ---------------------------------------------------------------------------

@tool
def check_resource_health(service_type: str, resource_name: str, region: str = "us-east-1") -> str:
    """
    Evaluate the health of a specific AWS resource and return a structured
    health report with findings and recommended actions.

    This tool synthesises raw AWS data into an opinionated assessment:
      - healthy  : resource is operating normally
      - degraded : resource is running but has issues
      - critical : resource is down or severely impaired

    Use this when you need to give a definitive health verdict rather than
    just listing raw state. Recommended next step: request_human_review if
    the status is degraded or critical.

    Parameters
    ----------
    service_type : str
        One of: "ecs", "ec2", "rds"
    resource_name : str
        Name or ID of the resource.
    region : str
        AWS region. Default: "us-east-1"

    Returns
    -------
    str
        JSON health report: status, findings list, recommendations list.
    """
    if _MOCK:
        return _mock_health(service_type, resource_name, region)

    raw = describe_resource(service_type, resource_name, region)
    detail = json.loads(raw)["data"]
    if "error" in detail:
        return _wrap({"health": "unknown", "error": detail["error"]}, "check_resource_health")
    return _derive_health(service_type, resource_name, detail, region)


def _mock_health(svc: str, name: str, region: str) -> str:
    catalog = {
        ("ecs", "api-gateway-svc"): {
            "health": "healthy",
            "findings": [
                "✅ Running tasks (3) match desired count (3)",
                "✅ No recent task failures",
                "✅ Active deployment is stable",
            ],
            "recommendations": [],
        },
        ("ecs", "auth-service"): {
            "health": "healthy",
            "findings": [
                "✅ Running tasks (2) match desired count (2)",
                "✅ No recent events to report",
            ],
            "recommendations": [],
        },
        ("ecs", "notification-svc"): {
            "health": "degraded",
            "findings": [
                "⚠️  Running tasks (1) below desired count (2) — 50% capacity",
                "⚠️  Recent event: EssentialContainerExited — container crashed",
                "⚠️  1 task failure in current deployment",
            ],
            "recommendations": [
                "Check CloudWatch Logs for the notification-svc task definition",
                "Review CPU/memory limits — currently 256 CPU units / 512 MB (may be undersized)",
                "Inspect container exit code in ECS task stopped reason",
                "Use request_human_review to escalate for immediate investigation",
            ],
        },
        ("rds", "reporting-mysql"): {
            "health": "degraded",
            "findings": [
                "⚠️  Single-AZ deployment — no automatic failover if instance fails",
                "ℹ️  Database is available but not production-hardened",
            ],
            "recommendations": [
                "Enable Multi-AZ for production workloads",
                "Use request_human_review to schedule the upgrade during maintenance window",
            ],
        },
        ("rds", "prod-postgres-01"): {
            "health": "healthy",
            "findings": [
                "✅ Database status: available",
                "✅ Multi-AZ enabled — automatic failover configured",
            ],
            "recommendations": [],
        },
    }
    key = (svc.lower(), name)
    data = catalog.get(key, {
        "health": "unknown",
        "findings": [f"Resource '{name}' ({svc}) not found in mock data"],
        "recommendations": ["Use list_aws_resources to discover available resources"],
    })
    return _wrap({"resource": name, "service_type": svc, "region": region, **data}, "check_resource_health")


def _derive_health(svc: str, name: str, detail: dict, region: str) -> str:
    findings: list[str] = []
    recommendations: list[str] = []
    health = "healthy"

    if svc == "ecs":
        running, desired = detail.get("running", 0), detail.get("desired", 0)
        if running < desired:
            health = "degraded" if running > 0 else "critical"
            findings.append(f"⚠️  Running ({running}) < Desired ({desired})")
            recommendations.append("Check CloudWatch Logs for task failure details")
        else:
            findings.append(f"✅ Running ({running}) == Desired ({desired})")
        for evt in detail.get("recent_events", [])[:3]:
            msg = evt.get("message", "")
            if any(w in msg.lower() for w in ("fail", "error", "stopped", "exit")):
                health = "degraded" if health == "healthy" else health
                findings.append(f"⚠️  Event: {msg[:120]}")

    elif svc == "ec2":
        state = detail.get("state", "")
        if state == "running":
            findings.append("✅ Instance is running")
        else:
            health = "critical" if state == "terminated" else "degraded"
            findings.append(f"⚠️  Instance state: {state}")

    elif svc == "rds":
        status = detail.get("status", "")
        if status == "available":
            findings.append("✅ Database is available")
        else:
            health = "critical" if status in ("failed", "stopped") else "degraded"
            findings.append(f"⚠️  Database status: {status}")
        if not detail.get("multi_az"):
            findings.append("ℹ️  Single-AZ — no automatic failover")
            recommendations.append("Consider enabling Multi-AZ for production")

    return _wrap(
        {"resource": name, "service_type": svc, "region": region,
         "health": health, "findings": findings, "recommendations": recommendations},
        "check_resource_health",
    )


# ---------------------------------------------------------------------------
# TOOL 4 — get_environment_summary
# ---------------------------------------------------------------------------

@tool
def get_environment_summary(region: str = "us-east-1") -> str:
    """
    Get a high-level summary of the infrastructure environment in a region.

    This tool aggregates across all service types to give a single-pane-of-glass
    view. Use it as a starting point when you need to orient yourself before
    drilling into specific resources.

    Unlike listing individual service types, this gives you cross-service
    context so you can identify patterns (e.g. an ECS task failing to connect
    to an RDS database that is also in a degraded state).

    Parameters
    ----------
    region : str
        AWS region to summarise. Default: "us-east-1"

    Returns
    -------
    str
        JSON summary: resource counts, health indicators, and action items.
    """
    if _MOCK:
        return _wrap({
            "region": region,
            "services": {
                "ecs":    {"total": 4, "healthy": 3, "degraded": 1, "issues": ["notification-svc running 1/2"]},
                "ec2":    {"total": 4, "running": 3, "stopped": 1,  "issues": ["dev-sandbox is stopped"]},
                "rds":    {"total": 2, "available": 2,              "issues": ["reporting-mysql is Single-AZ"]},
                "lambda": {"total": 3, "active": 3,                 "issues": []},
            },
            "overall_health": "degraded",
            "action_items": [
                "HIGH: notification-svc is at 50% capacity — container crash detected",
                "LOW:  reporting-mysql is Single-AZ — no automatic failover",
                "INFO: dev-sandbox EC2 instance is stopped — confirm intentional",
            ],
        }, "get_environment_summary")

    summary: dict[str, Any] = {"region": region, "services": {}, "action_items": []}
    overall = "healthy"
    for svc in ["ecs", "ec2", "rds", "lambda"]:
        try:
            raw = list_aws_resources(svc, region)
            data = json.loads(raw)["data"]
            summary["services"][svc] = {"total": data.get("count", 0)}
        except Exception as exc:
            summary["services"][svc] = {"error": str(exc)}
            overall = "unknown"
    summary["overall_health"] = overall
    return _wrap(summary, "get_environment_summary")


# ---------------------------------------------------------------------------
# TOOL 5 — request_human_review
# ---------------------------------------------------------------------------

@tool
def request_human_review(
    issue_summary: str,
    urgency: str,
    full_context: str,
    recommended_action: str,
) -> str:
    """
    Escalate an issue or proposed action to a human engineer for review.

    This is the Human-in-the-Loop pattern. In Module 1, ALL write operations
    MUST go through this tool — the agent never modifies infrastructure directly.

    Use this tool when you have:
      - Identified a problem that needs fixing (degraded/critical health)
      - A recommendation that would require an AWS API write call
      - Uncertainty that warrants human judgment before proceeding

    In future modules this escalation will integrate with SNS notifications,
    Slack, and an async approval workflow. In Module 1 it logs a structured
    review record with a unique ticket ID.

    Parameters
    ----------
    issue_summary : str
        One-sentence description of the issue or proposed action.
    urgency : str
        One of: "critical", "high", "medium", "low"
    full_context : str
        All relevant findings from your investigation. Include tool outputs,
        service names, metrics, and anything a human reviewer would need.
    recommended_action : str
        Specific, actionable steps you recommend the engineer take.

    Returns
    -------
    str
        JSON confirmation with ticket_id, status, and reviewer instructions.
    """
    urgency_norm = urgency.lower().strip()
    if urgency_norm not in {"critical", "high", "medium", "low"}:
        urgency_norm = "medium"

    ticket_id = f"INFRA-{int(time.time())}"
    record = {
        "ticket_id": ticket_id,
        "status": "PENDING_HUMAN_REVIEW",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "urgency": urgency_norm.upper(),
        "issue_summary": issue_summary,
        "full_context": full_context,
        "recommended_action": recommended_action,
        "raised_by": "infrastructure-agent/module-1",
        "reviewer_note": (
            f"Review ticket {ticket_id}. "
            "Approve and execute the recommended action, or reject with a reason. "
            "Context and recommended steps are included above."
        ),
    }

    # Console notification — in production this would post to Slack / SNS / JIRA
    border = "=" * 62
    print(f"\n{border}")
    print(f"  🔔  HUMAN REVIEW REQUIRED  [{urgency_norm.upper()}]  {ticket_id}")
    print(border)
    print(f"  Issue   : {issue_summary}")
    print(f"  Action  : {recommended_action[:120]}{'...' if len(recommended_action) > 120 else ''}")
    print(f"  Context : {full_context[:200]}{'...' if len(full_context) > 200 else ''}")
    print(f"{border}\n")

    return _wrap(record, "request_human_review")


# ---------------------------------------------------------------------------
# Tool registry — imported by agent.py
# ---------------------------------------------------------------------------

ALL_TOOLS = [
    list_aws_resources,
    describe_resource,
    check_resource_health,
    get_environment_summary,
    request_human_review,
]
