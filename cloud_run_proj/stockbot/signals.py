from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from typing import Iterable, List, Literal
from .config import Config
from .db import SupaDB
from .indicators import compute_sma_cross
from .notifiers import send_telegram, send_email

@dataclass(frozen=True)
class SignalMessage:
    ticker: str
    d: date
    kind: str
    price: float
    sma5: float
    sma60: float

    def to_text(self) -> str:
        arrow = "🟢" if self.kind == "golden_cross" else "🔴"
        korean_signal = "매수 신호" if self.kind == "golden_cross" else "매도 신호"
        
        korean_message = (f"{arrow} <b>{self.ticker}</b> {self.d.isoformat()} - {korean_signal}\n"
                         f"종가: {self.price:.2f} / 5일평균: {self.sma5:.2f} / 60일평균: {self.sma60:.2f}")
        
        english_message = (f"{self.kind.replace('_',' ').title()}\n"
                          f"Close: {self.price:.2f} / SMA5: {self.sma5:.2f} / SMA60: {self.sma60:.2f}")
        
        return f"{korean_message}\n\n{english_message}"

    def to_email(self) -> tuple[str, str]:
        subject = f"[{self.ticker}] {self.kind.upper()} on {self.d.isoformat()}"
        body = f"""{self.ticker} {self.kind}
Date   : {self.d.isoformat()}
Close  : {self.price:.2f}
SMA5   : {self.sma5:.2f}
SMA60  : {self.sma60:.2f}
"""
        return subject, body

def run_signal_detection(
    conf: Config,
    only: Iterable[str] | None = None,
    dry_run: bool = False,
    debug_mode: bool = False,
    market: Literal["us", "kr"] = "us"
) -> List[SignalMessage]:
    key = conf.supabase_service_role_key or conf.supabase_anon_key
    if not conf.supabase_url or not key:
        raise RuntimeError("Supabase URL/Key 설정이 필요합니다 (.env).")

    db = SupaDB(conf.supabase_url, key)

    # market에 따라 ticker 목록 선택
    if market == "kr":
        tickers = tuple(only) if only else conf.kr_tickers
    else:
        tickers = tuple(only) if only else conf.tickers

    found: List[SignalMessage] = []
    for sym in tickers:
        print(f"[signal] {sym}")
        rows = db.fetch_last_n(sym, 65)
        res = compute_sma_cross(rows, debug_mode=debug_mode)
        if not res:
            continue
        df, cross = res
        if not cross:
            continue

        d = df.dropna().index[-1]  # last valid day
        msg = SignalMessage(sym, d, cross.signal_type, cross.price, cross.sma5, cross.sma60)
        found.append(msg)

        db.upsert_signal(sym, d, cross.signal_type, cross.price, cross.sma5, cross.sma60)

        if dry_run:
            print("[dry-run]", msg.to_text())
            continue

        if conf.telegram_bot_token and conf.telegram_chat_id:
            try:
                send_telegram(conf.telegram_bot_token, conf.telegram_chat_id, msg.to_text())
            except Exception as e:
                print(f"[warn] telegram failed: {e}")

        if (conf.smtp_host and conf.smtp_port and conf.smtp_user and conf.smtp_pass and
            conf.email_from and conf.email_to):
            try:
                subj, body = msg.to_email()
                send_email(conf.smtp_host, conf.smtp_port, conf.smtp_user, conf.smtp_pass,
                           conf.email_from, conf.email_to, subj, body)
            except Exception as e:
                print(f"[warn] email failed: {e}")

    return found
