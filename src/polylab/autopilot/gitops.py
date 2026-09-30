"""Git + test helpers for the retro. Commits are scoped to explicit pathspecs so unrelated
working-tree changes in the checkout are never swept in."""

from __future__ import annotations

import subprocess
from pathlib import Path

from polylab import settings

EXPECTED_REMOTE = "izowooi/golden-burger"
COMMIT_PATHS = ("strategies", "reports", "autopilot", "docs/research")
TRAILER = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"


def _git(args: list[str], cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)


def remote_ok(repo: Path = settings.REPO_ROOT) -> bool:
    r = _git(["remote", "get-url", "origin"], repo)
    return r.returncode == 0 and EXPECTED_REMOTE in r.stdout


def pull(repo: Path = settings.REPO_ROOT) -> tuple[bool, str]:
    r = _git(["pull", "--ff-only", "--quiet"], repo, timeout=180)
    return r.returncode == 0, r.stderr.strip()[-300:]


def run_tests(repo: Path = settings.REPO_ROOT, timeout: int = 1200) -> tuple[bool, str]:
    try:
        r = subprocess.run(["uv", "run", "--no-sync", "pytest", "-q"], cwd=repo, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{type(exc).__name__}"
    return r.returncode == 0, (r.stdout + r.stderr)[-600:]


def last_commit_ts(path: Path, repo: Path = settings.REPO_ROOT) -> int | None:
    r = _git(["log", "-1", "--format=%ct", "--", str(path)], repo)
    return int(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip().isdigit() else None


def secret_values() -> list[str]:
    """Exact private keys and funder addresses from ~/.polylab/accounts.env (lower-case, no 0x)."""
    env = settings.load_env_file(settings.SECRETS_DIR / "accounts.env")
    return sorted({v.lower().removeprefix("0x") for k, v in env.items()
                   if k.endswith(("__POLYMARKET_PRIVATE_KEY", "__POLYMARKET_FUNDER_ADDRESS")) and len(v) >= 40})


def staged_secret_hits(repo: Path, paths: tuple[str, ...] | list[str]) -> int:
    """Count account secrets in the staged diff. A regex cannot tell a wallet from a 64-hex condition id,
    so this compares exact values; the repo is public and published rows must never carry them."""
    values = secret_values()
    if not values:
        return 0
    diff = _git(["diff", "--cached", "--no-color", "--", *paths], repo, timeout=300).stdout.lower()
    return sum(1 for v in values if v in diff)


def commit_and_push(message: str, push: bool = True, repo: Path = settings.REPO_ROOT,
                    paths: tuple[str, ...] = COMMIT_PATHS) -> tuple[bool, str]:
    present = [p for p in paths if (repo / p).exists() or _git(["ls-files", "--", p], repo).stdout.strip()]
    if not present:
        return True, "nothing to commit"
    add = _git(["add", "-A", "--", *present], repo)
    if add.returncode != 0:
        return False, f"git add failed: {add.stderr[-200:]}"
    if _git(["diff", "--cached", "--quiet", "--", *present], repo).returncode == 0:
        return True, "nothing to commit"
    hits = staged_secret_hits(repo, present)
    if hits:
        _git(["reset", "--quiet", "--", *present], repo)
        return False, f"aborted: {hits} account secret value(s) found in staged diff"
    body = f"{message}\n\n{TRAILER}\n"
    c = _git(["commit", "-m", body, "--", *present], repo)
    if c.returncode != 0:
        return False, f"git commit failed: {c.stderr[-300:]}"
    if not push:
        return True, "committed (no push)"
    if not remote_ok(repo):
        return False, "origin is not the expected repository; not pushing"
    for _ in range(3):
        p = _git(["push", "--quiet"], repo, timeout=180)
        if p.returncode == 0:
            return True, "pushed"
        _git(["pull", "--rebase", "--quiet"], repo, timeout=180)
    return False, f"git push failed: {p.stderr[-300:]}"
