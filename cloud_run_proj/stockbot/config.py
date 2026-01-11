from __future__ import annotations
from dataclasses import dataclass
import os
from dotenv import load_dotenv

@dataclass(frozen=True)
class Config:
    supabase_url: str
    supabase_service_role_key: str | None
    supabase_anon_key: str | None
    tickers: tuple[str, ...]
    kr_tickers: tuple[str, ...]

    telegram_bot_token: str | None
    telegram_chat_id: str | None

    smtp_host: str | None
    smtp_port: int | None
    smtp_user: str | None
    smtp_pass: str | None
    email_from: str | None
    email_to: str | None

    @staticmethod
    def from_env() -> "Config":
        load_dotenv()

        def _split_syms(s: str | None, upper: bool = True) -> tuple[str, ...]:
            if not s:
                return ()
            if upper:
                return tuple(x.strip().upper() for x in s.split(",") if x.strip())
            return tuple(x.strip() for x in s.split(",") if x.strip())

        return Config(
            supabase_url=os.getenv("SUPABASE_URL", ""),
            supabase_service_role_key=os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
            supabase_anon_key=os.getenv("SUPABASE_ANON_KEY"),
            tickers=_split_syms(os.getenv("TICKERS", "CPNG,MSFT,AMZN")),
            kr_tickers=_split_syms(os.getenv("KR_TICKERS", ""), upper=False),

            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),

            smtp_host=os.getenv("SMTP_HOST"),
            smtp_port=int(os.getenv("SMTP_PORT", "0")) or None,
            smtp_user=os.getenv("SMTP_USER"),
            smtp_pass=os.getenv("SMTP_PASS"),
            email_from=os.getenv("EMAIL_FROM"),
            email_to=os.getenv("EMAIL_TO"),
        )
