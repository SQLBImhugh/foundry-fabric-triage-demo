"""Controller-enforced policy.

The single most important design decision in this repo: **limits live in the
controller, not in the prompt.** A prompt that says "only take one action" is
a request. A ledger that refuses to dispatch a second write is a guarantee.

Ported from the production platform's ``RecoveryPolicy`` / ``RecoveryAgent`` loop,
where the same split has been running in production against real Microsoft
Fabric deployments.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

# ---------------------------------------------------------------------------
# Action taxonomy
# ---------------------------------------------------------------------------

# Actions that mutate the system being triaged. These are what
# ``max_write_actions`` governs — the blast-radius budget.
REMEDIATION_ACTIONS: frozenset[str] = frozenset(
    {
        "refresh_powerbi_dataset",
    }
)

# Actions that write only to observability surfaces (a flag table, a Teams
# channel, the incident store). They are audited but deliberately NOT charged
# against the remediation budget — otherwise reporting a failure would consume
# the same budget as fixing it, and the agent could go silent at exactly the
# moment you most need it to speak.
REPORTING_ACTIONS: frozenset[str] = frozenset(
    {
        "write_data_quality_flag",
        "notify_teams",
        "report_resolution",
    }
)

# Read-only diagnostics. Unlimited except by ``max_tool_calls``.
DIAGNOSTIC_ACTIONS: frozenset[str] = frozenset(
    {
        "get_request_context",
        "get_dataset_refresh_history",
        "check_duplicates",
        "get_known_incidents",
        "consult_data_quality_agent",
    }
)

ViolationKind = Literal[
    "max_turns_exceeded",
    "budget_exceeded",
    "timed_out",
    "policy_blocked",
]


class PolicyViolation(Exception):
    """Raised by the ledger when the controller refuses to proceed.

    ``kind`` maps 1:1 onto a terminal outcome so every refusal is recorded as
    a distinct, queryable incident rather than a generic failure.
    """

    def __init__(self, kind: ViolationKind, message: str):
        super().__init__(message)
        self.kind: ViolationKind = kind
        self.message = message


@dataclass(frozen=True)
class TriagePolicy:
    """Hard limits enforced by the controller loop.

    ``max_llm_turns`` covers **every agent in the run**, not just the
    orchestrator — the ledger is shared with the Data Quality agent. The
    observed worst case today is 10 turns (8 orchestrator + 2 data quality),
    so 14 leaves deliberate headroom. A limit tuned so tightly that the normal
    path nearly trips it stops being a safety control and becomes a source of
    spurious failures that mask the real ones.
    """

    max_llm_turns: int = 14
    max_tool_calls: int = 20
    max_write_actions: int = 1
    max_tokens: int = 80_000
    wall_clock_timeout_seconds: int = 300
    allowed_actions: frozenset[str] = field(
        default=REMEDIATION_ACTIONS | REPORTING_ACTIONS | DIAGNOSTIC_ACTIONS
    )

    @classmethod
    def from_settings(cls, settings) -> TriagePolicy:  # noqa: ANN001 - duck-typed
        """Rebuild from live settings so env changes actually take effect.

        the production platform shipped a module-level constant here for months and
        silently ignored the two env vars operators were tuning. Don't repeat
        that: read settings at construction time.
        """
        return cls(
            max_llm_turns=int(settings.triage_max_llm_turns),
            max_tool_calls=int(settings.triage_max_tool_calls),
            max_write_actions=int(settings.triage_max_write_actions),
            max_tokens=int(settings.triage_max_tokens),
            wall_clock_timeout_seconds=int(settings.triage_timeout_seconds),
        )


class PolicyLedger:
    """Mutable consumption tracker for one triage run.

    Call the ``charge_*`` methods *before* doing the thing they describe. Each
    raises :class:`PolicyViolation` rather than returning a boolean, so a
    forgotten check is a crash in tests, not a silent budget overrun in prod.
    """

    def __init__(self, policy: TriagePolicy, *, clock=time.monotonic):
        self.policy = policy
        self._clock = clock
        self._started_at = clock()
        self.llm_turns = 0
        self.tool_calls = 0
        self.attempted_actions = 0
        self.write_actions = 0
        self.tokens_used = 0
        self.blocked_attempts: list[str] = []

    # --- introspection -----------------------------------------------------

    @property
    def elapsed_seconds(self) -> float:
        return self._clock() - self._started_at

    @property
    def elapsed_ms(self) -> int:
        return int(self.elapsed_seconds * 1000)

    def snapshot(self) -> dict:
        return {
            "llm_turns": self.llm_turns,
            "tool_calls": self.tool_calls,
            "attempted_actions": self.attempted_actions,
            "write_actions": self.write_actions,
            "tokens_used": self.tokens_used,
            "elapsed_ms": self.elapsed_ms,
            "blocked_attempts": list(self.blocked_attempts),
        }

    # --- charges -----------------------------------------------------------

    def check_deadline(self) -> None:
        if self.elapsed_seconds > self.policy.wall_clock_timeout_seconds:
            raise PolicyViolation(
                "timed_out",
                f"Wall-clock timeout of {self.policy.wall_clock_timeout_seconds}s exceeded "
                f"after {self.elapsed_seconds:.1f}s",
            )

    def charge_llm_turn(self) -> None:
        self.check_deadline()
        if self.llm_turns >= self.policy.max_llm_turns:
            raise PolicyViolation(
                "max_turns_exceeded",
                f"Reached max_llm_turns={self.policy.max_llm_turns} without a resolution",
            )
        self.llm_turns += 1

    def charge_tokens(self, count: int) -> None:
        self.tokens_used += max(0, int(count))
        if self.tokens_used > self.policy.max_tokens:
            raise PolicyViolation(
                "budget_exceeded",
                f"Token budget {self.policy.max_tokens} exceeded ({self.tokens_used} used)",
            )

    def assert_action_allowed(self, action: str) -> None:
        """Reject anything not on the allowlist — including hallucinated names."""
        if action not in self.policy.allowed_actions:
            self.blocked_attempts.append(action)
            raise PolicyViolation(
                "policy_blocked",
                f"Action '{action}' is not on the allowlist. "
                f"Allowed: {sorted(self.policy.allowed_actions)}",
            )

    def charge_tool_call(self, action: str) -> None:
        # Attempts are counted before any check, so "the agent asked for 8
        # things and 7 were dispatched" is answerable. Counting only what was
        # dispatched hides the refusals, which are the interesting part.
        self.attempted_actions += 1
        self.check_deadline()
        self.assert_action_allowed(action)

        if self.tool_calls >= self.policy.max_tool_calls:
            raise PolicyViolation(
                "budget_exceeded",
                f"Reached max_tool_calls={self.policy.max_tool_calls}",
            )
        self.tool_calls += 1

        if action in REMEDIATION_ACTIONS:
            if self.write_actions >= self.policy.max_write_actions:
                self.blocked_attempts.append(action)
                raise PolicyViolation(
                    "policy_blocked",
                    f"Refusing '{action}': already performed "
                    f"{self.write_actions} remediation write(s), "
                    f"max_write_actions={self.policy.max_write_actions}",
                )
            self.write_actions += 1


def is_remediation(action: str) -> bool:
    return action in REMEDIATION_ACTIONS
