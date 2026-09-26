import os
from pathlib import Path
import subprocess
import pytest
from scripts.wrap_jenkins_children import wrap_children, RUNTIMES


@pytest.mark.parametrize("failed_index", [None, 0, 1, 2])
def test_failed_stage_never_skips_later_runtime_and_preserves_failed_build(tmp_path, failed_index):
    executable = tmp_path / "polybot"
    executable.write_text("#!/bin/bash\nprintf '%s %s %s\\n' \"$1\" \"$4\" \"${POLYBOT_ACCOUNT_PRIOR_CHILD_FAILED:-unset}\" >> \"$STAGES\"\nif [[ $1 == run && $4 == ${FAIL_RUNTIME:-none} ]]; then exit 7; fi\n")
    executable.chmod(0o755)
    job = "polybot-queen"
    names = RUNTIMES[job]
    commands = ["#!/bin/bash", "set -euo pipefail", "export POLYBOT_BUY_AMOUNT=5"]
    for i, runtime in enumerate(names):
        for stage in (["config", "run", "status"] if i < 2 else ["run"]):
            commands.append(f"{executable} {stage} --live --job {runtime}")
    script = "\n".join(commands) + "\n"
    wrapped = wrap_children(script, job)
    assert wrap_children(wrapped, job) == wrapped
    assert "export POLYBOT_BUY_AMOUNT=5" in wrapped
    env = {**os.environ, "STAGES": str(tmp_path / "stages"), "FAIL_RUNTIME": names[failed_index] if failed_index is not None else "none"}
    result = subprocess.run(["/bin/bash"], input=wrapped, text=True, env=env, capture_output=True)
    rows = [r.split() for r in (tmp_path / "stages").read_text().splitlines()]
    assert result.returncode == (1 if failed_index is not None else 0)
    assert [r[1] for r in rows if r[0] == "run"] == list(names)
    if failed_index is not None:
        later = [r for r in rows if names.index(r[1]) > failed_index]
        assert all(r[2] == "1" for r in later)
    else:
        assert all(r[2] == "0" for r in rows)


def test_rejects_unknown_runtime_without_mutating_input():
    with pytest.raises(ValueError):
        wrap_children("set -euo pipefail\npolybot run --live --job arbitrary\n", "polybot-king")
