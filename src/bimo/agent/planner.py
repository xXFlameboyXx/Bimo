"""Bounded Task Planner and Execution Lifecycle for Bimo.

Provides:
- Task, TaskStep, TaskPlan, TaskStatus, TaskStepStatus
- Finite plans with MAX_TASK_STEPS and MAX_REPLAN_ATTEMPTS
- Step dependency tracking, observation evidence, completed/failed step accounting
- Task states: PLANNING, READY, RUNNING, PAUSED, WAITING_CONFIRMATION, RECOVERING, COMPLETED, FAILED, CANCELLED
- Clean separation from RobotStateMachine (RobotState vs TaskState)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import logging
import time
from typing import Any, Callable, Dict, List, Optional
import uuid

logger = logging.getLogger(__name__)

MAX_TASK_STEPS = 20
MAX_REPLAN_ATTEMPTS = 3


class TaskStatus(str, Enum):
    """Lifecycle states of a goal-oriented Bimo Task."""
    PLANNING = "PLANNING"
    RESEARCHING = "RESEARCHING"
    DOWNLOADING = "DOWNLOADING"
    VERIFYING_PACKAGE = "VERIFYING_PACKAGE"
    INSTALLING = "INSTALLING"
    CONFIGURING = "CONFIGURING"
    LAUNCHING = "LAUNCHING"
    VERIFYING_RESULT = "VERIFYING_RESULT"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    RECOVERING = "RECOVERING"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskStage(str, Enum):
    """Execution stages of an autonomous computer operator task."""
    PLANNING = "PLANNING"
    RESEARCHING = "RESEARCHING"
    DOWNLOADING = "DOWNLOADING"
    VERIFYING_PACKAGE = "VERIFYING_PACKAGE"
    INSTALLING = "INSTALLING"
    CONFIGURING = "CONFIGURING"
    LAUNCHING = "LAUNCHING"
    VERIFYING_RESULT = "VERIFYING_RESULT"
    COMPLETED = "COMPLETED"


class TaskStepStatus(str, Enum):
    """Lifecycle states of an individual task step."""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


@dataclass
class TaskStep:
    """An individual bounded, verifiable step in a task plan."""
    step_id: str
    description: str
    tool_name: Optional[str] = None
    tool_arguments: Dict[str, Any] = field(default_factory=dict)
    status: TaskStepStatus = TaskStepStatus.PENDING
    dependencies: List[str] = field(default_factory=list)
    observation: Optional[str] = None
    result_data: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    verification_level: Optional[str] = None  # VERIFIED, FAILED, UNCERTAIN, NOT_APPLICABLE
    stage: Optional[str] = None
    preconditions: List[str] = field(default_factory=list)
    timeout: float = 300.0
    retry_limit: int = 2
    retry_count: int = 0
    verification_criteria: Optional[str] = None
    recovery_strategy: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "description": self.description,
            "tool_name": self.tool_name,
            "tool_arguments": dict(self.tool_arguments),
            "status": self.status.value,
            "dependencies": list(self.dependencies),
            "observation": self.observation,
            "result_data": self.result_data,
            "error_message": self.error_message,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "verification_level": self.verification_level,
            "stage": self.stage,
            "preconditions": list(self.preconditions),
            "timeout": self.timeout,
            "retry_limit": self.retry_limit,
            "retry_count": self.retry_count,
            "verification_criteria": self.verification_criteria,
            "recovery_strategy": self.recovery_strategy,
        }


@dataclass
class TaskPlan:
    """A bounded sequence of steps designed to fulfill a user goal."""
    plan_id: str
    goal: str
    steps: List[TaskStep] = field(default_factory=list)
    replan_count: int = 0
    max_steps: int = MAX_TASK_STEPS
    max_replans: int = MAX_REPLAN_ATTEMPTS

    def get_step(self, step_id: str) -> Optional[TaskStep]:
        for step in self.steps:
            if step.step_id == step_id:
                return step
        return None

    def get_next_runnable_step(self) -> Optional[TaskStep]:
        completed_ids = {s.step_id for s in self.steps if s.status == TaskStepStatus.COMPLETED}
        for step in self.steps:
            if step.status == TaskStepStatus.PENDING:
                # Check all dependencies are completed
                if all(dep in completed_ids for dep in step.dependencies):
                    return step
        return None

    def is_complete(self) -> bool:
        return bool(self.steps) and all(s.status == TaskStepStatus.COMPLETED for s in self.steps)

    def has_failure(self) -> bool:
        return any(s.status == TaskStepStatus.FAILED for s in self.steps)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "goal": self.goal,
            "steps": [s.to_dict() for s in self.steps],
            "replan_count": self.replan_count,
            "is_complete": self.is_complete(),
        }


@dataclass
class Task:
    """A goal-oriented user task managed by the Bimo assistant."""
    task_id: str
    goal: str
    status: TaskStatus = TaskStatus.PLANNING
    current_stage: Optional[str] = None
    plan: Optional[TaskPlan] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    error_message: Optional[str] = None
    evidence: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, goal: str, metadata: Optional[Dict[str, Any]] = None) -> Task:
        return cls(
            task_id=f"task-{uuid.uuid4().hex[:8]}",
            goal=goal,
            current_stage="PLANNING",
            metadata=metadata or {},
        )

    def set_stage(self, stage: str) -> None:
        """Explicitly update the operational stage of the task."""
        old_stage = self.current_stage
        self.current_stage = stage
        self.updated_at = time.time()
        logger.info("Task %s stage transition: %s -> %s", self.task_id, old_stage, stage)

    def set_status(self, status: TaskStatus, reason: Optional[str] = None) -> None:
        old_status = self.status
        self.status = status
        self.updated_at = time.time()
        # If status corresponds to a defined lifecycle stage, update current_stage
        if status in (
            TaskStatus.PLANNING,
            TaskStatus.RESEARCHING,
            TaskStatus.DOWNLOADING,
            TaskStatus.VERIFYING_PACKAGE,
            TaskStatus.INSTALLING,
            TaskStatus.CONFIGURING,
            TaskStatus.LAUNCHING,
            TaskStatus.VERIFYING_RESULT,
            TaskStatus.COMPLETED,
        ):
            self.current_stage = status.value

        if status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            self.completed_at = time.time()
        logger.info(
            "Task %s status transition: %s -> %s%s",
            self.task_id,
            old_status.value,
            status.value,
            f" (reason: {reason})" if reason else "",
        )

    def add_evidence(self, evidence_str: str) -> None:
        self.evidence.append(evidence_str)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "status": self.status.value,
            "current_stage": self.current_stage or self.status.value,
            "plan": self.plan.to_dict() if self.plan else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "error_message": self.error_message,
            "evidence": list(self.evidence),
            "metadata": dict(self.metadata),
        }


class TaskPlanner:
    """Creates, evaluates, replans, and tracks execution of bounded TaskPlans."""

    def __init__(self, max_steps: int = MAX_TASK_STEPS, max_replans: int = MAX_REPLAN_ATTEMPTS) -> None:
        self.max_steps = max_steps
        self.max_replans = max_replans

    def create_plan(self, goal: str, steps: List[TaskStep]) -> TaskPlan:
        """Create a bounded TaskPlan from a list of steps."""
        if len(steps) > self.max_steps:
            logger.warning("Proposed plan has %d steps, truncating to max %d", len(steps), self.max_steps)
            steps = steps[:self.max_steps]

        plan = TaskPlan(
            plan_id=f"plan-{uuid.uuid4().hex[:8]}",
            goal=goal,
            steps=steps,
            max_steps=self.max_steps,
            max_replans=self.max_replans,
        )
        return plan

    def replan(self, task: Task, failure_step: TaskStep, failure_reason: str) -> Optional[TaskPlan]:
        """Attempt to construct a bounded recovery plan when a step fails."""
        if not task.plan:
            return None
        if task.plan.replan_count >= self.max_replans:
            logger.warning(
                "Task %s reached max replan attempts (%d). Aborting replan.",
                task.task_id,
                self.max_replans,
            )
            return None

        # Build recovery step
        recovery_step_id = f"{failure_step.step_id}-rec"
        recovery_step = TaskStep(
            step_id=recovery_step_id,
            description=f"Recover from failure: {failure_reason}",
            dependencies=[s.step_id for s in task.plan.steps if s.status == TaskStepStatus.COMPLETED],
        )

        # Retry step
        retry_step_id = f"{failure_step.step_id}-retry"
        retry_step = TaskStep(
            step_id=retry_step_id,
            description=f"Retry: {failure_step.description}",
            tool_name=failure_step.tool_name,
            tool_arguments=dict(failure_step.tool_arguments),
            dependencies=[recovery_step_id],
        )

        # Retain completed steps, insert recovery and retry, append remaining pending steps
        new_steps: List[TaskStep] = [
            s for s in task.plan.steps if s.status == TaskStepStatus.COMPLETED
        ]
        new_steps.append(recovery_step)
        new_steps.append(retry_step)

        # Add subsequent pending steps with updated dependency
        subsequent = [
            s for s in task.plan.steps
            if s.status == TaskStepStatus.PENDING and s.step_id != failure_step.step_id
        ]
        for s in subsequent:
            # Re-link any dependency on the failed step to the retry step
            updated_deps = [retry_step_id if dep == failure_step.step_id else dep for dep in s.dependencies]
            s.dependencies = updated_deps
            new_steps.append(s)

        if len(new_steps) > self.max_steps:
            new_steps = new_steps[:self.max_steps]

        new_plan = TaskPlan(
            plan_id=f"plan-{uuid.uuid4().hex[:8]}",
            goal=task.goal,
            steps=new_steps,
            replan_count=task.plan.replan_count + 1,
            max_steps=self.max_steps,
            max_replans=self.max_replans,
        )
        task.plan = new_plan
        task.set_status(TaskStatus.RECOVERING, reason=f"Replanned after {failure_step.step_id} failure: {failure_reason}")
        return new_plan
