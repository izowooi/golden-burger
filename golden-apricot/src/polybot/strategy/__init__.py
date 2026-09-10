"""In-play soccer match-result strategy public pure interfaces."""

from .filters import get_aligned_binary_outcomes, get_strict_binary_yes
from .signals import EntryDecision, evaluate_entry, evaluate_exit

__all__ = [
    "EntryDecision",
    "evaluate_entry",
    "evaluate_exit",
    "get_strict_binary_yes",
    "get_aligned_binary_outcomes",
]
