"""
module3/evaluators/llm_judge.py
================================
LLM-as-judge evaluation implementation.

Uses Claude Opus (different from agent's Sonnet) to evaluate agent outputs
against defined criteria. This is the core evaluation pattern for Module 3.
"""

from __future__ import annotations

import json
import os
import random
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from module3.config.models import get_judge_model

# Mock mode flag
_MOCK = os.getenv("AGENT_MOCK_REPO", "false").lower() == "true"
MAX_TASK_DESCRIPTION_CHARS = int(os.getenv("MODULE3_JUDGE_MAX_TASK_DESCRIPTION_CHARS", "4000"))
MAX_AGENT_OUTPUT_CHARS = int(os.getenv("MODULE3_JUDGE_MAX_AGENT_OUTPUT_CHARS", "50000"))
MAX_REFERENCE_ANSWER_CHARS = int(os.getenv("MODULE3_JUDGE_MAX_REFERENCE_ANSWER_CHARS", "20000"))
MAX_CRITERIA = int(os.getenv("MODULE3_JUDGE_MAX_CRITERIA", "20"))


# ---------------------------------------------------------------------------
# Judge Prompt Templates
# ---------------------------------------------------------------------------

JUDGE_PROMPT_TEMPLATE = """You are an expert evaluator for AI agent outputs. Your job is to assess the quality of an agent's response against defined criteria.

## Task
{task_description}

## Agent's Output
{agent_output}

## Reference Answer (if available)
{reference_answer}

## Evaluation Criteria

{criteria}

## Scoring Rubric

For each criterion, provide a score from 0-100:
- 90-100: Excellent - Exceeds expectations
- 70-89: Good - Meets expectations with minor issues
- 50-69: Acceptable - Meets basic requirements but has notable gaps
- 30-49: Poor - Significant issues or missing elements
- 0-29: Very Poor - Does not meet requirements

## Response Format

Provide your evaluation as a JSON object with the following structure:
- scores: object with criterion names as keys and scores (0-100) as values
- overall_score: average of all criterion scores
- rationale: object with criterion names as keys and explanations as values
- strengths: array of strengths identified
- weaknesses: array of weaknesses identified
- recommendations: array of recommendations for improvement

Be thorough but concise. Focus on specific, actionable feedback.
"""


# ---------------------------------------------------------------------------
# LLM-as-Judge Function
# ---------------------------------------------------------------------------

def create_judge_prompt(
    task_description: str,
    agent_output: str,
    criteria: dict[str, str],
    reference_answer: str | None = None,
) -> str:
    """
    Create a judge prompt for evaluating agent output.

    Parameters
    ----------
    task_description : str
        Description of the task the agent was asked to perform.
    agent_output : str
        The agent's actual output.
    criteria : dict
        Evaluation criteria as {criterion_name: description}.
    reference_answer : str, optional
        Expected or reference answer for comparison.

    Returns
    -------
    str
        Formatted judge prompt.
    """
    # Format criteria
    criteria_text = "\n".join([
        f"**{name}**: {desc}"
        for name, desc in criteria.items()
    ])
    
    # Format reference answer
    ref_text = reference_answer if reference_answer else "Not provided"
    
    return JUDGE_PROMPT_TEMPLATE.format(
        task_description=task_description,
        agent_output=agent_output,
        reference_answer=ref_text,
        criteria=criteria_text,
    )


def _bounded_text(value: str | None, max_chars: int) -> str:
    """Bound user/model text size to limit prompt and logging cost."""
    text = value or ""
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}\n\n[TRUNCATED: original_length={len(text)}]"


def _validated_criteria(criteria: dict[str, str]) -> dict[str, str]:
    """Validate and normalize evaluation criteria."""
    if not isinstance(criteria, dict):
        raise TypeError("criteria must be a dictionary of {name: description}")
    if not criteria:
        raise ValueError("criteria must include at least one evaluation criterion")
    if len(criteria) > MAX_CRITERIA:
        raise ValueError(f"criteria exceeds maximum allowed entries ({MAX_CRITERIA})")

    normalized: dict[str, str] = {}
    for key, value in criteria.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("criteria keys must be non-empty strings")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("criteria descriptions must be non-empty strings")
        normalized[key.strip()] = value.strip()
    return normalized


