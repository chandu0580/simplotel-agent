"""Model routing: pick a model per task instead of sending everything to the most expensive one.

Only `GUEST_TURN` is used today (one call per guest message). The other tasks are where cheaper
models fit once they exist: classification and summarisation are short, well-bounded outputs.
Routes come from configuration, so changing a model is a config change validated by evals.
"""

from dataclasses import dataclass
from enum import StrEnum

from ..core.config import Settings


class ModelTask(StrEnum):
    GUEST_TURN = "guest_turn"  # understanding + tool choice + grounded answer
    INTENT_CLASSIFICATION = "intent_classification"
    CONVERSATION_SUMMARY = "conversation_summary"
    EVAL_JUDGE = "eval_judge"


@dataclass(frozen=True)
class ModelRoute:
    model: str
    effort: str | None
    max_tokens: int


class ModelRouter:
    def __init__(self, routes: dict[ModelTask, ModelRoute]):
        missing = set(ModelTask) - set(routes)
        if missing:
            raise ValueError(f"No model route for tasks: {sorted(missing)}")
        self._routes = routes

    @classmethod
    def from_settings(cls, settings: Settings) -> "ModelRouter":
        primary = ModelRoute(settings.model_primary, settings.effort, settings.llm_max_tokens)
        fast = ModelRoute(settings.model_fast, "low", 2048)
        return cls(
            {
                ModelTask.GUEST_TURN: primary,
                ModelTask.INTENT_CLASSIFICATION: fast,
                ModelTask.CONVERSATION_SUMMARY: fast,
                # Judges must be at least as capable as the model they grade.
                ModelTask.EVAL_JUDGE: ModelRoute(settings.model_primary, "high", 4096),
            }
        )

    def route(self, task: ModelTask) -> ModelRoute:
        return self._routes[task]

    def describe(self) -> dict[str, dict]:
        return {task.value: {"model": r.model, "effort": r.effort, "max_tokens": r.max_tokens} for task, r in self._routes.items()}
