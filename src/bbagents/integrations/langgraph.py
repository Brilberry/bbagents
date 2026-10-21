"""LangGraph pause-and-resume support for bbagents interventions.

Install with ``bbagents[langgraph]``. Graph state must retain the intervention
request ID before calling ``await_response``; the adapter then safely resolves that
same request when LangGraph resumes the node.
"""

from __future__ import annotations

from typing import Any, Mapping

from ..intervention import (
    ActionInterventionService,
    ActorKind,
    InterventionDecision,
    InterventionRecord,
    InterventionRequest,
    InterventionResponse,
)


class LangGraphIntervention:
    """Bridge a stored intervention request to LangGraph's ``interrupt`` API."""

    def __init__(self, service: ActionInterventionService) -> None:
        self.service = service

    def await_response(self, request_id: str) -> InterventionRecord:
        """Pause the graph and resolve this request when it is resumed.

        The resumed value must include ``actor_id``, ``actor_kind``, ``decision``,
        and ``request_version``. The host application authenticates the actor
        before constructing this response.
        """

        request = self.service.get(request_id).request
        interrupt = _load_interrupt()
        value = interrupt(request_payload(request))
        return self.service.respond(request_id, response_from_payload(value))


def request_payload(request: InterventionRequest) -> dict[str, Any]:
    """Return a JSON-safe request value suitable for a LangGraph interrupt."""

    return {
        "kind": "bbagents.intervention",
        "request": {
            "id": request.id,
            "intervention_kind": request.kind.value,
            "action_name": request.action_name,
            "resource": request.resource,
            "summary": request.summary,
            "action_parameters": dict(request.action_parameters),
            "context": dict(request.context),
            "authority": request.authority,
            "subject_ref": request.subject_ref,
            "expires_at": (
                request.expires_at.isoformat() if request.expires_at else None
            ),
            "created_at": request.created_at.isoformat(),
            "schema_version": request.schema_version,
            "action_fingerprint": request.action_fingerprint,
            "provenance": {
                "source_id": request.provenance.source_id,
                "trust": request.provenance.trust.name.lower(),
                "received_at": request.provenance.received_at.isoformat(),
                "metadata": dict(request.provenance.metadata),
            },
        },
    }


def response_from_payload(value: Mapping[str, Any]) -> InterventionResponse:
    """Convert an application-authenticated LangGraph resume value to a response."""

    try:
        return InterventionResponse(
            actor_id=str(value["actor_id"]),
            actor_kind=ActorKind(str(value["actor_kind"])),
            decision=InterventionDecision(str(value["decision"])),
            request_version=int(value["request_version"]),
            rationale=str(value.get("rationale", "")),
            payload=dict(value.get("payload", {})),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(
            "LangGraph intervention response requires actor_id, actor_kind, decision, "
            "and request_version"
        ) from error


def _load_interrupt() -> Any:
    try:
        from langgraph.types import interrupt
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "LangGraph support requires installing bbagents[langgraph]"
        ) from error
    return interrupt
