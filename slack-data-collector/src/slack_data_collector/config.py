from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(ValueError):
    """Raised when required local configuration is missing or invalid."""


def load_env_file(path: Path) -> None:
    """Load a small dotenv-compatible file without overriding existing variables."""

    if not path.exists():
        return

    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        if "=" not in line:
            raise ConfigurationError(
                f"{path}:{line_number}: KEY=VALUE 형식이 아닙니다."
            )

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise ConfigurationError(
                f"{path}:{line_number}: 환경변수 이름이 비어 있습니다."
            )
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    channel_id: str

    @classmethod
    def from_environment(cls, env_file: Path) -> Settings:
        load_env_file(env_file)

        bot_token = os.getenv("SLACK_BOT_TOKEN", "").strip()
        channel_id = os.getenv("SLACK_CHANNEL_ID", "").strip()

        if not bot_token:
            raise ConfigurationError("SLACK_BOT_TOKEN이 설정되지 않았습니다.")
        if not bot_token.startswith("xoxb-"):
            raise ConfigurationError(
                "SLACK_BOT_TOKEN은 xoxb-로 시작하는 Bot Token이어야 합니다."
            )
        if not channel_id:
            raise ConfigurationError("SLACK_CHANNEL_ID가 설정되지 않았습니다.")

        return cls(bot_token=bot_token, channel_id=channel_id)
