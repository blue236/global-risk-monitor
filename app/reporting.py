from __future__ import annotations

import datetime as dt
from typing import Dict, List

from .analytics import TriggerResult


def _status_counts(results: List[TriggerResult]) -> Dict[str, int]:
    counts = {"OK": 0, "WATCH": 0, "ALERT": 0}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    return counts


def generate_korean_report(results: List[TriggerResult], *, now: dt.datetime | None = None) -> Dict[str, str | dict]:
    """Generate a concise weekly risk report."""
    now = now or dt.datetime.now(dt.timezone.utc)
    counts = _status_counts(results)

    alerts = [r for r in results if r.status == "ALERT"]
    watch = [r for r in results if r.status == "WATCH"]

    def bullet(items: List[TriggerResult], limit: int = 6) -> str:
        lines = []
        for r in items[:limit]:
            lines.append(f"- **{r.name}**: WoW {r.wow_change:+.2f}{r.wow_change_unit} → {r.status} ({r.rationale})")
        return "\n".join(lines) if lines else "- None"

    headline = "Weekly risk trigger summary"
    summary_lines = [
        f"Reference time: {now.astimezone().strftime('%Y-%m-%d %H:%M %Z')}",
        f"Status summary: ALERT {counts['ALERT']} / WATCH {counts['WATCH']} / OK {counts['OK']}",
        "",
        "### 🔴 ALERT items",
        bullet(alerts),
        "",
        "### 🟠 WATCH items",
        bullet(watch),
        "",
        "### ✅ Interpretation (action guide)",
    ]

    if counts["ALERT"] >= 2:
        summary_lines.append("- This may be a period of elevated volatility. Scale in gradually on new buys and review your cash/short-duration allocation.")
    elif counts["ALERT"] == 1:
        summary_lines.append("- A single shock signal appeared. Apply rebalancing rules around the likely driver (rates/credit/tech).")
    else:
        summary_lines.append("- Sharp risk signals are limited. Keep your existing DCA/rebalancing plan, but monitor changes in WATCH signals.")

    md = f"# {headline}\n\n" + "\n".join(summary_lines)
    text = md.replace("**", "").replace("# ", "").replace("### ", "").replace("🔴 ", "").replace("🟠 ", "").replace("✅ ", "")

    return {"markdown": md, "text": text, "counts": counts}
