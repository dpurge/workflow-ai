"""Agent backends. Claude Code is the v1 implementation; others plug in later."""

from .base import AgentBackend, AgentInvocation, AgentOutputError, AgentResult

__all__ = ["AgentBackend", "AgentInvocation", "AgentOutputError", "AgentResult"]
