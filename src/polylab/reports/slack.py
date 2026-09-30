"""Slack delivery: bot token chat.postMessage to SLACK_CHANNEL_ID, fallback incoming webhook.

Messages carry account aliases only. Everything posted passes through `scrub` so a wallet
address or token that slipped into a string never leaves the machine.
"""

from __future__ import annotations

import re
import sys

import requests

from polylab import settings

DASHBOARD_URL = "https://poly.zowoo.uk"
_SECRET_PATTERNS = (
    re.compile(r"0x[a-fA-F0-9]{40,}"),                  # wallet addresses / private keys
    re.compile(r"\b[a-fA-F0-9]{64}\b"),                  # bare private keys
    re.compile(r"xox[abposr]-[A-Za-z0-9-]+"),           # slack tokens
    re.compile(r"https://hooks\.slack\.com/\S+"),
    re.compile(r"sb_(?:secret|publishable)_[A-Za-z0-9_-]+"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+"),  # JWTs
    re.compile(r"sk-ant-[A-Za-z0-9_-]+"),
)


def scrub(text: str) -> str:
    for pat in _SECRET_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    return text


def _scrub_obj(obj):
    if isinstance(obj, str):
        return scrub(obj)
    if isinstance(obj, list):
        return [_scrub_obj(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _scrub_obj(v) for k, v in obj.items()}
    return obj


def post(text: str, blocks: list[dict] | None = None, env: dict | None = None, timeout: float = 10.0) -> bool:
    env = env if env is not None else settings.service_env()
    payload = {"text": scrub(text)[:3000]}
    if blocks:
        payload["blocks"] = _scrub_obj(blocks)[:50]
    token, channel = env.get("SLACK_BOT_TOKEN"), env.get("SLACK_CHANNEL_ID")
    if token and channel:
        try:
            r = requests.post("https://slack.com/api/chat.postMessage", timeout=timeout,
                              headers={"Authorization": f"Bearer {token}"},
                              json={"channel": channel, "unfurl_links": False, **payload})
            if r.ok and r.json().get("ok"):
                return True
            print(f"slack: chat.postMessage failed (http {r.status_code}, {r.json().get('error') if r.ok else ''})",
                  file=sys.stderr)
        except (requests.RequestException, ValueError) as exc:
            print(f"slack: chat.postMessage error {type(exc).__name__}", file=sys.stderr)
    hook = env.get("SLACK_WEBHOOK_URL")
    if hook:
        try:
            r = requests.post(hook, json=payload, timeout=timeout)
            if r.ok:
                return True
            print(f"slack: webhook failed (http {r.status_code})", file=sys.stderr)
        except requests.RequestException as exc:
            print(f"slack: webhook error {type(exc).__name__}", file=sys.stderr)
    return False


def post_text(text: str) -> bool:
    return post(text)


def _fmt(v, nd=2, sign=True) -> str:
    if v is None:
        return "–"
    return f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}"


def report_blocks(report: dict, url: str) -> tuple[str, list[dict]]:
    """Short Block Kit summary for a report dict produced by reports.build."""
    title = report["title"]
    tot = report.get("totals", {})
    lines = [f"*실현손익* 오늘 {_fmt(tot.get('today'))} · 7일 {_fmt(tot.get('d7'))} · 30일 {_fmt(tot.get('d30'))} · "
             f"누적 {_fmt(tot.get('all'))} USDC",
             f"*24h 거래* {tot.get('tx_24h', 0)}건 · 정산 {tot.get('settled_24h', 0)}건 "
             f"({_fmt(tot.get('settled_pnl_24h'))} USDC) · 보유 {tot.get('open_positions', 0)}개"]
    rows = []
    for v in report.get("variants", []):
        if v["mode"] == "off":
            continue
        pnl = v["live"]["pnl"] if v["mode"] == "live" else v["paper"]["pnl"]
        rows.append(f"`{v['id']}` {v['mode']} {v['stake_usdc']:g}$ ({v.get('account') or '-'}) "
                    f"오늘 {_fmt(pnl.get('today'))} / 누적 {_fmt(pnl.get('all'))}")
    alerts = [a["message"] for a in report.get("alerts", [])][:5]
    changes = [c["summary"] for c in report.get("changes", [])][:5]
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": title[:150]}},
              {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}}]
    if rows:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(rows)[:2900]}})
    if changes:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*변경*\n" + "\n".join(changes)}})
    if alerts:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*경고*\n" + "\n".join(alerts)}})
    ai = report.get("ai") or {}
    ai_line = "AI 회고 포함" if ai.get("ran") else f"AI 회고 생략({ai.get('reason') or '결정론 리포트만'})"
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"{ai_line} · <{url}|리포트 보기>"}]})
    text = f"{title} — 누적 {_fmt(tot.get('all'))} USDC. {url}"
    return text, blocks


def post_report(report: dict, url: str, dry_run: bool = False, poster=None) -> bool:
    text, blocks = report_blocks(report, url)
    if dry_run:
        print(scrub(text))
        return True
    return (poster or post)(text, blocks)
