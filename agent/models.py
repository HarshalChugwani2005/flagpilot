"""Pydantic v2 data models shared by the planner, guardrails, executor and reporter.

Fields are defined in the step that implements each model.
"""

from pydantic import BaseModel


class Variant(BaseModel):
    """One arm of an experiment (control or treatment) and its traffic weight."""


class ExperimentPlan(BaseModel):
    """The LLM's structured plan: hypothesis, ``fp_`` flag name, variants and metric."""


class ApprovalRecord(BaseModel):
    """A human approval of a specific plan or rollout, required before any Flagsmith write."""


class ExecutionResult(BaseModel):
    """What the executor changed in Flagsmith, and through which path (MCP or Admin API)."""


class ExperimentResult(BaseModel):
    """Per-variant counts and the statistics computed in code (never by the LLM)."""


class AuditEntry(BaseModel):
    """One line of ``audit_log.jsonl`` describing a Flagsmith write and its approval."""