def _extract_json_payload(response: str) -> str:
    """Extract JSON payload from plain text or fenced code block output."""
    if "```json" in response:
        return response.split("```json", 1)[1].split("```", 1)[0].strip()
    if "```" in response:
        return response.split("```", 1)[1].split("```", 1)[0].strip()
    return response.strip()


def _clamp_score(value: Any) -> int:
    """Normalize score into integer range 0..100."""
    try:
        numeric = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, numeric))


def _normalize_judge_result(
    parsed: dict[str, Any],
    criteria_keys: list[str],
) -> dict[str, Any]:
    """Normalize LLM judge output into a stable, bounded response shape."""
    raw_scores = parsed.get("scores", {})
    if not isinstance(raw_scores, dict):
        raw_scores = {}

    scores = {
        key: _clamp_score(raw_scores.get(key, 0))
        for key in criteria_keys
    }

    overall_score = _clamp_score(
        parsed.get("overall_score", sum(scores.values()) / max(1, len(scores)))
    )

    rationale_raw = parsed.get("rationale", {})
    rationale = rationale_raw if isinstance(rationale_raw, dict) else {}

    strengths = parsed.get("strengths", [])
    if not isinstance(strengths, list):
        strengths = []

    weaknesses = parsed.get("weaknesses", [])
    if not isinstance(weaknesses, list):
        weaknesses = []

    recommendations = parsed.get("recommendations", [])
    if not isinstance(recommendations, list):
        recommendations = []

    return {
        "scores": scores,
        "overall_score": overall_score,
        "rationale": rationale,
        "strengths": [str(item) for item in strengths[:20]],
        "weaknesses": [str(item) for item in weaknesses[:20]],
        "recommendations": [str(item) for item in recommendations[:20]],
    }


