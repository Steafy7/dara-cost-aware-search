"""Post-seal ground-truth evaluation, isolated from the online harness."""

from ._core import EvaluationResult, evaluate_sealed_run

__all__ = ("EvaluationResult", "evaluate_sealed_run")
