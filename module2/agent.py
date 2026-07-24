"""
module2/agent.py
================
Core Repository Analysis Agent for Module 2.

This module implements the LangChain-based agent that analyzes git repositories
to identify applications, technology stacks, and AWS infrastructure requirements.

FRAMEWORK COMPARISON
--------------------
Module 1 uses AWS Strands:
    from strands import Agent
    agent = Agent(model=model, tools=tools, system_prompt=prompt)

Module 2 uses LangChain + LangGraph:
    from langchain.agents import create_tool_calling_agent
    from langgraph.prebuilt import create_react_agent
    agent = create_react_agent(model, tools)

Both implement the same think-act-observe loop, but LangGraph provides
explicit state management and better observability through its graph structure.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from langgraph.prebuilt import create_react_agent
from langchain_core.runnables import Runnable

from module2.config.models import get_chat_bedrock_model
from module2.prompts.system_prompts import SYSTEM_PROMPT
from module2.tools.repo_tools import ALL_TOOLS


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _parse_allowed_roots() -> list[Path]:
    """Parse repository root allowlist for agent-level path validation."""
    raw = os.getenv("AGENT_ALLOWED_REPO_ROOTS", "")
    if not raw.strip():
        return [PROJECT_ROOT]

    roots: list[Path] = []
    for item in raw.split(os.pathsep):
        value = item.strip()
        if not value:
            continue
        path_obj = Path(value).expanduser().resolve()
        if path_obj.exists() and path_obj.is_dir():
            roots.append(path_obj)

    return roots if roots else [PROJECT_ROOT]


def _validate_repo_path(repo_path: str) -> Path:
    """Validate repository path for all agent invocation paths, not only HTTP."""
    if not repo_path or not repo_path.strip():
        raise ValueError("repo_path is required")

    path_obj = Path(repo_path).expanduser()
    if not path_obj.is_absolute():
        raise ValueError("repo_path must be an absolute path")

    resolved = path_obj.resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError("repo_path must point to an existing directory")

    if not (resolved / ".git").exists():
        raise ValueError("repo_path must point to a git repository")

    allowed_roots = _parse_allowed_roots()
    allowed = any(resolved == root or root in resolved.parents for root in allowed_roots)
    if not allowed:
        raise ValueError("repo_path is outside allowed roots")

    return resolved


# ---------------------------------------------------------------------------
# Agent Factory (LangGraph ReAct Agent)
# ---------------------------------------------------------------------------

def create_agent(
    *,
    verbose: bool = True,
    max_iterations: int = 15,
    region: str | None = None,
    streaming: bool = False,
) -> Runnable:
    """
    Create a Module 2 Repository Analysis Agent using LangGraph.

    This uses LangGraph's create_react_agent which provides a ReAct
    (Reasoning + Acting) loop with automatic tool calling.

    The agent uses:
    - ChatBedrock (LangChain) for model access
    - LangGraph ReAct agent pattern
    - Automatic think-act-observe loop
    - Five repository analysis tools

    Parameters
    ----------
    verbose : bool
        Print agent steps and tool calls. Default True for demos.
    max_iterations : int
        Maximum number of agent loop iterations. Default 15 (not used in current implementation).
    region : str, optional
        AWS region override. Falls back to AWS_REGION env var.
    streaming : bool
        Enable streaming responses from the model.

    Returns
    -------
    Runnable
        Configured LangGraph agent ready to analyze repositories.

    Example
    -------
    >>> from module2.agent import create_agent
    >>> agent = create_agent()
    >>> result = agent.invoke({"messages": [("user", "Analyze repository at /path/to/repo")]})
    >>> print(result["messages"][-1].content)
    """
    if max_iterations < 1:
        raise ValueError("max_iterations must be >= 1")

    aws_region = region or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"

    # ── REASONING LAYER ──────────────────────────────────────────────────────
    model = get_chat_bedrock_model(region=aws_region, streaming=streaming)
    
    if verbose:
        print(f"  [Module 2 Agent] Using LangGraph ReAct Agent")
        print(f"  [Model] Claude Sonnet 4 via Amazon Bedrock")
        print(f"  [Region] {aws_region}")
        print(f"  [Tools] {len(ALL_TOOLS)} repository analysis tools")
        print()

    # ── AGENT CONSTRUCTION ───────────────────────────────────────────────────
    # LangGraph's create_react_agent provides a simple ReAct loop
    # It handles the think-act-observe pattern automatically
    agent = create_react_agent(
        model,
        ALL_TOOLS,
        prompt=SYSTEM_PROMPT,
    )

    # ReAct agents can loop; bound recursion to constrain runaway tool calls.
    return agent.with_config({"recursion_limit": max_iterations * 3})


# ---------------------------------------------------------------------------
# Graph-Based Agent Factory (LangGraph approach)
# ---------------------------------------------------------------------------

def create_graph_agent(
    *,
    verbose: bool = True,
    region: str | None = None,
) -> Runnable:
    """
    Create a Module 2 Repository Analysis Agent using LangGraph state machine.

    This approach uses the explicit workflow defined in analysis_graph.py
    with five stages: scan → detect → analyze → map → synthesize.

    The LangGraph approach provides:
    - Better observability (see each stage transition)
    - Conditional branching (skip stages if needed)
    - Parallel execution (analyze multiple apps simultaneously)
    - State persistence (save/resume analysis)

    Parameters
    ----------
    verbose : bool
        Print workflow stage transitions. Default True.
    region : str, optional
        AWS region override.

    Returns
    -------
    Runnable
        Compiled LangGraph workflow ready to invoke.

    Example
    -------
    >>> from module2.agent import create_graph_agent
    >>> agent = create_graph_agent()
    >>> result = agent.invoke({
    ...     "repo_path": "/path/to/repo",
    ...     "messages": [],
    ...     "current_stage": "init",
    ... })
    >>> print(result["analysis_report"])
    """
    from langgraph.prebuilt import create_react_agent
    
    from module2.config.models import get_chat_bedrock_model
    from module2.tools.repo_tools import ALL_TOOLS
    
    aws_region = region or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    
    # Get model
    model = get_chat_bedrock_model(region=aws_region)
    
    if verbose:
        print(f"  [Module 2 Graph Agent] Using LangGraph state machine")
        print(f"  [Model] ChatBedrock - Claude Sonnet 4")
        print(f"  [Workflow] scan → detect → analyze → map → synthesize")
        print()
    
    # Create ReAct agent with LangGraph
    # This provides the basic agent loop that we'll enhance with our workflow
    agent = create_react_agent(
        model,
        ALL_TOOLS,
        state_modifier=SYSTEM_PROMPT,
    )
    
    return agent


# ---------------------------------------------------------------------------
# Convenience Functions
# ---------------------------------------------------------------------------

def analyze_repository(repo_path: str, verbose: bool = True) -> dict[str, Any]:
    """
    Analyze a repository and return structured results.

    This is a convenience function that creates an agent, runs the analysis,
    and returns the results in a structured format.

    Parameters
    ----------
    repo_path : str
        Absolute path to the git repository to analyze.
    verbose : bool
        Print agent steps during analysis.

    Returns
    -------
    dict
        Analysis results with applications, stacks, and AWS requirements.

    Example
    -------
    >>> from module2.agent import analyze_repository
    >>> results = analyze_repository("/path/to/my-repo")
    >>> print(f"Found {len(results['applications'])} applications")
    """
    validated_repo = _validate_repo_path(repo_path)
    agent = create_agent(verbose=verbose)
    
    query = f"""Analyze the git repository at: {str(validated_repo)}

Please:
1. Scan the repository structure
2. Detect all applications/services
3. Analyze the technology stack for each application
4. Map dependencies to AWS infrastructure requirements
5. Provide a comprehensive analysis report

Return the results as structured JSON."""

    result = agent.invoke({"messages": [("user", query)]})
    
    # Extract the final message from LangGraph response
    messages = result.get("messages", [])
    final_output = messages[-1].content if messages else ""
    
    return {
        "repo_path": str(validated_repo),
        "output": final_output,
        "messages": messages,
    }
