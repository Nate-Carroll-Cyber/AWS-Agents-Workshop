"""
module2/chains/analysis_chain.py
=================================
LangChain chains for repository analysis.

Chains are composable sequences of operations that transform inputs into outputs.
They represent the core abstraction in LangChain for building multi-step workflows.

CONCEPT: Chains vs Agents
--------------------------
- Chains: Deterministic, predefined sequence of operations
- Agents: Dynamic, LLM decides which tools to use and when

This module demonstrates chains for structured repository analysis workflows.
"""

from __future__ import annotations

from typing import Any

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable, RunnablePassthrough

from module2.config.models import get_chat_bedrock_model
from module2.prompts.system_prompts import SYSTEM_PROMPT


def create_simple_analysis_chain(region: str | None = None) -> Runnable:
    """
    Create a simple chain for repository analysis.
    
    This chain demonstrates the basic LangChain pattern:
    prompt → model → output parser
    
    The chain is deterministic - it always follows the same sequence.
    
    Parameters
    ----------
    region : str, optional
        AWS region for Bedrock model
        
    Returns
    -------
    Runnable
        A chain that takes repository info and returns analysis
        
    Example
    -------
    >>> chain = create_simple_analysis_chain()
    >>> result = chain.invoke({
    ...     "repo_path": "/path/to/repo",
    ...     "file_list": "package.json, Dockerfile, src/index.js"
    ... })
    """
    # Step 1: Define the prompt template
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", """Analyze this repository based on the file list:

Repository: {repo_path}
Files found: {file_list}

Provide a brief analysis of:
1. What type of application this appears to be
2. What technology stack is likely used
3. What AWS services might be needed

Keep the response concise (3-4 sentences).""")
    ])
    
    # Step 2: Get the model
    model = get_chat_bedrock_model(region=region)
    
    # Step 3: Define output parser
    output_parser = StrOutputParser()
    
    # Step 4: Compose the chain using LCEL (LangChain Expression Language)
    # The | operator chains components together
    chain = prompt | model | output_parser
    
    return chain


def create_multi_step_analysis_chain(region: str | None = None) -> Runnable:
    """
    Create a multi-step chain with intermediate processing.
    
    This chain demonstrates:
    1. Input transformation
    2. LLM processing
    3. Output parsing
    4. Result formatting
    
    Each step transforms the data for the next step.
    
    Parameters
    ----------
    region : str, optional
        AWS region for Bedrock model
        
    Returns
    -------
    Runnable
        A chain that performs multi-step analysis
        
    Example
    -------
    >>> chain = create_multi_step_analysis_chain()
    >>> result = chain.invoke({
    ...     "repo_path": "/mock/repo",
    ...     "apps": ["api-service", "worker-service"],
    ...     "dependencies": {"api": ["express", "pg"], "worker": ["celery", "redis"]}
    ... })
    """
    # Step 1: Create a prompt for dependency analysis
    dependency_prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("human", """Analyze these application dependencies and map them to AWS services:

Repository: {repo_path}
Applications: {apps}
Dependencies: {dependencies}

For each dependency, identify the required AWS service.
Format as: dependency → AWS service (brief reason)""")
    ])
    
    # Step 2: Create a prompt for infrastructure summary
    summary_prompt = ChatPromptTemplate.from_messages([
        ("system", "You are an AWS infrastructure expert."),
        ("human", """Based on this dependency analysis:

{dependency_analysis}

Provide a concise infrastructure summary:
1. Required AWS services (list)
2. Estimated complexity (low/medium/high)
3. Key deployment considerations (1-2 sentences)""")
    ])
    
    model = get_chat_bedrock_model(region=region)
    output_parser = StrOutputParser()
    
    # Step 3: Create the first chain (dependency analysis)
    dependency_chain = dependency_prompt | model | output_parser
    
    # Step 4: Create the second chain (summary)
    # This chain takes the output of the first chain as input
    summary_chain = (
        {
            "dependency_analysis": dependency_chain,
        }
        | summary_prompt
        | model
        | output_parser
    )
    
    return summary_chain


def create_parallel_analysis_chain(region: str | None = None) -> Runnable:
    """
    Create a chain with parallel processing branches.
    
    This demonstrates LangChain's ability to run multiple operations
    in parallel and combine their results.
    
    The chain analyzes:
    - Technology stack (parallel branch 1)
    - Security considerations (parallel branch 2)
    - Scalability requirements (parallel branch 3)
    
    Then combines all analyses into a final report.
    
    Parameters
    ----------
    region : str, optional
        AWS region for Bedrock model
        
    Returns
    -------
    Runnable
        A chain that performs parallel analysis
    """
    model = get_chat_bedrock_model(region=region)
    output_parser = StrOutputParser()
    
    # Define three parallel analysis branches
    tech_stack_prompt = ChatPromptTemplate.from_template(
        "Analyze the technology stack for this repository: {repo_info}\n"
        "List the main languages, frameworks, and tools. Be concise (2-3 sentences)."
    )
    
    security_prompt = ChatPromptTemplate.from_template(
        "Identify security considerations for this repository: {repo_info}\n"
        "Focus on AWS security services needed. Be concise (2-3 sentences)."
    )
    
    scalability_prompt = ChatPromptTemplate.from_template(
        "Assess scalability requirements for this repository: {repo_info}\n"
        "Recommend AWS services for scaling. Be concise (2-3 sentences)."
    )
    
    # Create three parallel chains
    tech_chain = tech_stack_prompt | model | output_parser
    security_chain = security_prompt | model | output_parser
    scalability_chain = scalability_prompt | model | output_parser
    
    # Combine results
    combine_prompt = ChatPromptTemplate.from_template(
        """Synthesize these three analyses into a brief summary:

Technology Stack:
{tech_analysis}

Security:
{security_analysis}

Scalability:
{scalability_analysis}

Provide a 3-sentence executive summary."""
    )
    
    # Create the parallel chain using RunnablePassthrough
    parallel_chain = (
        {
            "tech_analysis": tech_chain,
            "security_analysis": security_chain,
            "scalability_analysis": scalability_chain,
        }
        | combine_prompt
        | model
        | output_parser
    )
    
    return parallel_chain


# Export all chain factories
__all__ = [
    "create_simple_analysis_chain",
    "create_multi_step_analysis_chain",
    "create_parallel_analysis_chain",
]
