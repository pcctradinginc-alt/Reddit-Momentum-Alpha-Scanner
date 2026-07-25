"""Render alert reports (plain text + HTML) from TradePlans."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

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
        + (f"  Options    : {r['options']}\n" if r.get("options") else "")
        + f"  Edge       : {r.get('edge','')}\n"
    )


_DEGRADED_TEXT = (
    "⚠️  DEGRADED RUN — Reddit attention is SYNTHETIC (no live radar).\n"
    "    These are NOT actionable trade signals. Connect Reddit OAuth to go live.\n"
)


def render_text(plans: list[TradePlan], asof: datetime | None = None,
                actionable: bool = True) -> str:
    asof = asof or datetime.now(timezone.utc)
    banner = "" if actionable else _DEGRADED_TEXT
    header = (
        f"RMAS Alerts — {asof:%Y-%m-%d %H:%M UTC}\n"
        + banner
        + f"{len(plans)} setup(s) passed Discovery + Tradeability + Timing/Risk (all green).\n"
        "Few high-quality trades > many FOMO signals.\n"
        + "=" * 64 + "\n"
    )
    if not plans:
        return header + "No qualifying setups today. Staying flat.\n"
    return header + "\n".join(_plan_block_text(p) for p in plans)


def render_html(plans: list[TradePlan], asof: datetime | None = None,
                actionable: bool = True) -> str:
    asof = asof or datetime.now(timezone.utc)
    degraded = "" if actionable else (
        '<div style="background:#fff3cd;border:1px solid #ffca2c;padding:10px;'
        'border-radius:6px;font-family:Arial;color:#664d03">⚠️ <b>DEGRADED RUN</b> — '
        'Reddit attention is synthetic (no live radar). <b>Not actionable.</b> '
        'Connect Reddit OAuth to go live.</div>'
    )
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
      {degraded}
      <p style="font-family:Arial;color:#444">{len(plans)} setup(s) — all three gates green.
         Few high-quality trades &gt; many FOMO signals.</p>
      {body}
    </body></html>"""


def render_heartbeat(res: Any, meta_status: str, forward_summary: str) -> tuple[str, str]:
    """Liveness email for an actionable-but-0-setups day.

    Without this, ``run.email_when_no_setups=false`` means a perfectly
    healthy engine that simply found nothing tradeable looks IDENTICAL, from
    the user's inbox, to a silently broken cron job. This is explicitly NOT
    a trade signal — it just proves the pipeline ran, ingested real data,
    and made an honest "no setups today" call, plus the current meta-gate
    and forward-log status so cold-start progress is visible without
    running `rmas alpha-report` by hand.
    """
    asof = res.asof
    regime_name = res.regime.regime if res.regime else "?"
    rejected = dict(res.rejected)
    rejected_line = ", ".join(f"{k}={v}" for k, v in rejected.items()) or "(none rejected — few candidates reached the gates)"

    text = (
        f"RMAS heartbeat — engine live, 0 setups — {asof:%Y-%m-%d}\n"
        + "=" * 64 + "\n"
        "This is a LIVENESS CONFIRMATION, not a trade signal.\n"
        "The engine ran end-to-end on real data and found no qualifying setup today.\n\n"
        f"Regime          : {regime_name}\n"
        f"Candidates seen : {len(res.candidates)}\n"
        f"Rejected/gate   : {rejected_line}\n"
        f"Meta-gate       : {meta_status}\n\n"
        f"Forward-log maturity:\n{forward_summary}\n"
    )

    html = f"""<html><body style="background:#fafafa;font-family:Arial">
      <h2>RMAS heartbeat &mdash; engine live, 0 setups &mdash; {asof:%Y-%m-%d}</h2>
      <div style="background:#e7f1ff;border:1px solid #7ab0f5;padding:10px;border-radius:6px;color:#1a3d6d">
        This is a <b>liveness confirmation</b>, not a trade signal. The engine ran
        end-to-end on real data and found no qualifying setup today.
      </div>
      <table style="font-size:13px;margin-top:12px;border-collapse:collapse">
        <tr><td><b>Regime</b></td><td style="padding-left:8px">{regime_name}</td></tr>
        <tr><td><b>Candidates seen</b></td><td style="padding-left:8px">{len(res.candidates)}</td></tr>
        <tr><td><b>Rejected/gate</b></td><td style="padding-left:8px">{rejected_line}</td></tr>
        <tr><td><b>Meta-gate</b></td><td style="padding-left:8px">{meta_status}</td></tr>
      </table>
      <pre style="font-size:12px;background:#fff;border:1px solid #ddd;border-radius:6px;padding:10px">{forward_summary}</pre>
    </body></html>"""
    return text, html
