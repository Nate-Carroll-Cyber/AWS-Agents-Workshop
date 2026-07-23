"""
module1
=======
Module 1: AWS Infrastructure Agent using AWS Strands framework.

This module demonstrates the Module 1 framework approach with:
- AWS Strands for agent orchestration
- BedrockModel for Amazon Bedrock access
- Read-only AWS tools (ECS, EC2, RDS, Lambda)
- Human-in-the-loop pattern
"""

from module1.agent import create_agent

__all__ = ["create_agent"]
