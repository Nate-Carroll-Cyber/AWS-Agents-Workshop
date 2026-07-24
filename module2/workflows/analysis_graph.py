"""
module2/workflows/analysis_graph.py
====================================
LangGraph state machine for repository analysis workflow.

This module implements the Module 2 repository analysis workflow as a
LangGraph state machine with five nodes:
1. scan_node - Initial repository scan
2. detect_apps_node - Identify applications
3. analyze_stack_node - Analyze each app's stack
4. map_infrastructure_node - Map to AWS services
5. synthesize_node - Generate final report

The state machine provides better observability and control compared to
a simple AgentExecutor loop.
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages


# ---------------------------------------------------------------------------
# State Schema
# ---------------------------------------------------------------------------

class AnalysisState(TypedDict):
    """
    State schema for the repository analysis workflow.
    
    This state is passed between nodes and accumulates analysis results
    as the workflow progresses.
    """
    # Input
    repo_path: str
    
    # Conversation history
    messages: Annotated[list[BaseMessage], add_messages]
    
    # Analysis results (accumulated through workflow)
    file_tree: dict | None
    applications: list[dict] | None
    dependencies: dict | None
    aws_services: dict | None
    analysis_report: str | None
    
    # Workflow control
    current_stage: str
    error: str | None


# ---------------------------------------------------------------------------
# Node Functions
# ---------------------------------------------------------------------------

def scan_node(state: AnalysisState) -> AnalysisState:
    """
    SCAN stage: Scan repository structure.
    
    Uses scan_repository_structure tool to get file tree and identify
    dependency files, config files, and directory structure.
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    from module2.prompts import SCAN_PROMPT
    
    # This node would invoke the agent with SCAN_PROMPT
    # For now, we'll structure it to be called by the agent
    state["current_stage"] = "scan"
    state["messages"].append(SystemMessage(content=SCAN_PROMPT))
    state["messages"].append(HumanMessage(content=f"Scan repository at: {state['repo_path']}"))
    
    return state


def detect_apps_node(state: AnalysisState) -> AnalysisState:
    """
    DETECT stage: Identify distinct applications.
    
    Uses detect_applications tool to find separate apps/services
    based on dependency files and directory structure.
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    from module2.prompts import DETECTION_PROMPT
    
    state["current_stage"] = "detect"
    state["messages"].append(SystemMessage(content=DETECTION_PROMPT))
    state["messages"].append(HumanMessage(content="Detect all applications in the repository."))
    
    return state


def analyze_stack_node(state: AnalysisState) -> AnalysisState:
    """
    ANALYZE stage: Analyze technology stack for each application.
    
    Reads dependency files and uses analyze_dependencies tool to
    extract language, framework, and dependencies.
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    from module2.prompts import ANALYSIS_PROMPT
    
    state["current_stage"] = "analyze"
    state["messages"].append(SystemMessage(content=ANALYSIS_PROMPT))
    state["messages"].append(HumanMessage(content="Analyze the technology stack for each detected application."))
    
    return state


def map_infrastructure_node(state: AnalysisState) -> AnalysisState:
    """
    MAP stage: Map dependencies to AWS services.
    
    Uses map_aws_services tool to identify required AWS infrastructure
    based on application dependencies.
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    from module2.prompts import MAPPING_PROMPT
    
    state["current_stage"] = "map"
    state["messages"].append(SystemMessage(content=MAPPING_PROMPT))
    state["messages"].append(HumanMessage(content="Map application dependencies to AWS infrastructure services."))
    
    return state


def synthesize_node(state: AnalysisState) -> AnalysisState:
    """
    SYNTHESIS stage: Generate final analysis report.
    
    Combines all findings into a comprehensive structured report.
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    from module2.prompts import SYNTHESIS_PROMPT
    
    state["current_stage"] = "synthesize"
    state["messages"].append(SystemMessage(content=SYNTHESIS_PROMPT))
    state["messages"].append(HumanMessage(content="Create a comprehensive analysis report with all findings."))
    
    return state


# ---------------------------------------------------------------------------
# Conditional Edges
# ---------------------------------------------------------------------------

def should_continue_to_detect(state: AnalysisState) -> str:
    """Decide whether to proceed to detect stage or end due to error."""
    if state.get("error"):
        return END
    if state.get("file_tree"):
        return "detect"
    return "detect"  # Proceed anyway, agent will handle


def should_continue_to_analyze(state: AnalysisState) -> str:
    """Decide whether to proceed to analyze stage."""
    if state.get("error"):
        return END
    if state.get("applications"):
        return "analyze"
    return "analyze"  # Proceed anyway


def should_continue_to_map(state: AnalysisState) -> str:
    """Decide whether to proceed to map stage."""
    if state.get("error"):
        return END
    return "map"


def should_continue_to_synthesize(state: AnalysisState) -> str:
    """Decide whether to proceed to synthesize stage."""
    if state.get("error"):
        return END
    return "synthesize"


# ---------------------------------------------------------------------------
# Graph Construction
# ---------------------------------------------------------------------------

def create_analysis_graph() -> StateGraph:
    """
    Create the LangGraph state machine for repository analysis.
    
    The graph follows this flow:
    scan → detect → analyze → map → synthesize → END
    
    Each stage can terminate early if an error occurs.
    
    Returns
    -------
    StateGraph
        Compiled LangGraph state machine ready for execution.
    """
    # Create the graph
    workflow = StateGraph(AnalysisState)
    
    # Add nodes
    workflow.add_node("scan", scan_node)
    workflow.add_node("detect", detect_apps_node)
    workflow.add_node("analyze", analyze_stack_node)
    workflow.add_node("map", map_infrastructure_node)
    workflow.add_node("synthesize", synthesize_node)
    
    # Set entry point
    workflow.set_entry_point("scan")
    
    # Add edges
    workflow.add_conditional_edges(
        "scan",
        should_continue_to_detect,
        {
            "detect": "detect",
            END: END,
        }
    )
    
    workflow.add_conditional_edges(
        "detect",
        should_continue_to_analyze,
        {
            "analyze": "analyze",
            END: END,
        }
    )
    
    workflow.add_conditional_edges(
        "analyze",
        should_continue_to_map,
        {
            "map": "map",
            END: END,
        }
    )
    
    workflow.add_conditional_edges(
        "map",
        should_continue_to_synthesize,
        {
            "synthesize": "synthesize",
            END: END,
        }
    )
    
    workflow.add_edge("synthesize", END)
    
    return workflow


def compile_analysis_graph() -> Any:
    """
    Compile the analysis graph for execution.
    
    Returns
    -------
    CompiledGraph
        Compiled graph ready to invoke with initial state.
    """
    workflow = create_analysis_graph()
    return workflow.compile()
