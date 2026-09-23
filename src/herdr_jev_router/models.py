"""Immutable values shared by the pure routing policy."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from types import MappingProxyType


class Harness(StrEnum):
    """Identify a supported interactive agent harness."""

    CLAUDE = "claude"
    CODEX = "codex"
    OPENCODE = "opencode"
    PI = "pi"


class ClaudeModel(StrEnum):
    """Identify a supported Claude Code model."""

    HAIKU = "haiku"
    SONNET = "sonnet"
    OPUS = "opus"


class CodexModel(StrEnum):
    """Identify a supported Codex model."""

    LUNA = "luna"
    TERRA = "terra"
    SOL = "sol"


class OpenCodeModel(StrEnum):
    """Identify a supported OpenCode model tier."""

    DEEPSEEK = "deepseek"
    GLM = "glm"
    KIMI = "kimi"


class PiModel(StrEnum):
    """Identify a supported Pi model tier."""

    DEEPSEEK = "deepseek"
    GLM = "glm"
    KIMI = "kimi"


class Effort(StrEnum):
    """Identify a reasoning effort accepted by the routing contract."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class CapacityState(StrEnum):
    """Summarize provider capacity without exposing quota arithmetic."""

    SURPLUS = "surplus"
    ON_PACE = "on_pace"
    CONSERVE = "conserve"
    UNKNOWN = "unknown"
    CRITICAL = "critical"
    EXHAUSTED = "exhausted"


Model = ClaudeModel | CodexModel | OpenCodeModel | PiModel

TIER_NAMES = tuple(model.value for model in OpenCodeModel)


@dataclass(frozen=True, slots=True)
class HarnessLaunch:
    """Hold one opt-in harness provider and its tier-to-model mapping."""

    provider: str
    models: Mapping[str, str]

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or not self.provider:
            raise TypeError("provider must be a non-empty string")
        if not isinstance(self.models, Mapping) or set(self.models) != set(TIER_NAMES):
            raise ValueError("models must name every launch tier")
        if any(
            not isinstance(model, str) or not model for model in self.models.values()
        ):
            raise TypeError("every launch model must be a non-empty string")
        object.__setattr__(self, "models", MappingProxyType(dict(self.models)))


@dataclass(frozen=True, slots=True)
class QuotaDetail:
    """Hold the 5-hour and weekly quota numbers sent to Jev for one provider.

    Every field is `None` when that window is unknown, stale, absent, or not
    about 5 hours or 7 days long. A number is never guessed from another
    window. `*_resets_in_hours` is relative to the routing clock, so Jev never
    receives a raw timestamp.
    """

    five_hour_remaining_percent: float | None = None
    five_hour_resets_in_hours: float | None = None
    weekly_remaining_percent: float | None = None
    weekly_resets_in_hours: float | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("five_hour_remaining_percent", self.five_hour_remaining_percent),
            ("weekly_remaining_percent", self.weekly_remaining_percent),
        ):
            if value is not None and not 0 <= _number(value, name) <= 100:
                raise ValueError(f"{name} must be between zero and 100")
        for name, value in (
            ("five_hour_resets_in_hours", self.five_hour_resets_in_hours),
            ("weekly_resets_in_hours", self.weekly_resets_in_hours),
        ):
            if value is not None and _number(value, name) < 0:
                raise ValueError(f"{name} must not be negative")
        for remaining, resets in (
            (self.five_hour_remaining_percent, self.five_hour_resets_in_hours),
            (self.weekly_remaining_percent, self.weekly_resets_in_hours),
        ):
            if (remaining is None) != (resets is None):
                raise ValueError("a known window must carry percent and resets")

    def to_dict(self) -> dict[str, float | None]:
        """Serialize the four quota numbers for Jev state and the audit."""

        return {
            "five_hour_remaining_percent": self.five_hour_remaining_percent,
            "five_hour_resets_in_hours": self.five_hour_resets_in_hours,
            "weekly_remaining_percent": self.weekly_remaining_percent,
            "weekly_resets_in_hours": self.weekly_resets_in_hours,
        }


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be numeric")
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


@dataclass(frozen=True, slots=True)
class ProviderCapacity:
    """Represent one provider's routing state, penalty, and quota numbers."""

    harness: Harness
    state: CapacityState
    penalty: int = 0
    quota: QuotaDetail = QuotaDetail()
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.harness, Harness):
            raise TypeError("harness must be a Harness")
        if not isinstance(self.state, CapacityState):
            raise TypeError("state must be a CapacityState")
        if type(self.penalty) is not int:
            raise TypeError("penalty must be an integer")
        if not isinstance(self.quota, QuotaDetail):
            raise TypeError("quota must be a QuotaDetail")
        if self.state is CapacityState.UNKNOWN and self.penalty <= 0:
            raise ValueError("unknown capacity requires a penalty")
        if self.state is CapacityState.CRITICAL and self.penalty < 0:
            raise ValueError("critical capacity penalty must not be negative")
        if (
            self.state not in {CapacityState.UNKNOWN, CapacityState.CRITICAL}
            and self.penalty != 0
        ):
            raise ValueError("only unknown or critical capacity may have a penalty")
        if self.reason is not None and (
            not isinstance(self.reason, str) or not self.reason
        ):
            raise TypeError("reason must be a non-empty string or None")
        if self.reason is not None and self.state is not CapacityState.CRITICAL:
            raise ValueError("reason is only for critical capacity")


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """Hold validated branch answers and the selected harness."""

    harness: Harness
    claude_model: ClaudeModel
    codex_model: CodexModel
    opencode_model: OpenCodeModel
    pi_model: PiModel
    effort: Effort

    def __post_init__(self) -> None:
        if not isinstance(self.harness, Harness):
            raise TypeError("harness must be a Harness")
        if not isinstance(self.claude_model, ClaudeModel):
            raise TypeError("claude_model must be a ClaudeModel")
        if not isinstance(self.codex_model, CodexModel):
            raise TypeError("codex_model must be a CodexModel")
        if not isinstance(self.opencode_model, OpenCodeModel):
            raise TypeError("opencode_model must be an OpenCodeModel")
        if not isinstance(self.pi_model, PiModel):
            raise TypeError("pi_model must be a PiModel")
        if not isinstance(self.effort, Effort):
            raise TypeError("effort must be an Effort")

    @property
    def model(self) -> Model:
        """Return only the model belonging to the selected harness."""

        match self.harness:
            case Harness.CLAUDE:
                return self.claude_model
            case Harness.CODEX:
                return self.codex_model
            case Harness.OPENCODE:
                return self.opencode_model
            case Harness.PI:
                return self.pi_model


@dataclass(frozen=True, slots=True)
class LaunchProfile:
    """Hold the allowlisted Herdr kind and launch arguments."""

    kind: Harness
    args: tuple[str, ...]
