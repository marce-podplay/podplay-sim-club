"""Validation boundary between generative actors and deterministic execution."""

from typing import Any, Dict, Iterable


class IllegalAction(ValueError):
    pass


class ActionValidator:
    def __init__(self, pod_id: str, allowed_actions: Iterable[str]):
        self.pod_id = pod_id
        self.allowed_actions = set(allowed_actions)

    def validate(self, request: Dict[str, Any]) -> None:
        action_type = request.get("type")
        if action_type not in self.allowed_actions:
            raise IllegalAction(f"action is not allowed: {action_type!r}")
        requested_pod = request.get("arguments", {}).get("podId")
        if requested_pod is not None and requested_pod != self.pod_id:
            raise IllegalAction(
                f"actor requested pod {requested_pod!r}; scenario owns {self.pod_id!r}"
            )
