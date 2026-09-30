"""Create/update the polylab-* Jenkins jobs from jenkins/jobs.yaml via the REST API.

Dry-run by default; `--apply` writes. `--disable-legacy` disables every other polybot-* job.
Jenkins is LAN-only with anonymous configure rights (owner's choice), so no token is needed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from xml.sax.saxutils import escape

import requests
import yaml

JENKINS = "http://192.168.50.23:8080"
RUNTIME = "/Volumes/t7/polylab/repo"

# macOS TCC blocks processes spawned by the launchd-started Jenkins from opening files on the external
# volume (open() hangs waiting for a consent prompt nobody sees). sshd has disk access, so every job
# hops through `ssh polylab-local` (a localhost key in ~/.ssh/config) and runs the script from stdin.
REMOTE_PRELUDE = f"""set -euo pipefail
export PATH=/opt/homebrew/bin:$HOME/.local/bin:$PATH
export PYTHONUNBUFFERED=1
if [ ! -d /Volumes/t7/polylab ]; then echo "FATAL: /Volumes/t7 not mounted" >&2; exit 2; fi
cd {RUNTIME}
"""

PULL = """if [ -z "$(git status --porcelain -- . ':!reports' ':!autopilot')" ]; then
  git pull -q --ff-only || echo "WARN: git pull failed"
  uv sync -q --frozen --extra dev
fi
"""


def shell_script(spec: dict) -> str:
    body = REMOTE_PRELUDE + (PULL if spec.get("pull") else "") + spec["command"] + "\n"
    return "#!/bin/zsh\nset -e\nssh polylab-local /bin/zsh -s <<'POLYLAB_EOF'\n" + body + "POLYLAB_EOF\n"


def job_xml(name: str, spec: dict, defaults: dict) -> str:
    script = shell_script(spec)
    keep = spec.get("keep_builds", defaults["keep_builds"])
    timeout = spec.get("timeout_minutes", defaults["timeout_minutes"])
    return f"""<?xml version='1.1' encoding='UTF-8'?>
<project>
  <description>{escape(spec.get('description', ''))} (managed by jenkins/sync_jobs.py)</description>
  <keepDependencies>false</keepDependencies>
  <properties>
    <jenkins.model.BuildDiscarderProperty>
      <strategy class="hudson.tasks.LogRotator">
        <daysToKeep>-1</daysToKeep><numToKeep>{keep}</numToKeep>
        <artifactDaysToKeep>-1</artifactDaysToKeep><artifactNumToKeep>-1</artifactNumToKeep>
      </strategy>
    </jenkins.model.BuildDiscarderProperty>
    <org.jenkinsci.plugins.workflow.job.properties.DisableConcurrentBuildsJobProperty/>
  </properties>
  <scm class="hudson.scm.NullSCM"/>
  <canRoam>true</canRoam>
  <disabled>false</disabled>
  <blockBuildWhenDownstreamBuilding>false</blockBuildWhenDownstreamBuilding>
  <blockBuildWhenUpstreamBuilding>false</blockBuildWhenUpstreamBuilding>
  <triggers>
    <hudson.triggers.TimerTrigger><spec>{escape(spec['cron'])}</spec></hudson.triggers.TimerTrigger>
  </triggers>
  <concurrentBuild>false</concurrentBuild>
  <builders>
    <hudson.tasks.Shell><command>{escape(script)}</command></hudson.tasks.Shell>
  </builders>
  <publishers/>
  <buildWrappers>
    <hudson.plugins.build__timeout.BuildTimeoutWrapper plugin="build-timeout">
      <strategy class="hudson.plugins.build_timeout.impl.AbsoluteTimeOutStrategy">
        <timeoutMinutes>{timeout}</timeoutMinutes>
      </strategy>
      <operationList><hudson.plugins.build__timeout.operations.AbortOperation/></operationList>
    </hudson.plugins.build__timeout.BuildTimeoutWrapper>
    <hudson.plugins.timestamper.TimestamperBuildWrapper plugin="timestamper"/>
  </buildWrappers>
</project>
"""


def session() -> requests.Session:
    s = requests.Session()
    crumb = s.get(f"{JENKINS}/crumbIssuer/api/json", timeout=10).json()
    s.headers[crumb["crumbRequestField"]] = crumb["crumb"]
    return s


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--disable-legacy", action="store_true")
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args(argv)
    cfg = yaml.safe_load((Path(__file__).parent / "jobs.yaml").read_text())
    s = session()
    existing = {j["name"] for j in s.get(f"{JENKINS}/api/json?tree=jobs[name]", timeout=10).json()["jobs"]}
    for name, spec in cfg["jobs"].items():
        if args.only and name not in args.only:
            continue
        xml = job_xml(name, spec, cfg["defaults"]).encode()
        action = "update" if name in existing else "create"
        print(f"{action:6s} {name}  cron={spec['cron']!r}")
        if not args.apply:
            continue
        headers = {"Content-Type": "application/xml; charset=utf-8"}
        if name in existing:
            r = s.post(f"{JENKINS}/job/{name}/config.xml", data=xml, headers=headers, timeout=20)
        else:
            r = s.post(f"{JENKINS}/createItem", params={"name": name}, data=xml, headers=headers, timeout=20)
        r.raise_for_status()
    if args.disable_legacy:
        for name in sorted(existing):
            if name.startswith("polybot-") or name in {"golden-pomegranate", "strategy-lifecycle-monitor", "disk-monitor"}:
                print(f"disable {name}")
                if args.apply:
                    s.post(f"{JENKINS}/job/{name}/disable", timeout=20).raise_for_status()
    return 0


if __name__ == "__main__":
    sys.exit(main())
