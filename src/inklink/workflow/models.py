from __future__ import annotations

import hashlib
import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class NodeState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkflowNode(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", validate_assignment=True)

    node_id: str = Field(min_length=1)
    node_type: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    state: NodeState = NodeState.PENDING
    attempt: int = Field(default=0, ge=0)

    @field_validator("node_id", "node_type")
    @classmethod
    def validate_non_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value

    @field_validator("depends_on")
    @classmethod
    def validate_dependencies(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        for value in values:
            if not value.strip():
                raise ValueError("dependency must not be blank")
            if value in seen:
                raise ValueError(f"duplicate dependency: {value}")
            seen.add(value)
        return values

    def idempotency_key(
        self,
        *,
        input_version: str,
        profile: str,
        toolset_version: str,
        prompt_version: str,
        task_parameters_hash: str,
        approval_messages_hash: str,
        generation: int,
    ) -> str:
        return idempotency_key(
            IdempotencyInputs(
                node_type=self.node_type,
                input_version=input_version,
                profile=profile,
                toolset_version=toolset_version,
                prompt_version=prompt_version,
                task_parameters_hash=task_parameters_hash,
                approval_messages_hash=approval_messages_hash,
                generation=generation,
            )
        )


class IdempotencyInputs(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", validate_assignment=True)

    node_type: str = Field(min_length=1)
    input_version: str = Field(min_length=1)
    profile: str = Field(min_length=1)
    toolset_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    task_parameters_hash: str = Field(min_length=1)
    approval_messages_hash: str = Field(min_length=1)
    generation: int = Field(gt=0)

    @field_validator(
        "node_type",
        "input_version",
        "profile",
        "toolset_version",
        "prompt_version",
        "task_parameters_hash",
        "approval_messages_hash",
    )
    @classmethod
    def validate_non_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value


def idempotency_key(inputs: IdempotencyInputs) -> str:
    payload = inputs.model_dump(mode="json")
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