def evaluate_with_llm_judge(
    task_description: str,
    agent_output: str,
    criteria: dict[str, str],
    reference_answer: str | None = None,
    region: str | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    """
    Evaluate agent output using LLM-as-judge pattern.

    Uses Claude Opus (different from agent's Sonnet) for unbiased evaluation.

    Parameters
    ----------
    task_description : str
        Description of the task.
    agent_output : str
        Agent's output to evaluate.
    criteria : dict
        Evaluation criteria as {criterion_name: description}.
    reference_answer : str, optional
        Expected answer for comparison.
    region : str, optional
        AWS region for Bedrock.
    verbose : bool
        Print evaluation progress.

    Returns
    -------
    dict
        Evaluation results with scores, rationale, and recommendations.

    Example
    -------
    >>> criteria = {
    ...     "completeness": "All required CDK resources are included",
    ...     "best_practices": "Follows AWS and CDK best practices",
    ...     "security": "Proper security configurations",
    ... }
    >>> result = evaluate_with_llm_judge(
    ...     task_description="Generate VPC CDK stack",
    ...     agent_output=cdk_code,
    ...     criteria=criteria,
    ... )
    >>> print(result["overall_score"])
    """
    criteria = _validated_criteria(criteria)
    task_description = _bounded_text(task_description, MAX_TASK_DESCRIPTION_CHARS)
    agent_output = _bounded_text(agent_output, MAX_AGENT_OUTPUT_CHARS)
    reference_answer = _bounded_text(reference_answer, MAX_REFERENCE_ANSWER_CHARS) if reference_answer else None

    if verbose:
        print("  [LLM Judge] Evaluating agent output...")
        print(f"  [Criteria] {len(criteria)} evaluation criteria")
    
    # Mock mode: return synthetic evaluation
    if _MOCK:
        if verbose:
            print("  [LLM Judge] Running in MOCK mode")
        
        # Generate mock scores (70-95 range for realistic evaluation)
        random.seed(hash(agent_output) % 2**32)  # Deterministic based on output
        
        scores = {name: random.randint(70, 95) for name in criteria.keys()}
        overall_score = sum(scores.values()) // len(scores)
        
        return {
            "scores": scores,
            "overall_score": overall_score,
            "rationale": {name: f"Mock evaluation for {name}" for name in criteria.keys()},
            "strengths": ["Mock strength 1", "Mock strength 2"],
            "weaknesses": ["Mock weakness 1"],
            "recommendations": ["Mock recommendation 1", "Mock recommendation 2"],
        }
    
    # Real mode: call Claude Opus judge
    # Get judge model (Claude Opus, temperature=0.0)
    judge_model = get_judge_model(region=region)
    
    # Format criteria and reference answer
    criteria_text = "\n".join(
        f"- **{name}**: {description}"
        for name, description in criteria.items()
    )
    ref_text = reference_answer if reference_answer else "No reference answer provided."
    
    # Create chain with template variables
    prompt = ChatPromptTemplate.from_messages([
        ("user", JUDGE_PROMPT_TEMPLATE),
    ])
    chain = prompt | judge_model | StrOutputParser()
    
    # Invoke judge with variables
    response = chain.invoke({
        "task_description": task_description,
        "agent_output": agent_output,
        "reference_answer": ref_text,
        "criteria": criteria_text,
    })
    
    # Parse JSON response
    try:
        json_str = _extract_json_payload(response)
        parsed = json.loads(json_str)
        result = _normalize_judge_result(parsed, list(criteria.keys()))
        
        if verbose:
            print(f"  [Overall Score] {result.get('overall_score', 'N/A')}/100")
            print(f"  [Strengths] {len(result.get('strengths', []))}")
            print(f"  [Weaknesses] {len(result.get('weaknesses', []))}")
        
        return result
        
    except (json.JSONDecodeError, KeyError) as e:
        if verbose:
            print(f"  [Warning] Failed to parse judge response: {e}")
        
        # Return fallback result
        return {
            "scores": {name: 0 for name in criteria.keys()},
            "overall_score": 0,
            "rationale": {},
            "strengths": [],
            "weaknesses": ["Failed to parse evaluation response"],
            "recommendations": ["Retry evaluation"],
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Batch Evaluation
# ---------------------------------------------------------------------------

def evaluate_batch(
    evaluations: list[dict[str, Any]],
    region: str | None = None,
    verbose: bool = False,
) -> list[dict[str, Any]]:
    """
    Evaluate multiple agent outputs in batch.

    Parameters
    ----------
    evaluations : list of dict
        List of evaluation specs, each with:
        - task_description: str
        - agent_output: str
        - criteria: dict
        - reference_answer: str (optional)
    region : str, optional
        AWS region for Bedrock.
    verbose : bool
        Print progress for each evaluation.

    Returns
    -------
    list of dict
        Evaluation results for each output.

    Example
    -------
    >>> evaluations = [
    ...     {
    ...         "task_description": "Generate VPC stack",
    ...         "agent_output": vpc_code,
    ...         "criteria": vpc_criteria,
    ...     },
    ...     {
    ...         "task_description": "Generate RDS stack",
    ...         "agent_output": rds_code,
    ...         "criteria": rds_criteria,
    ...     },
    ... ]
    >>> results = evaluate_batch(evaluations)
    """
    results = []
    
    for i, eval_spec in enumerate(evaluations):
        if verbose:
            print(f"\n[Batch Evaluation {i+1}/{len(evaluations)}]")
        
        result = evaluate_with_llm_judge(
            task_description=eval_spec["task_description"],
            agent_output=eval_spec["agent_output"],
            criteria=eval_spec["criteria"],
            reference_answer=eval_spec.get("reference_answer"),
            region=region,
            verbose=verbose,
        )
        
        results.append({
            **eval_spec,
            "evaluation": result,
        })
    
    return results
