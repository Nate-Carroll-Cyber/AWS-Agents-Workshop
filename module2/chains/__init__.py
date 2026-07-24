"""
module2/chains
==============
LangChain chains for repository analysis.

Chains are composable sequences of operations - the fundamental building block
of LangChain applications.
"""

from module2.chains.analysis_chain import (
    create_multi_step_analysis_chain,
    create_parallel_analysis_chain,
    create_simple_analysis_chain,
)

__all__ = [
    "create_simple_analysis_chain",
    "create_multi_step_analysis_chain",
    "create_parallel_analysis_chain",
]
