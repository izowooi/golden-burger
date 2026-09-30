"""AI engines for the retro, tried in order: `claude -p` → `codex exec` → deterministic only.

Both agents run with cwd = the retro context directory and may only write there:
- claude: file tools only, permission-mode dontAsk, writes allowed only under the cwd, reads under
  $HOME denied, no Bash / web. Auth: CLAUDE_CODE_OAUTH_TOKEN from ~/.polylab/claude_oauth_token
  (engine skipped when missing/empty).
- codex: `codex exec -s workspace-write` (writes confined to the context dir, network off),
  approval_policy=never, ChatGPT login from ~/.codex. Residual risk: the codex sandbox limits writes
  only and can read files anywhere, so a prompt injection could pull local secrets into the model
  context; published text is still scrubbed (slack.scrub) before it leaves the machine.
Outputs are untrusted: the retro validates proposal.json and scrubs text before publishing.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from polylab import settings

TOKEN_FILE = "claude_oauth_token"
FILE_TOOLS = "Read,Write,Edit,Glob,Grep"
# No shell/web, and no reads under $HOME (secrets live in ~/.polylab, ~/.codex, ~/.claude); the context
# dir sits on /Volumes/t7 so it stays readable.
ALLOW_TOOLS = "Read,Glob,Grep,Write(./**),Edit(./**)"
DENY_TOOLS = "Bash,WebFetch,WebSearch,NotebookEdit,Task,Read(~/**),Edit(~/**),Write(~/**)"
DEFAULT_TIMEOUT_S = 1200
CODEX_DISABLED_FEATURES = ("apps", "browser_use", "browser_use_external", "browser_use_full_cdp_access",
                           "computer_use", "image_generation", "in_app_browser", "multi_agent", "hooks")
RATE_LIMIT_MARKERS = ("rate limit", "rate_limit", "usage limit", "429", "overloaded", "quota")


@dataclass
class RunResult:
    ok: bool
    reason: str = ""
    returncode: int | None = None
    rate_limited: bool = False


class Engine(Protocol):
    name: str

    def available(self) -> tuple[bool, str]: ...

    def run(self, prompt: str, cwd: Path, timeout: int) -> RunResult: ...


def read_token(secrets_dir: Path | None = None) -> str | None:
    p = (secrets_dir or settings.SECRETS_DIR) / TOKEN_FILE
    try:
        token = p.read_text().strip()
    except OSError:
        return None
    return token or None


def _base_env(drop: tuple[str, ...] = ()) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env["PATH"] = f"/opt/homebrew/bin:{Path.home() / '.local/bin'}:{env.get('PATH', '')}"
    return env


def _run(cmd: list[str], prompt: str, cwd: Path, env: dict, timeout: int, log_name: str,
         redact: tuple[str, ...] = ()) -> RunResult:
    try:
        proc = subprocess.run(cmd, input=prompt, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return RunResult(False, f"{cmd[0]} not found")
    except subprocess.TimeoutExpired:
        return RunResult(False, f"timeout after {timeout}s")
    out = proc.stdout[-200_000:]
    err = proc.stderr[-2000:]
    for secret in redact:
        out, err = out.replace(secret, "[REDACTED]"), err.replace(secret, "[REDACTED]")
    (cwd / f"{log_name}.log").write_text(out + "\n--- stderr ---\n" + err)
    text = (out[-4000:] + err).lower()
    limited = any(m in text for m in RATE_LIMIT_MARKERS)
    if proc.returncode != 0:
        return RunResult(False, f"exit {proc.returncode}: {err[-300:]}", proc.returncode, limited)
    if log_name == "claude":
        try:  # --output-format json reports errors (e.g. usage limits) with exit 0
            data = json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
            if data.get("is_error"):
                return RunResult(False, f"claude error: {str(data.get('result'))[:300]}", 0, limited)
        except (json.JSONDecodeError, IndexError, AttributeError):
            pass
    return RunResult(True, "", 0, False)


class ClaudeEngine:
    name = "claude"

    def __init__(self, secrets_dir: Path | None = None, model: str | None = None):
        self.secrets_dir = secrets_dir
        self.model = model or os.environ.get("POLYLAB_RETRO_CLAUDE_MODEL")

    def available(self) -> tuple[bool, str]:
        if not read_token(self.secrets_dir):
            return False, "토큰 없음"
        if not shutil.which("claude", path=_base_env()["PATH"]):
            return False, "claude CLI 없음"
        return True, ""

    def command(self) -> list[str]:
        # dontAsk = anything not pre-approved is denied; writes are approved only under the cwd.
        # Verified on the Mac mini (claude 2.1.138): home reads and writes outside the cwd are refused.
        cmd = ["claude", "-p", "--output-format", "json", "--permission-mode", "dontAsk",
               "--tools", FILE_TOOLS, "--allowedTools", ALLOW_TOOLS,
               "--disallowedTools", DENY_TOOLS]
        return cmd + (["--model", self.model] if self.model else [])

    def run(self, prompt: str, cwd: Path, timeout: int = DEFAULT_TIMEOUT_S) -> RunResult:
        token = read_token(self.secrets_dir)
        if not token:
            return RunResult(False, "토큰 없음")
        env = _base_env(("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"))
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
        return _run(self.command(), prompt, cwd, env, timeout, "claude", redact=(token,))


class CodexEngine:
    name = "codex"

    def __init__(self, binary: str | None = None, model: str | None = None):
        self.binary = binary or os.environ.get("POLYLAB_CODEX_BIN") or str(Path.home() / ".local/bin/codex")
        self.model = model or os.environ.get("POLYLAB_RETRO_CODEX_MODEL")

    def available(self) -> tuple[bool, str]:
        if not (Path(self.binary).exists() or shutil.which(self.binary, path=_base_env()["PATH"])):
            return False, "codex CLI 없음"
        if not (Path.home() / ".codex").exists():
            return False, "codex 로그인 없음"
        return True, ""

    def command(self, cwd: Path) -> list[str]:
        # --ignore-user-config drops user MCP servers/hooks (auth still comes from ~/.codex); tool
        # features that reach outside the sandbox (browser, computer use, apps, web search) are off.
        cmd = [self.binary, "exec", "--skip-git-repo-check", "--ephemeral", "--color", "never",
               "--ignore-user-config", "-s", "workspace-write", "-c", 'approval_policy="never"',
               "-c", "sandbox_workspace_write.network_access=false", "-c", 'web_search="disabled"']
        for feature in CODEX_DISABLED_FEATURES:
            cmd += ["--disable", feature]
        cmd += ["-C", str(cwd), "-o", str(cwd / "codex_last_message.txt")]
        if self.model:
            cmd += ["-m", self.model]
        return cmd + ["-"]

    def run(self, prompt: str, cwd: Path, timeout: int = DEFAULT_TIMEOUT_S) -> RunResult:
        return _run(self.command(cwd), prompt, cwd, _base_env(("OPENAI_API_KEY",)), timeout, "codex")


def default_chain() -> list[Engine]:
    return [ClaudeEngine(), CodexEngine()]


EngineFactory = Callable[[], list[Engine]]
