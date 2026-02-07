"""Slack notification module for sending reports."""
import logging
import os
from typing import Dict, List, Optional
import requests
from datetime import datetime

logger = logging.getLogger(__name__)


class SlackNotifier:
    """Send formatted messages to Slack via Webhook.

    Supports rich formatting with:
    - Markdown-style text
    - Color-coded attachments
    - Structured fields
    """

    def __init__(self, webhook_url: Optional[str] = None):
        """Initialize Slack notifier.

        Args:
            webhook_url: Slack webhook URL (or use SLACK_WEBHOOK_URL env var)
        """
        self.webhook_url = webhook_url or os.getenv("SLACK_WEBHOOK_URL")
        if not self.webhook_url:
            logger.warning("SLACK_WEBHOOK_URL 환경변수가 설정되지 않았습니다")

    def send_message(
        self,
        text: str,
        attachments: Optional[List[Dict]] = None,
        blocks: Optional[List[Dict]] = None
    ) -> bool:
        """Send a message to Slack.

        Args:
            text: Plain text message (fallback)
            attachments: List of attachment dictionaries (legacy format)
            blocks: List of block dictionaries (modern format)

        Returns:
            True if message sent successfully
        """
        if not self.webhook_url:
            logger.error("Slack webhook URL이 설정되지 않아 메시지를 전송할 수 없습니다")
            return False

        payload = {"text": text}
        if attachments:
            payload["attachments"] = attachments
        if blocks:
            payload["blocks"] = blocks

        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10
            )
            response.raise_for_status()
            logger.info("Slack 메시지 전송 완료")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Slack 메시지 전송 실패: {e}")
            return False

    def send_portfolio_report(
        self,
        account_name: str,
        summary: Dict,
        color: str = "good"
    ) -> bool:
        """Send a formatted portfolio report to Slack.

        Args:
            account_name: Account identifier (e.g., "golden-apple")
            summary: Portfolio summary dictionary from DataAPIClient
            color: Attachment color ("good", "warning", "danger", or hex)

        Returns:
            True if sent successfully
        """
        pnl_7d = summary.get("pnl_7d", {})
        pnl_30d = summary.get("pnl_30d", {})
        positions = summary.get("positions", [])
        total_value = summary.get("total_value", 0)

        # Calculate color based on P&L
        total_pnl_7d = pnl_7d.get("total_pnl", 0)
        if total_pnl_7d > 0:
            color = "good"  # Green
        elif total_pnl_7d < -10:
            color = "danger"  # Red
        else:
            color = "warning"  # Yellow

        # Format timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Build attachment
        attachment = {
            "color": color,
            "title": f"📊 {account_name.upper()} Portfolio Report",
            "text": f"Daily portfolio status as of {timestamp}",
            "fields": [
                {
                    "title": "💰 Total Value",
                    "value": f"${total_value:.2f}",
                    "short": True
                },
                {
                    "title": "📈 Positions",
                    "value": f"{len(positions)} open",
                    "short": True
                },
                {
                    "title": "📅 7-Day P&L",
                    "value": (
                        f"${total_pnl_7d:+.2f}\n"
                        f"({pnl_7d.get('num_trades', 0)} trades)"
                    ),
                    "short": True
                },
                {
                    "title": "📆 30-Day P&L",
                    "value": (
                        f"${pnl_30d.get('total_pnl', 0):+.2f}\n"
                        f"({pnl_30d.get('num_trades', 0)} trades)"
                    ),
                    "short": True
                },
                {
                    "title": "🔹 Realized P&L (7d)",
                    "value": f"${pnl_7d.get('realized_pnl', 0):+.2f}",
                    "short": True
                },
                {
                    "title": "🔸 Unrealized P&L (Current)",
                    "value": f"${pnl_7d.get('unrealized_pnl', 0):+.2f}",
                    "short": True
                }
            ],
            "footer": f"Polymarket Bot • {account_name}",
            "ts": int(datetime.now().timestamp())
        }

        # Add top positions if any
        if positions:
            top_positions = sorted(
                positions,
                key=lambda p: abs(float(p.get("pnl", 0))),
                reverse=True
            )[:3]

            positions_text = "\n".join([
                f"• {pos.get('outcome', 'N/A')}: ${pos.get('value', 0):.2f} "
                f"(P&L: ${pos.get('pnl', 0):+.2f})"
                for pos in top_positions
            ])

            attachment["fields"].append({
                "title": "🎯 Top Positions by P&L",
                "value": positions_text or "No positions",
                "short": False
            })

        return self.send_message(
            text=f"{account_name} Daily Report - ${total_value:.2f}",
            attachments=[attachment]
        )

    def send_multi_account_report(
        self,
        reports: Dict[str, Dict]
    ) -> bool:
        """Send a consolidated report for multiple accounts.

        Args:
            reports: Dictionary mapping account names to their summaries

        Returns:
            True if sent successfully
        """
        # Calculate totals
        total_value = sum(r.get("total_value", 0) for r in reports.values())
        total_positions = sum(r.get("num_positions", 0) for r in reports.values())
        total_pnl_7d = sum(
            r.get("pnl_7d", {}).get("total_pnl", 0) for r in reports.values()
        )
        total_pnl_30d = sum(
            r.get("pnl_30d", {}).get("total_pnl", 0) for r in reports.values()
        )

        # Main summary attachment
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        summary_attachment = {
            "color": "good" if total_pnl_7d >= 0 else "danger",
            "title": "📊 Polymarket Portfolio - All Accounts",
            "text": f"Consolidated daily report as of {timestamp}",
            "fields": [
                {
                    "title": "💰 Total Portfolio Value",
                    "value": f"${total_value:.2f}",
                    "short": True
                },
                {
                    "title": "📈 Total Positions",
                    "value": f"{total_positions} open",
                    "short": True
                },
                {
                    "title": "📅 7-Day P&L",
                    "value": f"${total_pnl_7d:+.2f}",
                    "short": True
                },
                {
                    "title": "📆 30-Day P&L",
                    "value": f"${total_pnl_30d:+.2f}",
                    "short": True
                }
            ],
            "footer": "Polymarket Bot • Multi-Account Summary"
        }

        # Individual account attachments
        account_attachments = []
        for account_name, summary in reports.items():
            pnl_7d = summary.get("pnl_7d", {})
            total_pnl = pnl_7d.get("total_pnl", 0)

            account_attachments.append({
                "color": "#36a64f" if total_pnl >= 0 else "#ff0000",
                "author_name": account_name.upper(),
                "fields": [
                    {
                        "title": "Value",
                        "value": f"${summary.get('total_value', 0):.2f}",
                        "short": True
                    },
                    {
                        "title": "7d P&L",
                        "value": f"${total_pnl:+.2f}",
                        "short": True
                    }
                ]
            })

        return self.send_message(
            text=f"Daily Report - Total: ${total_value:.2f} (7d: ${total_pnl_7d:+.2f})",
            attachments=[summary_attachment] + account_attachments
        )

    def send_error_notification(
        self,
        account_name: str,
        error_message: str
    ) -> bool:
        """Send an error notification to Slack.

        Args:
            account_name: Account that encountered the error
            error_message: Error description

        Returns:
            True if sent successfully
        """
        attachment = {
            "color": "danger",
            "title": f"⚠️ Error in {account_name}",
            "text": error_message,
            "footer": "Polymarket Bot Error",
            "ts": int(datetime.now().timestamp())
        }

        return self.send_message(
            text=f"Error in {account_name}: {error_message}",
            attachments=[attachment]
        )
