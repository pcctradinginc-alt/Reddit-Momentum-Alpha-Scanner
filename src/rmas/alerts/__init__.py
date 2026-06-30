"""Alerts: build a human-readable report and (optionally) email it via SMTP.

An alert is only emitted when all three gates are GREEN. Each recommendation
explains why it's a signal, why it's tradeable, why now, the risk, the exit,
and the expected edge."""
