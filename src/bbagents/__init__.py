"""BrilBerry Agents: provider-neutral contracts for agent applications.

The root namespace intentionally contains only the most common execution path.
Import specialised APIs from their responsibility-specific modules, for example
``bbagents.intervention`` or ``bbagents.models``.
"""

from .core import LanguageModel, Observation, TraceSink
from .decision import (
    DecisionModel,
    DecisionOutput,
    DecisionRequest,
    DecisionResult,
    DecisionRuntime,
    DecisionSchema,
    DecisionUsage,
    PythonTypeSchema,
)
from .planning import (
    AgentState,
    DependencyCycleError,
    Plan,
    PlanExecutor,
    PlanStep,
    StepStatus,
)
from .provenance import Provenance, TrustLevel
from .runtime import (
    Action,
    ActionResult,
    ActionRuntime,
    ActionStatus,
    AuthorizationDecision,
    AuthorizationStatus,
)
from .tools import FunctionTool, Tool, ToolCall, ToolRegistry

__all__ = [
    "Action",
    "ActionResult",
    "ActionRuntime",
    "ActionStatus",
    "AgentState",
    "AuthorizationDecision",
    "AuthorizationStatus",
    "DecisionModel",
    "DecisionOutput",
    "DecisionRequest",
    "DecisionResult",
    "DecisionRuntime",
    "DecisionSchema",
    "DecisionUsage",
    "DependencyCycleError",
    "FunctionTool",
    "LanguageModel",
    "Observation",
    "Plan",
    "PlanExecutor",
    "PlanStep",
    "Provenance",
    "PythonTypeSchema",
    "StepStatus",
    "Tool",
    "ToolCall",
    "ToolRegistry",
    "TraceSink",
    "TrustLevel",
]

__version__ = "0.1.0"
