"""Compatibility facade for the JSON control-flow Temporal workflow.

Implementation has been split into graph helpers, workflow activities, and the
workflow class. Existing imports from maos_runtime.dag_workflow continue to work.
"""

from maos_runtime.graph.control_flow import normalize_graph, validate_graph
from maos_runtime.workflows.activities import (
    ACTIVITIES,
    archive_completed_workflow,
    dispatch_agent_node,
    poll_agent_node,
)
from maos_runtime.workflows.constants import (
    ACTIVITY_TIMEOUT_SECONDS,
    DEFAULT_MAX_NODE_VISITS,
    DEFAULT_MAX_TOTAL_VISITS,
    DEFAULT_NODE_TIMEOUT_SECONDS,
)
from maos_runtime.workflows.json_dag import JsonControlFlowWorkflow, JsonDagWorkflow

__all__ = [
    "ACTIVITIES",
    "ACTIVITY_TIMEOUT_SECONDS",
    "DEFAULT_MAX_NODE_VISITS",
    "DEFAULT_MAX_TOTAL_VISITS",
    "DEFAULT_NODE_TIMEOUT_SECONDS",
    "JsonControlFlowWorkflow",
    "JsonDagWorkflow",
    "archive_completed_workflow",
    "dispatch_agent_node",
    "normalize_graph",
    "poll_agent_node",
    "validate_graph",
]
