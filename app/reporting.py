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
    """Generate a concise Korean weekly risk report."""
    now = now or dt.datetime.now(dt.timezone.utc)
    counts = _status_counts(results)

    alerts = [r for r in results if r.status == "ALERT"]
    watch = [r for r in results if r.status == "WATCH"]

    def bullet(items: List[TriggerResult], limit: int = 6) -> str:
        lines = []
        for r in items[:limit]:
            lines.append(f"- **{r.name}**: WoW {r.wow_change:+.2f}{r.wow_change_unit} → {r.status} ({r.rationale})")
        return "\n".join(lines) if lines else "- 해당 없음"

    headline = "주간 리스크 트리거 요약"
    summary_lines = [
        f"기준시각: {now.astimezone().strftime('%Y-%m-%d %H:%M %Z')}",
        f"상태 집계: ALERT {counts['ALERT']} / WATCH {counts['WATCH']} / OK {counts['OK']}",
        "",
        "### 🔴 ALERT 항목",
        bullet(alerts),
        "",
        "### 🟠 WATCH 항목",
        bullet(watch),
        "",
        "### ✅ 해석 (액션 가이드)",
    ]

    if counts["ALERT"] >= 2:
        summary_lines.append("- 변동성 확대 구간일 가능성이 높습니다. 신규 매수는 분할로, 현금/단기금리 비중을 점검하세요.")
    elif counts["ALERT"] == 1:
        summary_lines.append("- 단일 충격 신호가 나타났습니다. 해당 원인(금리/크레딧/테크)을 중심으로 리밸런싱 규칙을 적용하세요.")
    else:
        summary_lines.append("- 급격한 위험 신호는 제한적입니다. 기존 적립/리밸런싱 계획을 유지하되 WATCH 변화에는 주의하세요.")

    md = f"# {headline}\n\n" + "\n".join(summary_lines)
    text = md.replace("**", "").replace("# ", "").replace("### ", "").replace("🔴 ", "").replace("🟠 ", "").replace("✅ ", "")

    return {"markdown": md, "text": text, "counts": counts}
