"""Trade analytics report generator - outputs standalone HTML with Chart.js charts.

Usage:
    python report.py                                    # data/default/trades.db
    python report.py path/to/trades.db                 # custom DB path
    python report.py path/to/trades.db report.html     # custom DB + output path
"""
import sqlite3
import json
import sys
from pathlib import Path
from datetime import datetime


DB_PATH = sys.argv[1] if len(sys.argv) > 1 else "data/default/trades.db"
OUTPUT_PATH = sys.argv[2] if len(sys.argv) > 2 else str(Path(DB_PATH).parent / "report.html")


def categorize(question: str) -> str:
    q = question.lower()
    if any(k in q for k in ["nba", "nfl", "nhl", "mlb", "basketball", "football", "hockey", "baseball"]):
        if "nba" in q or "basketball" in q:
            return "NBA/농구"
        if "nfl" in q or "mvp" in q or "rookie of the year" in q or "super bowl" in q:
            return "NFL/미식축구"
        if "nhl" in q or "hockey" in q or "art ross" in q:
            return "NHL/아이스하키"
        if "mlb" in q or "baseball" in q:
            return "MLB/야구"
    if any(k in q for k in ["masters", "golf", "pga", "mcilroy", "scheffler", "dechambeau", "fleetwood", "rahm", "reed", "burns"]):
        return "골프"
    if any(k in q for k in ["world cup", "fifa", "soccer", "qualify"]):
        return "축구"
    if any(k in q for k in ["academy award", "oscar", "best picture", "best actor", "best actress",
                              "best director", "best supporting", "best cinemat", "best costume",
                              "best makeup", "best production", "best original", "best adapted", "best film editing"]):
        return "아카데미 시상식"
    if any(k in q for k in ["primary", "election", "senate", "chamber", "cornyn", "paxton", "hunt", "colombian"]):
        return "정치/선거"
    if any(k in q for k in ["revenue", "tariff", "collect", "u.s. collect"]):
        return "경제/관세"
    if any(k in q for k in ["bitcoin", "crypto", "bitboy", "convicted"]):
        return "크립토"
    return "기타"


def load_trades():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT id, question, outcome, buy_price, sell_price, realized_pnl,
               buy_timestamp, sell_timestamp, exit_reason, entry_reason, buy_amount
        FROM trades
        WHERE status = 'COMPLETED'
        ORDER BY sell_timestamp
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


EXIT_REASON_KO = {
    "trailing_stop": "트레일링 스탑",
    "stop_loss":     "손절",
    "time_exit":     "시간 기반 청산",
    "take_profit":   "익절",
    "forced_close":  "강제 청산",
}


