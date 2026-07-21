"""Deterministic no-BGMN validation of sealed online runs."""

from ._core import (
    CompletionEquivalence,
    ReplayReport,
    replay_run,
    verify_completion_equivalence,
)

__all__ = (
    "CompletionEquivalence",
    "ReplayReport",
    "replay_run",
    "verify_completion_equivalence",
)
