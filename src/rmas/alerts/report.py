"""Render alert reports (plain text + HTML) from TradePlans."""

from __future__ import annotations

from datetime import datetime, timezone

from rmas.types import TradePlan


def _plan_block_text(p: TradePlan) -> str:
    r = p.rationale
    return (
        f"━━━ {p.ticker}  [{p.strategy}]  {p.direction.upper()} {p.instrument} ━━━\n"
        f"  Entry {p.entry} | Stop {p.stop} | Targets {p.targets}\n"
        f"  Size {p.shares} sh | Risk ${p.risk_usd} | Time-stop {p.time_stop_days}d\n"
        f"  Why signal : {r.get('why_signal','')}\n"
        f"  Why tradeable: {r.get('why_tradeable','')}\n"
        f"  Why now    : {r.get('why_now','')}\n"
        f"  Risk       : {r.get('risk','')}\n"
        f"  Exit       : {r.get('exit','')}\n"
        f"  Edge       : {r.get('edge','')}\n"
    )


def render_text(plans: list[TradePlan], asof: datetime | None = None) -> str:
    asof = asof or datetime.now(timezone.utc)
    header = (
        f"RMAS Alerts — {asof:%Y-%m-%d %H:%M UTC}\n"
        f"{len(plans)} setup(s) passed Discovery + Tradeability + Timing/Risk (all green).\n"
        "Few high-quality trades > many FOMO signals.\n"
        + "=" * 64 + "\n"
    )
    if not plans:
        return header + "No qualifying setups today. Staying flat.\n"
    return header + "\n".join(_plan_block_text(p) for p in plans)


def render_html(plans: list[TradePlan], asof: datetime | None = None) -> str:
    asof = asof or datetime.now(timezone.utc)
    rows = []
    for p in plans:
        r = p.rationale
        rows.append(f"""
        <div style="border:1px solid #ddd;border-radius:8px;padding:12px;margin:10px 0;font-family:Arial">
          <h3 style="margin:0 0 6px">{p.ticker}
            <span style="font-size:12px;color:#666">[{p.strategy}] {p.direction.upper()} · {p.instrument}</span></h3>
          <table style="font-size:13px;border-collapse:collapse">
            <tr><td><b>Entry</b></td><td>{p.entry}</td><td><b>Stop</b></td><td>{p.stop}</td>
                <td><b>Targets</b></td><td>{p.targets}</td></tr>
            <tr><td><b>Size</b></td><td>{p.shares} sh</td><td><b>Risk</b></td><td>${p.risk_usd}</td>
                <td><b>Time-stop</b></td><td>{p.time_stop_days}d</td></tr>
          </table>
          <ul style="font-size:13px;margin:8px 0">
            <li><b>Why signal:</b> {r.get('why_signal','')}</li>
            <li><b>Why tradeable:</b> {r.get('why_tradeable','')}</li>
            <li><b>Why now:</b> {r.get('why_now','')}</li>
            <li><b>Risk:</b> {r.get('risk','')}</li>
            <li><b>Exit:</b> {r.get('exit','')}</li>
            <li><b>Edge:</b> {r.get('edge','')}</li>
          </ul>
        </div>""")
    body = "".join(rows) or "<p>No qualifying setups today. Staying flat.</p>"
    return f"""<html><body style="background:#fafafa">
      <h2 style="font-family:Arial">RMAS Alerts — {asof:%Y-%m-%d %H:%M UTC}</h2>
      <p style="font-family:Arial;color:#444">{len(plans)} setup(s) — all three gates green.
         Few high-quality trades &gt; many FOMO signals.</p>
      {body}
    </body></html>"""
