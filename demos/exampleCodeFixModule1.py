"""Atlas local-guide agent, rewritten against the agentic security framework.

NOTE: Use --self-test to simulate production boundaries.

Control mapping (framework section -> component):
  §4.1  IdentityAuthority, AgentIdentity   asymmetric (Ed25519) identity with a
                                           trust anchor, key_id, expiry
  §4.2  SessionContext                     every action bound to a human principal,
                                           session, and trace ID
  §5.2  ToolBroker                         deterministic PEP: allowlist, schema
                                           validation, rate limits, approval hook,
                                           fail-closed; the model only proposes
  §7.2  per-tool validators                typed, canonicalized inputs; unknown
                                           values rejected or bounded
  §9.1  AuditLog / AuditVerifier           append-only, sequence-numbered,
                                           hash-chained, Ed25519-signed records;
                                           verifier holds public keys only

PRODUCTION BOUNDARIES — in-process stand-ins that must be replaced before
production (framework Priority 1):
  [PB-1] IdentityAuthority -> SPIFFE/SPIRE, cloud workload identity, or org PKI.
  [PB-2] AgentIdentity private key in memory -> KMS/HSM-held key; process signs
         via the KMS API and never handles raw key bytes.
  [PB-3] AuditLog local file -> independently controlled append-only/WORM sink
         (e.g. S3 Object Lock) with external checkpoint anchoring. The agent
         must have no delete/modify access to that sink.
  [PB-4] ToolBroker in-process -> out-of-process PDP/PEP so a compromised agent
         runtime cannot bypass or patch policy. Also the corroboration control:
         an independently authored record stream to cross-check this one.
  [PB-5] SessionContext principal -> verified OIDC ID token or mTLS-bound
         assertion. As shipped, SessionContext.start() accepts a self-asserted
         string; the audit trail identifies a session, not a cryptographically
         bound human. Use SessionContext.from_assertion() with real token
         validation (signature against IdP JWKS, iss/aud/exp) in production.

Remediation order under compromise-resistance criteria:
  PB-3 (history preservation) -> PB-2 (revocability) -> PB-5 (principal
  binding) -> PB-1 (anti-minting) -> PB-4 (cross-stream corroboration).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

# ----------------------------------------------------------------------------
# Static demo data (unchanged from original)
# ----------------------------------------------------------------------------

WEATHER_DATA = {
    "San Francisco": {"temp_f": 62, "conditions": "Partly Cloudy", "humidity": 75},
    "New York": {"temp_f": 78, "conditions": "Sunny", "humidity": 55},
    "Seattle": {"temp_f": 58, "conditions": "Rainy", "humidity": 85},
    "Austin": {"temp_f": 95, "conditions": "Hot and Sunny", "humidity": 40},
}

TRAVEL_TIMES = {
    ("San Francisco", "San Jose"): {"driving": 45, "transit": 75, "walking": 600},
    ("San Francisco", "Oakland"): {"driving": 25, "transit": 40, "walking": 180},
    ("New York", "Brooklyn"): {"driving": 30, "transit": 25, "walking": 120},
}

EVENTS = {
    "San Francisco": [
        {"name": "Jazz Night at SFJAZZ", "category": "music", "time": "8:00 PM", "venue": "SFJAZZ Center"},
        {"name": "Giants vs Dodgers", "category": "sports", "time": "7:15 PM", "venue": "Oracle Park"},
        {"name": "Street Food Festival", "category": "food", "time": "11:00 AM", "venue": "Ferry Building"},
        {"name": "AI Meetup", "category": "tech", "time": "6:30 PM", "venue": "Moscone Center"},
    ],
    "New York": [
        {"name": "Broadway Show: Hamilton", "category": "music", "time": "7:30 PM", "venue": "Richard Rodgers Theatre"},
        {"name": "Yankees Game", "category": "sports", "time": "7:05 PM", "venue": "Yankee Stadium"},
        {"name": "Smorgasburg Food Market", "category": "food", "time": "11:00 AM", "venue": "Williamsburg"},
    ],
}


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------------------
# §4.1 Identity: trust anchor + asymmetric agent identity
# ----------------------------------------------------------------------------

@dataclass(frozen=True)
class Attestation:
    """Authority-signed binding: agent_id <-> key_id <-> public key, with expiry."""
    claims: dict
    signature: str  # hex, over canonical_json(claims), by the authority key


class IdentityAuthority:
    """[PB-1] Stand-in trust anchor. Production: SPIFFE/SPIRE / cloud workload
    identity / org PKI. Holds its own signing key; verifiers trust only its
    public key, never individual agent keys directly."""

    def __init__(self) -> None:
        self._key = Ed25519PrivateKey.generate()
        self.public_key: Ed25519PublicKey = self._key.public_key()

    def issue(self, agent_id: str, agent_public_key: Ed25519PublicKey,
              ttl: timedelta = timedelta(hours=1)) -> Attestation:
        key_id = key_fingerprint(agent_public_key)
        claims = {
            "agent_id": agent_id,
            "key_id": key_id,
            "public_key": public_key_hex(agent_public_key),
            "not_before": utc_now(),
            "not_after": (datetime.now(timezone.utc) + ttl).isoformat(),
        }
        sig = self._key.sign(canonical_json(claims)).hex()
        return Attestation(claims=claims, signature=sig)


def public_key_hex(pk: Ed25519PublicKey) -> str:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    return pk.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def key_fingerprint(pk: Ed25519PublicKey) -> str:
    return hashlib.sha256(bytes.fromhex(public_key_hex(pk))).hexdigest()[:16]


@dataclass(frozen=True)
class AgentIdentity:
    """Signs; cannot be used to verify a trust chain (no verify() on purpose —
    verification lives in AuditVerifier, which never holds a private key)."""
    agent_id: str
    key_id: str
    attestation: Attestation
    _private_key: Ed25519PrivateKey = field(repr=False)

    @classmethod
    def issue(cls, authority: IdentityAuthority, agent_id: str) -> "AgentIdentity":
        # [PB-2] Production: key generated and held in KMS/HSM; sign() delegates
        # to the KMS API. Raw key bytes never enter this process.
        private_key = Ed25519PrivateKey.generate()
        attestation = authority.issue(agent_id, private_key.public_key())
        return cls(
            agent_id=agent_id,
            key_id=attestation.claims["key_id"],
            attestation=attestation,
            _private_key=private_key,
        )

    def sign(self, payload: dict) -> str:
        return self._private_key.sign(canonical_json(payload)).hex()


# ----------------------------------------------------------------------------
# §4.2 Principal binding: who the agent is acting for
# ----------------------------------------------------------------------------

@dataclass(frozen=True)
class SessionContext:
    principal: str            # "iss|sub" when token-bound; self-asserted otherwise
    session_id: str
    trace_id: str
    principal_binding: str    # "assertion" | "self-asserted"
    token_hash: Optional[str] = None  # sha256 of the raw assertion, for correlation

    @classmethod
    def from_assertion(cls, raw_token: str, claims: dict) -> "SessionContext":
        """[PB-5] Production entry point. `claims` must come from real validation
        of `raw_token` (signature against the IdP's JWKS, plus aud check) —
        this method enforces only structural requirements and expiry."""
        for required in ("iss", "sub", "exp"):
            if required not in claims:
                raise ValueError(f"assertion missing required claim: {required}")
        if datetime.fromtimestamp(claims["exp"], tz=timezone.utc) < datetime.now(timezone.utc):
            raise ValueError("assertion expired")
        return cls(
            principal=f"{claims['iss']}|{claims['sub']}",
            session_id=f"sess-{uuid.uuid4().hex[:12]}",
            trace_id=f"trace-{uuid.uuid4().hex[:12]}",
            principal_binding="assertion",
            token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        )

    @classmethod
    def start(cls, principal: str) -> "SessionContext":
        """Demo/testing only: principal is a self-asserted string. Audit records
        created under this context are labeled accordingly."""
        return cls(principal=principal,
                   session_id=f"sess-{uuid.uuid4().hex[:12]}",
                   trace_id=f"trace-{uuid.uuid4().hex[:12]}",
                   principal_binding="self-asserted")


_current_session: ContextVar[Optional[SessionContext]] = ContextVar("session", default=None)


# ----------------------------------------------------------------------------
# §9.1 Audit: append-only, hash-chained, signed; independent verifier
# ----------------------------------------------------------------------------

GENESIS_HASH = "0" * 64


class AuditLog:
    """[PB-3] Local append-only file stands in for WORM storage. Records carry
    a monotonic sequence number and the hash of the previous record, and are
    Ed25519-signed. Deletion/reordering/truncation breaks the chain."""

    def __init__(self, identity: AgentIdentity, path: Path) -> None:
        self.identity = identity
        self.path = path
        self._lock = threading.Lock()
        self._seq = 0
        self._prev_hash = GENESIS_HASH
        self.log_id = f"log-{uuid.uuid4().hex[:12]}"

    def emit(self, event_type: str, body: dict) -> dict:
        session = _current_session.get()
        with self._lock:
            record = {
                "log_id": self.log_id,
                "seq": self._seq,
                "prev_hash": self._prev_hash,
                "timestamp": utc_now(),
                "event_type": event_type,
                "agent_id": self.identity.agent_id,
                "key_id": self.identity.key_id,
                "principal": session.principal if session else None,
                "principal_binding": session.principal_binding if session else None,
                "principal_token_hash": session.token_hash if session else None,
                "session_id": session.session_id if session else None,
                "trace_id": session.trace_id if session else None,
                "body": body,
            }
            record["signature"] = self.identity.sign(record)
            line = canonical_json(record)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line.decode("utf-8") + "\n")
            self._prev_hash = hashlib.sha256(line).hexdigest()
            self._seq += 1
            return record


class AuditVerifier:
    """Holds ONLY public material: the authority's public key and attestations.
    Structurally cannot forge records — asymmetric fix for the original
    HMAC self-verification flaw."""

    def __init__(self, authority_public_key: Ed25519PublicKey) -> None:
        self.authority_public_key = authority_public_key
        self._agent_keys: dict[str, Ed25519PublicKey] = {}  # key_id -> pubkey

    def register(self, attestation: Attestation) -> None:
        # Trust chain: verify the authority's signature over the claims
        self.authority_public_key.verify(
            bytes.fromhex(attestation.signature),
            canonical_json(attestation.claims),
        )
        claims = attestation.claims
        if datetime.fromisoformat(claims["not_after"]) < datetime.now(timezone.utc):
            raise ValueError(f"attestation for {claims['agent_id']} is expired")
        self._agent_keys[claims["key_id"]] = Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(claims["public_key"])
        )

    def verify_log(self, path: Path) -> dict:
        prev_hash, expected_seq, failures, count = GENESIS_HASH, 0, [], 0
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, start=1):
                count += 1
                record = json.loads(line)
                sig = record.pop("signature")
                if record["seq"] != expected_seq:
                    failures.append(f"line {lineno}: seq {record['seq']} != expected {expected_seq}")
                if record["prev_hash"] != prev_hash:
                    failures.append(f"line {lineno}: chain break")
                pubkey = self._agent_keys.get(record["key_id"])
                if pubkey is None:
                    failures.append(f"line {lineno}: unknown key_id {record['key_id']}")
                else:
                    try:
                        pubkey.verify(bytes.fromhex(sig), canonical_json(record))
                    except InvalidSignature:
                        failures.append(f"line {lineno}: bad signature")
                record["signature"] = sig
                prev_hash = hashlib.sha256(canonical_json(record)).hexdigest()
                expected_seq = record["seq"] + 1
        return {"records": count, "valid": not failures, "failures": failures}


# ----------------------------------------------------------------------------
# §5.2 / §7.2 Tool broker: deterministic PEP, fail-closed
# ----------------------------------------------------------------------------

POLICY_VERSION = "atlas-policy-v1"


class PolicyDenied(Exception):
    pass


def _validate_city(value: Any, field_name: str = "city") -> str:
    if not isinstance(value, str):
        raise PolicyDenied(f"{field_name}: must be a string")
    value = value.strip()
    if not (1 <= len(value) <= 80):
        raise PolicyDenied(f"{field_name}: length out of bounds")
    return value


def _validate_weather(kwargs: dict) -> dict:
    unknown = set(kwargs) - {"city"}
    if unknown:
        raise PolicyDenied(f"unknown fields: {sorted(unknown)}")
    return {"city": _validate_city(kwargs.get("city"))}


def _validate_travel(kwargs: dict) -> dict:
    unknown = set(kwargs) - {"origin", "destination", "mode"}
    if unknown:
        raise PolicyDenied(f"unknown fields: {sorted(unknown)}")
    mode = str(kwargs.get("mode", "driving")).lower().strip()
    if mode not in {"driving", "transit", "walking"}:
        raise PolicyDenied(f"mode: {mode!r} not in allowed set")  # reject, don't silently coerce
    return {
        "origin": _validate_city(kwargs.get("origin"), "origin"),
        "destination": _validate_city(kwargs.get("destination"), "destination"),
        "mode": mode,
    }


def _validate_events(kwargs: dict) -> dict:
    unknown = set(kwargs) - {"city", "category"}
    if unknown:
        raise PolicyDenied(f"unknown fields: {sorted(unknown)}")
    out: dict = {"city": _validate_city(kwargs.get("city"))}
    category = kwargs.get("category")
    if category is not None:
        category = str(category).lower().strip()
        if category not in {"music", "sports", "food", "tech"}:
            raise PolicyDenied(f"category: {category!r} not in allowed set")
        out["category"] = category
    return out


@dataclass(frozen=True)
class ToolPolicy:
    validator: Callable[[dict], dict]
    impl: Callable[..., Any]
    requires_approval: bool = False   # HITL hook (§8); all Atlas tools are read-only
    max_calls_per_session: int = 20


class ToolBroker:
    """The model proposes; this decides. Every decision — allow and deny — is
    an audit record with the policy id/version. [PB-4] Production: run this
    out-of-process so the agent runtime cannot patch it."""

    def __init__(self, audit: AuditLog, policies: dict[str, ToolPolicy]) -> None:
        self.audit = audit
        self.policies = policies
        self._call_counts: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()

    def execute(self, tool_name: str, kwargs: dict) -> Any:
        session = _current_session.get()
        decision_base = {
            "tool": tool_name,
            "input": kwargs,
            "policy_version": POLICY_VERSION,
        }
        try:
            if session is None:
                raise PolicyDenied("no authenticated session context")  # fail closed
            policy = self.policies.get(tool_name)
            if policy is None:
                raise PolicyDenied(f"tool {tool_name!r} not in allowlist")
            validated = policy.validator(kwargs)
            with self._lock:
                key = (session.session_id, tool_name)
                self._call_counts[key] = self._call_counts.get(key, 0) + 1
                if self._call_counts[key] > policy.max_calls_per_session:
                    raise PolicyDenied("per-session rate limit exceeded")
            if policy.requires_approval:
                # §8 hook: production obtains a cryptographically bound approval
                # of the canonical action and re-checks it at execution (TOCTOU).
                raise PolicyDenied("human approval required and not obtained")
        except PolicyDenied as e:
            self.audit.emit("tool_denied", {**decision_base, "decision": "deny", "reason": str(e)})
            return {"error": "POLICY_DENIED", "reason": str(e)}  # §6.5: stable, minimal error to model

        started = time.monotonic()
        result = policy.impl(**validated)
        self.audit.emit("tool_call", {
            **decision_base,
            "input_validated": validated,
            "decision": "allow",
            "outcome": {
                "status": "ok",
                "duration_ms": round((time.monotonic() - started) * 1000, 2),
                # §9.1: store an output reference/hash, not full payloads
                "output_sha256": hashlib.sha256(canonical_json(result)).hexdigest(),
            },
        })
        return result


# ----------------------------------------------------------------------------
# Tool implementations (pure business logic — no logging, no policy)
# ----------------------------------------------------------------------------

def _format_duration(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} minutes"
    hours, remainder = divmod(minutes, 60)
    return f"{hours}h {remainder}m"


def _get_weather_impl(city: str) -> dict:
    result = dict(WEATHER_DATA.get(city, {"temp_f": 70, "conditions": "Unknown", "humidity": 50}))
    result.update(city=city, timestamp=utc_now())
    return result


def _travel_time_impl(origin: str, destination: str, mode: str) -> dict:
    times = TRAVEL_TIMES.get((origin, destination)) or TRAVEL_TIMES.get((destination, origin)) \
        or {"driving": 30, "transit": 45, "walking": 90}
    duration = times[mode]
    return {"origin": origin, "destination": destination, "mode": mode,
            "duration_minutes": duration, "duration_text": _format_duration(duration)}


def _local_events_impl(city: str, category: Optional[str] = None) -> list:
    events = list(EVENTS.get(city, []))
    if category:
        events = [e for e in events if e["category"] == category]
    return events


def build_broker(audit: AuditLog) -> ToolBroker:
    return ToolBroker(audit, policies={
        "get_weather": ToolPolicy(_validate_weather, _get_weather_impl),
        "calculate_travel_time": ToolPolicy(_validate_travel, _travel_time_impl),
        "get_local_events": ToolPolicy(_validate_events, _local_events_impl),
    })


# ----------------------------------------------------------------------------
# Agent wiring — tools are thin proposal shims; the broker decides
# ----------------------------------------------------------------------------

def create_agent(broker: ToolBroker):
    from strands import Agent, tool
    from strands.models import BedrockModel

    @tool
    def get_weather(city: str) -> dict:
        """Get current weather for a city."""
        return broker.execute("get_weather", {"city": city})

    @tool
    def calculate_travel_time(origin: str, destination: str, mode: str = "driving") -> dict:
        """Travel time between two places. mode: driving | transit | walking."""
        return broker.execute("calculate_travel_time",
                              {"origin": origin, "destination": destination, "mode": mode})

    @tool
    def get_local_events(city: str, category: Optional[str] = None) -> list:
        """Local events for a city, optionally filtered by category
        (music | sports | food | tech)."""
        return broker.execute("get_local_events", {"city": city, "category": category})

    model = BedrockModel(
        model_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
        region_name="us-west-2",
    )
    # Note: no security claims in the prompt. Controls are enforced by the
    # broker/audit layer, not asserted to the model (§3.2).
    system_prompt = (
        "You are Atlas, a helpful local guide assistant. Use tools to gather "
        "weather, events, and travel time data before answering. Be concise, "
        "practical, and explicit about uncertainty when location data is "
        "approximate. If a tool returns POLICY_DENIED, tell the user that "
        "action was not permitted; do not retry it."
    )
    return Agent(model=model, system_prompt=system_prompt,
                 tools=[get_weather, calculate_travel_time, get_local_events])


# ----------------------------------------------------------------------------
# Entry points
# ----------------------------------------------------------------------------

def bootstrap(log_path: Path):
    authority = IdentityAuthority()
    identity = AgentIdentity.issue(authority, agent_id="atlas-local-guide")
    audit = AuditLog(identity, log_path)
    verifier = AuditVerifier(authority.public_key)
    verifier.register(identity.attestation)
    return identity, audit, verifier


def self_test(log_path: Path) -> int:
    """Exercise identity, broker, and audit-chain verification without a model."""
    identity, audit, verifier = bootstrap(log_path)
    broker = build_broker(audit)

    # 1. No session -> fail closed
    r = broker.execute("get_weather", {"city": "Seattle"})
    assert r.get("error") == "POLICY_DENIED", r

    token = _current_session.set(SessionContext.start(principal="user:nate@example.com"))
    try:
        # 2. Allowed calls
        assert broker.execute("get_weather", {"city": "Seattle"})["temp_f"] == 58
        assert broker.execute("calculate_travel_time",
                              {"origin": "San Francisco", "destination": "Oakland",
                               "mode": "transit"})["duration_minutes"] == 40
        assert len(broker.execute("get_local_events",
                                  {"city": "San Francisco", "category": "music"})) == 1
        # 3. Schema rejections
        assert broker.execute("calculate_travel_time",
                              {"origin": "A", "destination": "B", "mode": "teleport"})["error"] == "POLICY_DENIED"
        assert broker.execute("get_weather", {"city": "X", "extra": 1})["error"] == "POLICY_DENIED"
        # 4. Non-allowlisted tool
        assert broker.execute("delete_database", {})["error"] == "POLICY_DENIED"
    finally:
        _current_session.reset(token)

    # 5. Chain verifies clean
    report = verifier.verify_log(log_path)
    assert report["valid"] and report["records"] == 7, report

    # 6. Tampering is detected (flip one character in a signed record)
    tampered = log_path.with_suffix(".tampered")
    lines = log_path.read_text().splitlines()
    assert "Seattle" in lines[1]
    lines[1] = lines[1].replace("Seattle", "Sxattle", 1)
    tampered.write_text("\n".join(lines) + "\n")
    assert not verifier.verify_log(tampered)["valid"]

    # 7. Deletion is detected (drop a record)
    truncated = log_path.with_suffix(".truncated")
    truncated.write_text("\n".join(lines[:2] + lines[3:]) + "\n")
    assert not verifier.verify_log(truncated)["valid"]

    print(f"self-test passed: {report['records']} records, chain valid, "
          f"tamper + deletion detected")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true",
                        help="verify security controls without invoking the model")
    parser.add_argument("--log", default="atlas_audit.log", type=Path)
    args = parser.parse_args()

    if args.self_test:
        args.log.unlink(missing_ok=True)
        raise SystemExit(self_test(args.log))

    identity, audit, verifier = bootstrap(args.log)
    broker = build_broker(audit)
    agent = create_agent(broker)

    # Production: principal comes from an authenticated request (IdP token),
    # never a constant.
    token = _current_session.set(SessionContext.start(principal="user:demo"))
    try:
        response = agent(
            "I'm in San Francisco and want to do something fun this evening. "
            "What's the weather like, and are there any music events happening? "
            "If there's something good, how long would it take me to get there "
            "from the Ferry Building?"
        )
        print(response)
    finally:
        _current_session.reset(token)

    print(json.dumps(verifier.verify_log(args.log), indent=2))


if __name__ == "__main__":
    main()
