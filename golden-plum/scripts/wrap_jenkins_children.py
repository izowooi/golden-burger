"""Wrap existing Plum child stages without changing credentials or parameters.

Input/output may contain private Jenkins settings: never print or commit them.
The final build still fails when any child stage fails; later children run with
an explicit, audited new-entry block while keeping exits/reconciliation active.
"""
from __future__ import annotations
import re

MARKER = "# PLUM_CHILD_FAILURE_CONTAINMENT_V1"
RUNTIMES = {
    "polybot-king": ("plum-live-king-90-1m-v1", "plum-live-king-mlb-90-1m-v1", "plum-live-king-nfl-85-1m-v15"),
    "polybot-queen": ("plum-live-queen-95-1m-v1", "plum-live-queen-mlb-95-1m-v1", "plum-live-queen-nfl-90-1m-v15"),
}
PATTERN = re.compile(r"\bpolybot\s+(config|run|status)\s+--live\s+--job\s+([a-z0-9-]+)")


def wrap_children(script: str, job: str) -> str:
    if job not in RUNTIMES:
        raise ValueError("unsupported Plum Jenkins job")
    if MARKER in script:
        return script
    if script.count("set -euo pipefail") != 1:
        raise ValueError("unexpected shell safety header")
    allowed = RUNTIMES[job]
    stages = []
    output = []
    for line in script.splitlines():
        match = PATTERN.search(line)
        if not match or line.lstrip().startswith("#"):
            output.append(line)
            continue
        stage, runtime = match.groups()
        if runtime not in allowed:
            raise ValueError("unexpected child runtime")
        stages.append((runtime, stage))
        output.extend([
            "if " + line.strip() + "; then",
            "  :",
            "else",
            "  PLUM_CHILD_FAILURE=1",
            "  export POLYBOT_ACCOUNT_PRIOR_CHILD_FAILED=1",
            f'  echo "CHILD_STAGE_FAILED runtime={runtime} stage={stage}; later BUY blocked" >&2',
            "fi",
        ])
    if any(stages.count((runtime, "run")) != 1 for runtime in allowed):
        raise ValueError("each registered child must run exactly once")
    if len(stages) != 7:
        raise ValueError("unexpected config/run/status stage count")
    result = "\n".join(output) + "\n"
    result = result.replace("set -euo pipefail\n", "set -euo pipefail\n" + MARKER + "\nPLUM_CHILD_FAILURE=0\nexport POLYBOT_ACCOUNT_PRIOR_CHILD_FAILED=0\n", 1)
    return result + '\nexit "$PLUM_CHILD_FAILURE"\n'