def build_report(trades):
    # --- per-trade enrichment ---
    for t in trades:
        t["category"] = categorize(t["question"])
        t["pnl"] = t["realized_pnl"] or 0.0
        t["won"] = t["pnl"] > 0
        ts = t["sell_timestamp"] or t["buy_timestamp"] or ""
        t["month"] = ts[:7] if ts else "unknown"

    total = len(trades)
    wins = sum(1 for t in trades if t["won"])
    total_pnl = sum(t["pnl"] for t in trades)
    avg_pnl = total_pnl / total if total else 0
    win_rate = wins / total * 100 if total else 0

    # --- monthly P&L ---
    monthly = {}
    for t in trades:
        m = t["month"]
        monthly.setdefault(m, {"pnl": 0.0, "count": 0, "wins": 0})
        monthly[m]["pnl"] += t["pnl"]
        monthly[m]["count"] += 1
        if t["won"]:
            monthly[m]["wins"] += 1
    months_sorted = sorted(monthly.keys())

    # --- category stats ---
    cat_stats = {}
    for t in trades:
        c = t["category"]
        cat_stats.setdefault(c, {"pnl": 0.0, "count": 0, "wins": 0})
        cat_stats[c]["pnl"] += t["pnl"]
        cat_stats[c]["count"] += 1
        if t["won"]:
            cat_stats[c]["wins"] += 1
    cats_sorted = sorted(cat_stats.keys(), key=lambda c: cat_stats[c]["pnl"], reverse=True)

    # --- exit reason ---
    exit_counts = {}
    for t in trades:
        r = t["exit_reason"] or "unknown"
        exit_counts[r] = exit_counts.get(r, 0) + 1

    # --- trade list for table ---
    trade_rows_html = ""
    for t in sorted(trades, key=lambda x: x["sell_timestamp"] or "", reverse=True):
        pnl_class = "pos" if t["pnl"] > 0 else "neg"
        pnl_str = f"+${t['pnl']:.2f}" if t["pnl"] > 0 else f"-${abs(t['pnl']):.2f}"
        sell_ts = (t["sell_timestamp"] or "")[:10]
        short_q = t["question"][:55] + ("…" if len(t["question"]) > 55 else "")
        trade_rows_html += f"""
        <tr>
          <td>{t['id']}</td>
          <td title="{t['question']}">{short_q}</td>
          <td>{t['outcome']}</td>
          <td>{t['category']}</td>
          <td>{t['buy_price']:.3f}</td>
          <td>{f"{t['sell_price']:.3f}" if t['sell_price'] else '-'}</td>
          <td class="{pnl_class}">{pnl_str}</td>
          <td>{EXIT_REASON_KO.get(t['exit_reason'], t['exit_reason'] or '-')}</td>
          <td>{sell_ts}</td>
        </tr>"""

    # --- JSON for charts ---
    monthly_labels = json.dumps(months_sorted)
    monthly_pnl = json.dumps([round(monthly[m]["pnl"], 2) for m in months_sorted])
    monthly_counts = json.dumps([monthly[m]["count"] for m in months_sorted])

    cat_labels = json.dumps(cats_sorted)
    cat_pnl = json.dumps([round(cat_stats[c]["pnl"], 2) for c in cats_sorted])
    cat_counts = json.dumps([cat_stats[c]["count"] for c in cats_sorted])
    cat_winrates = json.dumps([
        round(cat_stats[c]["wins"] / cat_stats[c]["count"] * 100, 1) for c in cats_sorted
    ])

    exit_counts_ko = {EXIT_REASON_KO.get(k, k): v for k, v in exit_counts.items()}
    exit_labels = json.dumps(list(exit_counts_ko.keys()))
    exit_values = json.dumps(list(exit_counts_ko.values()))

    # cumulative P&L
    cum_pnl = []
    running = 0.0
    cum_dates = []
    for t in sorted(trades, key=lambda x: x["sell_timestamp"] or ""):
        running += t["pnl"]
        cum_pnl.append(round(running, 2))
        cum_dates.append((t["sell_timestamp"] or "")[:10])
    cum_labels = json.dumps(cum_dates)
    cum_values = json.dumps(cum_pnl)

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>Polybot 거래 분석 리포트</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          background: #0f1117; color: #e0e0e0; padding: 24px; }}
  h1 {{ font-size: 1.6rem; margin-bottom: 4px; color: #fff; }}
  .subtitle {{ color: #888; font-size: 0.85rem; margin-bottom: 24px; }}
  .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
               gap: 16px; margin-bottom: 32px; }}
  .kpi {{ background: #1a1d27; border-radius: 10px; padding: 18px 20px; }}
  .kpi .label {{ font-size: 0.75rem; color: #888; text-transform: uppercase; letter-spacing: .05em; }}
  .kpi .value {{ font-size: 1.8rem; font-weight: 700; margin-top: 4px; }}
  .kpi .value.pos {{ color: #4caf50; }}
  .kpi .value.neg {{ color: #f44336; }}
  .kpi .value.neutral {{ color: #90caf9; }}
  .charts-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 32px; }}
  .chart-box {{ background: #1a1d27; border-radius: 10px; padding: 20px; }}
  .chart-box.wide {{ grid-column: 1 / -1; }}
  .chart-box h2 {{ font-size: 0.95rem; color: #bbb; margin-bottom: 14px; }}
  canvas {{ max-height: 280px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; }}
  th {{ background: #252836; color: #aaa; text-align: left;
        padding: 8px 10px; position: sticky; top: 0; }}
  td {{ padding: 7px 10px; border-bottom: 1px solid #252836; }}
  tr:hover td {{ background: #1f2230; }}
  .pos {{ color: #4caf50; font-weight: 600; }}
  .neg {{ color: #f44336; font-weight: 600; }}
  .table-wrap {{ background: #1a1d27; border-radius: 10px; padding: 20px;
                 max-height: 480px; overflow-y: auto; }}
  .table-wrap h2 {{ font-size: 0.95rem; color: #bbb; margin-bottom: 14px; }}
  @media (max-width: 700px) {{ .charts-grid {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<h1>📊 Polybot 거래 분석 리포트</h1>
<p class="subtitle">생성: {generated_at} &nbsp;·&nbsp; 완료 거래 {total}건 기준</p>

<div class="kpi-grid">
  <div class="kpi">
    <div class="label">총 거래 수</div>
    <div class="value neutral">{total}건</div>
  </div>
  <div class="kpi">
    <div class="label">승률</div>
    <div class="value {'pos' if win_rate >= 50 else 'neg'}">{win_rate:.1f}%</div>
  </div>
  <div class="kpi">
    <div class="label">총 손익 (P&L)</div>
    <div class="value {'pos' if total_pnl >= 0 else 'neg'}">{'+' if total_pnl >= 0 else ''}${total_pnl:.2f}</div>
  </div>
  <div class="kpi">
    <div class="label">거래당 평균 P&L</div>
    <div class="value {'pos' if avg_pnl >= 0 else 'neg'}">{'+' if avg_pnl >= 0 else ''}${avg_pnl:.2f}</div>
  </div>
  <div class="kpi">
    <div class="label">수익 거래</div>
    <div class="value pos">{wins}건</div>
  </div>
  <div class="kpi">
    <div class="label">손실 거래</div>
    <div class="value neg">{total - wins}건</div>
  </div>
</div>

<div class="charts-grid">
  <div class="chart-box wide">
    <h2>누적 손익 (P&L) 추이</h2>
    <canvas id="cumChart"></canvas>
  </div>
  <div class="chart-box">
    <h2>카테고리별 총 손익</h2>
    <canvas id="catPnlChart"></canvas>
  </div>
  <div class="chart-box">
    <h2>카테고리별 승률 & 거래 수</h2>
    <canvas id="catWinChart"></canvas>
  </div>
  <div class="chart-box">
    <h2>월별 손익</h2>
    <canvas id="monthChart"></canvas>
  </div>
  <div class="chart-box">
    <h2>청산 사유 분포</h2>
    <canvas id="exitChart"></canvas>
  </div>
</div>

<div class="table-wrap">
  <h2>거래 내역 (최신순)</h2>
  <table>
    <thead>
      <tr>
        <th>ID</th><th>질문</th><th>포지션</th><th>카테고리</th>
        <th>매수가</th><th>매도가</th><th>손익</th><th>청산 사유</th><th>매도일</th>
      </tr>
    </thead>
    <tbody>{trade_rows_html}</tbody>
  </table>
</div>

<script>
const GRID_COLOR = 'rgba(255,255,255,0.07)';
const defaults = {{
  color: '#bbb',
  plugins: {{ legend: {{ labels: {{ color: '#bbb' }} }} }},
  scales: {{
    x: {{ ticks: {{ color: '#888' }}, grid: {{ color: GRID_COLOR }} }},
    y: {{ ticks: {{ color: '#888' }}, grid: {{ color: GRID_COLOR }} }}
  }}
}};

// Cumulative P&L
new Chart(document.getElementById('cumChart'), {{
  type: 'line',
  data: {{
    labels: {cum_labels},
    datasets: [{{
      label: '누적 P&L ($)',
      data: {cum_values},
      borderColor: '#4caf50',
      backgroundColor: 'rgba(76,175,80,0.08)',
      fill: true,
      tension: 0.3,
      pointRadius: 3,
    }}]
  }},
  options: {{ ...defaults, plugins: {{ legend: {{ display: false }} }} }}
}});

// Category P&L (horizontal bar)
const catPnlData = {cat_pnl};
new Chart(document.getElementById('catPnlChart'), {{
  type: 'bar',
  data: {{
    labels: {cat_labels},
    datasets: [{{
      label: '손익 ($)',
      data: catPnlData,
      backgroundColor: catPnlData.map(v => v >= 0 ? 'rgba(76,175,80,0.7)' : 'rgba(244,67,54,0.7)'),
      borderRadius: 4,
    }}]
  }},
  options: {{
    indexAxis: 'y',
    ...defaults,
    plugins: {{ legend: {{ display: false }} }}
  }}
}});

// Category win rate + count
new Chart(document.getElementById('catWinChart'), {{
  type: 'bar',
  data: {{
    labels: {cat_labels},
    datasets: [
      {{
        label: '승률 (%)',
        data: {cat_winrates},
        backgroundColor: 'rgba(144,202,249,0.7)',
        borderRadius: 4,
        yAxisID: 'y',
      }},
      {{
        label: '거래 수',
        data: {cat_counts},
        backgroundColor: 'rgba(255,183,77,0.5)',
        borderRadius: 4,
        yAxisID: 'y2',
        type: 'line',
        borderColor: 'rgba(255,183,77,0.9)',
        tension: 0.3,
        pointRadius: 4,
      }}
    ]
  }},
  options: {{
    ...defaults,
    scales: {{
      x: {{ ticks: {{ color: '#888' }}, grid: {{ color: GRID_COLOR }} }},
      y: {{ ticks: {{ color: '#888' }}, grid: {{ color: GRID_COLOR }}, title: {{ display: true, text: '승률 (%)', color: '#888' }} }},
      y2: {{ position: 'right', ticks: {{ color: '#888' }}, grid: {{ display: false }}, title: {{ display: true, text: '거래 수', color: '#888' }} }}
    }},
    plugins: {{ legend: {{ labels: {{ color: '#bbb' }} }} }}
  }}
}});

// Monthly P&L
const monthPnl = {monthly_pnl};
new Chart(document.getElementById('monthChart'), {{
  type: 'bar',
  data: {{
    labels: {monthly_labels},
    datasets: [{{
      label: '월별 P&L ($)',
      data: monthPnl,
      backgroundColor: monthPnl.map(v => v >= 0 ? 'rgba(76,175,80,0.7)' : 'rgba(244,67,54,0.7)'),
      borderRadius: 4,
    }}]
  }},
  options: {{ ...defaults, plugins: {{ legend: {{ display: false }} }} }}
}});

// Exit reasons (doughnut)
new Chart(document.getElementById('exitChart'), {{
  type: 'doughnut',
  data: {{
    labels: {exit_labels},
    datasets: [{{
      data: {exit_values},
      backgroundColor: ['#90caf9','#ffcc80','#ef9a9a','#a5d6a7','#ce93d8','#80deea'],
      borderWidth: 0,
    }}]
  }},
  options: {{
    plugins: {{
      legend: {{ position: 'right', labels: {{ color: '#bbb', padding: 12 }} }}
    }}
  }}
}});
</script>
</body>
</html>"""
    return html


def main():
    trades = load_trades()
    html = build_report(trades)
    Path(OUTPUT_PATH).write_text(html, encoding="utf-8")
    print(f"리포트 생성 완료: {OUTPUT_PATH}  ({len(trades)}건)")


if __name__ == "__main__":
    main()
