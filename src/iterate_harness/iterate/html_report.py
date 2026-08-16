"""Self-contained single-file HTML rendering of the final iterate report.

The canonical loops append one ``report`` entry to the decision log
(:mod:`.decision_log`); this module renders that entry — plus the fix
timeline entries (``atomic_fix`` / ``revert`` / ``validation``) that lead
up to it — into ONE ``.html`` file with zero external dependencies:

- inline CSS only (no CDN, works offline / inside CI artifacts);
- inline SVG convergence curve (findings per round);
- severity + dimension distribution bars;
- the full findings table with failure scenarios and suggested fixes;
- per-fix diffs whenever the decision log carries them.

All interpolated text is HTML-escaped; severity values map to a fixed
color table before touching CSS, so log content cannot inject markup.
"""

from __future__ import annotations

import html as _html
from typing import Any

from .ci_report import latest_report_entry
from .decision_log import DecisionLogEntry

#: Timeline entry types rendered in the fix timeline section.
TIMELINE_TYPES = ("atomic_fix", "revert", "validation")

#: Upper bound of timeline entries rendered (log grows unboundedly).
MAX_TIMELINE_ENTRIES = 50

#: Fixed severity → badge color mapping (values never come from the log).
SEVERITY_COLORS: dict[str, str] = {
    "critical": "#b91c1c",
    "high": "#ea580c",
    "medium": "#ca8a04",
    "low": "#2563eb",
}

#: Replay entry types that get their own card inside a round panel.
REPLAY_ENTRY_TYPES = (
    "round_start",
    "review_result",
    "atomic_fix",
    "architectural_fix",
    "revert",
    "validation",
    "decision",
)

#: Upper bound of per-round findings rendered in the replay page.
MAX_REPLAY_FINDINGS = 200

#: Upper bound of diff preview lines inside a replay card.
MAX_REPLAY_DIFF_LINES = 80

_BASE_CSS = (
    ":root{color-scheme:light}*{box-sizing:border-box}"
    "body{font-family:ui-sans-serif,system-ui,-apple-system,'Segoe UI',sans-serif;"
    "margin:0;background:#f8fafc;color:#0f172a}"
    ".wrap{max-width:980px;margin:0 auto;padding:32px 20px 64px}"
    "header h1{font-size:22px;margin:0 0 4px}"
    "header p.meta{color:#64748b;font-size:13px;margin:2px 0}"
    ".badge{display:inline-block;padding:2px 10px;border-radius:999px;"
    "font-size:12px;font-weight:600;color:#fff;vertical-align:middle}"
    ".cards{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0}"
    ".card{background:#fff;border:1px solid #e2e8f0;border-radius:10px;"
    "padding:14px 18px;min-width:130px}"
    ".card .k{font-size:12px;color:#64748b}"
    ".card .v{font-size:22px;font-weight:700;margin-top:2px}"
    "section{background:#fff;border:1px solid #e2e8f0;border-radius:10px;"
    "padding:18px 20px;margin:16px 0}"
    "section h2{font-size:15px;margin:0 0 12px;color:#334155}"
    ".bar-row{display:flex;align-items:center;gap:10px;margin:6px 0}"
    ".bar-label{width:110px;font-size:13px;color:#334155;text-align:right}"
    ".bar-track{flex:1;background:#f1f5f9;border-radius:6px;height:18px}"
    ".bar-fill{height:18px;border-radius:6px;min-width:2px}"
    ".bar-value{width:34px;font-size:13px;font-variant-numeric:tabular-nums}"
    "table{width:100%;border-collapse:collapse;font-size:13px}"
    "th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #e2e8f0;"
    "vertical-align:top}"
    "th{color:#64748b;font-weight:600;font-size:12px;text-transform:uppercase}"
    "td code{font-family:ui-monospace,'SF Mono',Menlo,monospace;font-size:12px}"
    ".timeline-item{border-left:3px solid #cbd5e1;padding:6px 0 6px 14px;margin:8px 0}"
    ".timeline-item .t{font-size:12px;color:#64748b}"
    "pre.diff{background:#0f172a;color:#e2e8f0;border-radius:8px;padding:12px;"
    "font-size:12px;overflow-x:auto;line-height:1.5}"
    "pre.diff .add{color:#4ade80}"
    "pre.diff .del{color:#f87171}"
    "footer{color:#94a3b8;font-size:12px;text-align:center;margin-top:24px}"
)


def _esc(value: object) -> str:
    """HTML-escape any log-derived text before interpolation."""
    return _html.escape(str(value if value is not None else ""), quote=True)


def _severity_color(severity: object) -> str:
    key = str(severity or "").strip().lower()
    return SEVERITY_COLORS.get(key, "#64748b")


def _int_or_zero(value: object) -> int:
    return value if isinstance(value, int) else 0


def build_html_report(entries: list[DecisionLogEntry]) -> str | None:
    """Render the latest ``report`` entry as one self-contained HTML page.

    Returns ``None`` when the log carries no report entry yet.
    """
    report = latest_report_entry(entries)
    if report is None:
        return None
    data = report.data if isinstance(report.data, dict) else {}
    timeline = _collect_timeline(entries, report)

    parts = [
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        "<title>iterate report</title>",
        f"<style>{_BASE_CSS}</style></head><body><div class=\"wrap\">",
        _render_header(data, report),
        _render_summary_cards(data),
        _render_convergence_chart(data),
        _render_distribution(data),
        _render_findings_table(data),
        _render_fix_timeline(timeline),
        "<footer>generated by iterate-harness · single-file report · works offline</footer>",
        "</div></body></html>",
    ]
    return "".join(parts)


def _render_header(data: dict[str, Any], report: DecisionLogEntry) -> str:
    mode = _esc(data.get("mode") or "dry-run")
    verdict = str(data.get("verdict") or "unknown")
    converged = bool(data.get("convergence", {}).get("converged")) if isinstance(
        data.get("convergence"), dict
    ) else False
    convergence_text = "converged" if converged else "not converged"
    color = "#16a34a" if verdict == "approved" else "#b91c1c"
    return (
        f"<header><h1>iterate report <span class=\"badge\" style=\"background:{color}\">"
        f"{_esc(verdict)}</span> <span class=\"badge\" style=\"background:#475569\">"
        f"{mode}</span> <span class=\"badge\" style=\"background:"
        f"{'#16a34a' if converged else '#ea580c'}\">{_esc(convergence_text)}</span></h1>"
        f"<p class=\"meta\">goal: {_esc(data.get('goal') or '(none)')}</p>"
        f"<p class=\"meta\">final entry: {_esc(report.timestamp)} · round {_int_or_zero(report.round)}</p>"
        "</header>"
    )


def _render_summary_cards(data: dict[str, Any]) -> str:
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    convergence = data.get("convergence") if isinstance(data.get("convergence"), dict) else {}
    cards = [
        ("total findings", _int_or_zero(summary.get("totalFindings"))),
        ("critical", _int_or_zero(summary.get("critical"))),
        ("high", _int_or_zero(summary.get("high"))),
        ("rounds", _int_or_zero(convergence.get("totalRounds"))),
    ]
    body = "".join(
        f"<div class=\"card\"><div class=\"k\">{_esc(label)}</div>"
        f"<div class=\"v\">{value}</div></div>"
        for label, value in cards
    )
    return f"<div class=\"cards\">{body}</div>"


def _render_convergence_chart(data: dict[str, Any]) -> str:
    """Inline SVG line chart of findings per round (convergence curve)."""
    convergence = data.get("convergence") if isinstance(data.get("convergence"), dict) else {}
    raw_series = convergence.get("findingsByRound")
    series = [v for v in raw_series if isinstance(v, int)] if isinstance(raw_series, list) else []
    width, height, pad = 720, 220, 34
    if not series:
        return (
            "<section><h2>Convergence curve</h2>"
            "<p class=\"meta\">no per-round data recorded</p></section>"
        )
    max_value = max(max(series), 1)
    step_x = (width - pad * 2) / max(len(series) - 1, 1)
    points: list[str] = []
    for index, value in enumerate(series):
        x = pad + index * step_x
        y = height - pad - (value / max_value) * (height - pad * 2)
        points.append(f"{x:.1f},{y:.1f}")
    polyline = " ".join(points)
    dots = "".join(
        f"<circle cx=\"{p.split(',')[0]}\" cy=\"{p.split(',')[1]}\" r=\"4\" "
        f"fill=\"#2563eb\"/>"
        + f"<text x=\"{p.split(',')[0]}\" y=\"{float(p.split(',')[1]) - 10}\" "
        f"font-size=\"11\" text-anchor=\"middle\" fill=\"#334155\">{series[i]}</text>"
        for i, p in enumerate(points)
    )
    stopped = _esc(convergence.get("stoppedReason") or "")
    return (
        "<section><h2>Convergence curve (findings per round)</h2>"
        f"<svg viewBox=\"0 0 {width} {height}\" width=\"100%\" role=\"img\" "
        "aria-label=\"findings per round\">"
        f"<line x1=\"{pad}\" y1=\"{height - pad}\" x2=\"{width - pad}\" "
        f"y2=\"{height - pad}\" stroke=\"#cbd5e1\"/>"
        "<polyline points=\"" + polyline + "\" fill=\"none\" stroke=\"#2563eb\" "
        "stroke-width=\"2.5\"/>" + dots + "</svg>"
        f"<p class=\"meta\">stopped: {stopped}</p></section>"
    )


def _render_distribution(data: dict[str, Any]) -> str:
    """Severity and per-dimension distribution bars."""
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    severity_rows = _bars(
        [("critical", _int_or_zero(summary.get("critical"))),
         ("high", _int_or_zero(summary.get("high"))),
         ("medium", _int_or_zero(summary.get("medium"))),
         ("low", _int_or_zero(summary.get("low")))],
        color_fn=_severity_color,
    )
    by_dimension = summary.get("byDimension")
    dimension_pairs: list[tuple[str, int]] = []
    if isinstance(by_dimension, dict):
        dimension_pairs = sorted(
            ((str(k), _int_or_zero(v)) for k, v in by_dimension.items()),
            key=lambda pair: (-pair[1], pair[0]),
        )
    dimension_rows = _bars(dimension_pairs, color_fn=lambda _: "#6366f1")
    return (
        "<section><h2>Severity distribution</h2>" + severity_rows + "</section>"
        "<section><h2>Dimension distribution</h2>" + (dimension_rows or "<p class=\"meta\">none</p>") + "</section>"
    )


def _bars(pairs: list[tuple[str, int]], *, color_fn) -> str:
    """Render horizontal bars; scale is relative to the largest value."""
    if not pairs:
        return "<p class=\"meta\">none</p>"
    peak = max((value for _, value in pairs), default=0) or 1
    rows = []
    for label, value in pairs:
        pct = round(value / peak * 100, 1)
        rows.append(
            f"<div class=\"bar-row\"><div class=\"bar-label\">{_esc(label)}</div>"
            f"<div class=\"bar-track\"><div class=\"bar-fill\" style=\"width:{pct}%;"
            f"background:{color_fn(label)}\"></div></div>"
            f"<div class=\"bar-value\">{value}</div></div>"
        )
    return "".join(rows)


def _render_findings_table(data: dict[str, Any]) -> str:
    raw = data.get("findings")
    findings = [f for f in raw if isinstance(f, dict)] if isinstance(raw, list) else []
    if not findings:
        return "<section><h2>Findings</h2><p class=\"meta\">no findings recorded</p></section>"
    rows = []
    for finding in findings:
        severity = str(finding.get("severity") or "?")
        color = _severity_color(severity)
        file_name = _esc(finding.get("file") or "(no file)")
        line_value = finding.get("line")
        location = (
            f"{file_name}<br>line {_int_or_zero(line_value)}"
            if isinstance(line_value, int) and line_value > 0
            else file_name
        )
        rows.append(
            f"<tr><td><span class=\"badge\" style=\"background:{color}\">"
            f"{_esc(severity)}</span></td>"
            f"<td><code>{location}</code></td>"
            f"<td>{_esc(finding.get('dimension') or 'general')}</td>"
            f"<td><strong>{_esc(finding.get('summary') or '')}</strong><br>"
            f"<span style=\"color:#64748b\">failure:</span> "
            f"{_esc(finding.get('failure_scenario') or '-')}<br>"
            f"<span style=\"color:#64748b\">fix:</span> "
            f"{_esc(finding.get('suggested_fix') or '-')}</td></tr>"
        )
    return (
        "<section><h2>Findings</h2><table><thead><tr><th>severity</th><th>location</th>"
        "<th>dimension</th><th>detail</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></section>"
    )


def _collect_timeline(
    entries: list[DecisionLogEntry], report: DecisionLogEntry
) -> list[DecisionLogEntry]:
    """Timeline entries recorded before the final report (this run's tail)."""
    timeline: list[DecisionLogEntry] = []
    for entry in entries:
        if entry is report:
            break
        if entry.type in TIMELINE_TYPES:
            timeline.append(entry)
    return timeline[-MAX_TIMELINE_ENTRIES:]


def _render_fix_timeline(timeline: list[DecisionLogEntry]) -> str:
    if not timeline:
        return (
            "<section><h2>Fix timeline</h2>"
            "<p class=\"meta\">no fix/validation entries before this report</p></section>"
        )
    items = []
    for entry in timeline:
        data = entry.data if isinstance(entry.data, dict) else {}
        diff_raw = data.get("diff")
        diff_html = ""
        if isinstance(diff_raw, str) and diff_raw.strip():
            diff_html = "<pre class=\"diff\">" + _render_diff_body(diff_raw) + "</pre>"
        detail = _esc(
            data.get("summary")
            or data.get("description")
            or data.get("command")
            or data.get("file")
            or ""
        )
        items.append(
            f"<div class=\"timeline-item\"><div class=\"t\">{_esc(entry.timestamp)} · "
            f"round {_int_or_zero(entry.round)} · {_esc(entry.type)}</div>"
            f"<div>{detail}</div>{diff_html}</div>"
        )
    return "<section><h2>Fix timeline</h2>" + "".join(items) + "</section>"


def _render_diff_body(diff_text: str) -> str:
    """Escape a unified-diff payload and color +/- lines."""
    lines = []
    for line in diff_text.splitlines():
        escaped = _esc(line)
        if line.startswith("+"):
            lines.append(f"<span class=\"add\">{escaped}</span>")
        elif line.startswith("-"):
            lines.append(f"<span class=\"del\">{escaped}</span>")
        else:
            lines.append(escaped)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Interactive round replay page (design §14.4 finding #6 "HTML 报告服务化")
#
# A second self-contained page: every decision-log round becomes one
# navigable panel (review → fixes → validation → decision), plus a final
# full-report panel. Navigation (prev / next / jump dots / ← → keys) is
# implemented with a tiny inline script that only toggles CSS classes —
# every piece of log-derived text is rendered server-side and HTML-escaped,
# so no user data ever flows into JavaScript.
# ---------------------------------------------------------------------------

_REPLAY_CSS = (
    ".rp{max-width:820px;margin:0 auto;padding:24px 20px 64px}"
    ".rp h1{font-size:20px;margin:0 0 2px}"
    ".rp p.sub{color:#64748b;font-size:13px;margin:2px 0 0}"
    ".rp .controls{display:flex;align-items:center;gap:10px;margin:16px 0;"
    "position:sticky;top:0;background:#f8fafc;padding:8px 0;z-index:5}"
    ".rp button{border:1px solid #cbd5e1;background:#fff;color:#0f172a;"
    "border-radius:8px;padding:6px 14px;font-size:13px;cursor:pointer}"
    ".rp button:hover:not(:disabled){background:#f1f5f9}"
    ".rp button:disabled{opacity:.4;cursor:default}"
    ".rp .round-num{font-weight:600;font-size:13px;min-width:112px;text-align:center}"
    ".rp .jumps{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 14px}"
    ".rp .jumps button{min-width:30px;padding:4px 8px;font-variant-numeric:tabular-nums}"
    ".rp .jumps button.on{background:#2563eb;border-color:#2563eb;color:#fff}"
    ".rp-panel{display:none}"
    ".rp-panel.on{display:block}"
    ".rp-panel h2{font-size:16px;margin:0 0 2px}"
    ".rp-panel p.round-meta{color:#64748b;font-size:12px;margin:2px 0 10px}"
    ".rp-card{background:#fff;border:1px solid #e2e8f0;border-radius:10px;"
    "padding:14px 16px;margin:12px 0}"
    ".rp-card .tag{font-size:11px;font-weight:700;color:#fff;border-radius:5px;"
    "padding:2px 8px;display:inline-block;vertical-align:middle}"
    ".rp-card .meta{color:#64748b;font-size:12px;margin:6px 0}"
    ".rp-card h3{font-size:14px;margin:8px 0 4px;color:#334155}"
    ".rp-card p{font-size:13px;margin:6px 0}"
    ".rp-card ul{font-size:13px;margin:6px 0;padding-left:18px}"
    ".rp-card pre.out{background:#0f172a;color:#e2e8f0;border-radius:8px;padding:10px;"
    "font-size:12px;overflow-x:auto;max-height:260px;overflow-y:auto}"
    ".ok{color:#16a34a;font-weight:600}.bad{color:#b91c1c;font-weight:600}"
    ".kbd{font-size:11px;color:#94a3b8}"
)

#: Fixed tag colors per replay entry type (never derived from the log).
REPLAY_TAG_COLORS: dict[str, str] = {
    "round_start": "#475569",
    "review_result": "#2563eb",
    "atomic_fix": "#16a34a",
    "architectural_fix": "#0d9488",
    "revert": "#b91c1c",
    "validation": "#ca8a04",
    "decision": "#7c3aed",
    "report": "#0f172a",
}

#: Replay page navigation script (static; only toggles CSS classes).
_REPLAY_SCRIPT = (
    "<script>(function(){"
    "var panels=document.querySelectorAll('.rp-panel');"
    "var jumps=document.querySelectorAll('.rp-jump');"
    "var prev=document.getElementById('rp-prev');"
    "var next=document.getElementById('rp-next');"
    "var num=document.getElementById('rp-num');"
    "var current=0,total=panels.length;"
    "function show(i){"
    "if(i<0||i>=total)return;current=i;"
    "for(var k=0;k<panels.length;k++){panels[k].classList.toggle('on',k===i);}"
    "for(var k=0;k<jumps.length;k++){jumps[k].classList.toggle('on',k===i);}"
    "prev.disabled=(i===0);next.disabled=(i===total-1);"
    "num.textContent='Round '+(i+1)+' / '+total;"
    "window.scrollTo(0,0);"
    "}"
    "prev.addEventListener('click',function(){show(current-1);});"
    "next.addEventListener('click',function(){show(current+1);});"
    "for(var k=0;k<jumps.length;k++){(function(k){"
    "jumps[k].addEventListener('click',function(){show(k);});})(k);}"
    "document.addEventListener('keydown',function(e){"
    "if(e.key==='ArrowLeft')show(current-1);"
    "if(e.key==='ArrowRight')show(current+1);});"
    "show(0);"
    "})();</script>"
)


def build_replay_page(entries: list[DecisionLogEntry]) -> str | None:
    """Render the whole decision log as an interactive round-by-round page.

    Returns ``None`` when the log is empty. Every round becomes one
    navigable panel; a final panel renders the full report (if present).
    """
    if not entries:
        return None
    groups = _group_entries_by_round(entries)
    panels = [
        _render_replay_round_panel(round_number, panel_entries)
        for round_number, panel_entries in groups
    ]
    report = latest_report_entry(entries)
    if report is not None:
        panels.append(_render_replay_report_panel(report))
    if not panels:
        return None
    first = entries[0]
    last = entries[-1]
    goal = "(none)"
    for entry in entries:
        data = entry.data if isinstance(entry.data, dict) else {}
        if data.get("goal"):
            goal = str(data["goal"])
            break
    title = "iterate replay"
    jump_buttons = "".join(
        f"<button class=\"rp-jump\" type=\"button\">{index + 1}</button>"
        for index in range(len(panels))
    )
    return (
        "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{title}</title>"
        f"<style>{_BASE_CSS}{_REPLAY_CSS}</style></head>"
        "<body><div class=\"wrap rp\">"
        f"<header><h1>{title}</h1>"
        f"<p class=\"sub\">goal: {_esc(goal)} · {_esc(first.timestamp[:19])} → "
        f"{_esc(last.timestamp[:19])} · {len(entries)} log entries</p></header>"
        "<div class=\"controls\">"
        "<button id=\"rp-prev\" type=\"button\">← prev</button>"
        "<span class=\"round-num\" id=\"rp-num\"></span>"
        "<button id=\"rp-next\" type=\"button\">next →</button>"
        "</div>"
        f"<div class=\"jumps\">{jump_buttons}</div>"
        + "".join(panels)
        + "<p class=\"kbd\">use ← / → arrow keys to move between rounds</p>"
        + _REPLAY_SCRIPT
        + "<footer>generated by iterate-harness · replay page · works offline</footer>"
        + "</div></body></html>"
    )


def _group_entries_by_round(
    entries: list[DecisionLogEntry],
) -> list[tuple[int, list[DecisionLogEntry]]]:
    """Group replay entries by round number; report entries are excluded.

    The report renders as its own dedicated final panel so reviewers get a
    clean full-report view after stepping through the rounds.
    """
    groups: dict[int, list[DecisionLogEntry]] = {}
    for entry in entries:
        if entry.type == "report":
            continue
        groups.setdefault(entry.round, []).append(entry)
    return [(round_number, groups[round_number]) for round_number in sorted(groups)]


def _render_replay_round_panel(
    round_number: int, entries: list[DecisionLogEntry]
) -> str:
    cards = "".join(_render_replay_entry_card(entry) for entry in entries)
    first_stamp = entries[0].timestamp[:19] if entries else ""
    return (
        "<section class=\"rp-panel\"><h2>Round "
        f"{_int_or_zero(round_number)}</h2>"
        f"<p class=\"round-meta\">{_esc(first_stamp)} · {len(entries)} log entries</p>"
        f"{cards}</section>"
    )


def _render_replay_entry_card(entry: DecisionLogEntry) -> str:
    """Render one decision-log entry as a replay card."""
    data = entry.data if isinstance(entry.data, dict) else {}
    color = REPLAY_TAG_COLORS.get(entry.type, "#475569")
    header = (
        f"<span class=\"tag\" style=\"background:{color}\">{_esc(entry.type)}</span>"
        f"<span class=\"meta\">&nbsp;&nbsp;{_esc(entry.timestamp[:19])}</span>"
    )
    body = _render_replay_card_body(entry.type, data)
    return f"<div class=\"rp-card\">{header}{body}</div>"


def _render_replay_card_body(entry_type: str, data: dict[str, Any]) -> str:
    """Type-specific body for one replay card (defensive on every field)."""
    if entry_type == "round_start":
        return _replay_card_text(
            [("goal", data.get("goal")), ("mode", data.get("mode")),
             ("dimensions", data.get("dimensions"))]
        )
    if entry_type == "review_result":
        return _replay_review_result(data)
    if entry_type in ("atomic_fix", "architectural_fix"):
        return _replay_fix_card(data)
    if entry_type == "revert":
        return _replay_card_text(
            [("file", data.get("file")), ("reason", data.get("reason")),
             ("summary", data.get("summary"))]
        )
    if entry_type == "validation":
        return _replay_validation_card(data)
    if entry_type == "decision":
        return _replay_card_text(
            [("action", data.get("action")), ("kind", data.get("kind")),
             ("detail", data.get("detail"))]
        )
    # Unknown entry type: fall back to a compact JSON preview.
    preview = _esc(data)[:400] if data else "(no payload)"
    return f"<p>{preview}</p>"


def _replay_card_text(pairs: list[tuple[str, object]]) -> str:
    """Render label/value pairs, skipping empty values."""
    parts = []
    for label, value in pairs:
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        parts.append(f"<p><strong>{_esc(label)}:</strong> {_esc(text)}</p>")
    return "".join(parts) if parts else "<p class=\"meta\">(no payload)</p>"


def _replay_review_result(data: dict[str, Any]) -> str:
    summary_line = (
        f"new: {_int_or_zero(data.get('newFindings'))} · "
        f"total: {_int_or_zero(data.get('totalFindings'))}"
    )
    converged = bool(data.get("converged"))
    status = "<span class=\"ok\">converged</span>" if converged else (
        "<span class=\"bad\">not converged</span>"
    )
    lines = [f"<p class=\"meta\">{summary_line} · {status}</p>"]
    raw = data.get("findings")
    findings = [f for f in raw if isinstance(f, dict)] if isinstance(raw, list) else []
    if findings:
        rows = []
        for finding in findings[:MAX_REPLAY_FINDINGS]:
            severity = str(finding.get("severity") or "?")
            color = _severity_color(severity)
            location = _esc(finding.get("file") or "(no file)")
            line_value = finding.get("line")
            if isinstance(line_value, int) and line_value > 0:
                location += f" · line {line_value}"
            rows.append(
                f"<tr><td><span class=\"badge\" style=\"background:{color}\">"
                f"{_esc(severity)}</span></td>"
                f"<td><code>{location}</code></td>"
                f"<td>{_esc(finding.get('dimension') or 'general')}</td>"
                f"<td>{_esc(finding.get('summary') or '')}</td></tr>"
            )
        lines.append(
            "<table><thead><tr><th>severity</th><th>location</th><th>dimension</th>"
            "<th>summary</th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
        )
        if len(findings) > MAX_REPLAY_FINDINGS:
            lines.append(
                f"<p class=\"meta\">… +{len(findings) - MAX_REPLAY_FINDINGS} "
                "more findings not shown</p>"
            )
    else:
        lines.append("<p class=\"meta\">no findings recorded</p>")
    return "".join(lines)


def _replay_fix_card(data: dict[str, Any]) -> str:
    text = _replay_card_text(
        [("file", data.get("file")), ("summary", data.get("summary")),
         ("description", data.get("description"))]
    )
    diff_raw = data.get("diff")
    if isinstance(diff_raw, str) and diff_raw.strip():
        lines = diff_raw.splitlines()
        if len(lines) > MAX_REPLAY_DIFF_LINES:
            truncated = "\n".join(lines[:MAX_REPLAY_DIFF_LINES])
            text += (
                "<pre class=\"diff\">"
                + _render_diff_body(truncated)
                + "</pre><p class=\"meta\">diff truncated "
                f"(+{len(lines) - MAX_REPLAY_DIFF_LINES} more lines)</p>"
            )
        else:
            text += "<pre class=\"diff\">" + _render_diff_body(diff_raw) + "</pre>"
    return text


def _replay_validation_card(data: dict[str, Any]) -> str:
    exit_code = data.get("exitCode")
    timed_out = bool(data.get("timedOut"))
    status_text = str(data.get("status") or "").strip()
    if exit_code is not None and not isinstance(exit_code, (int, str)):
        exit_code = None
    parts = [f"<p class=\"meta\">command: <code>{_esc(data.get('command') or '')}</code></p>"]
    if status_text:
        css = "ok" if status_text.lower() in ("ok", "success", "passed") else "bad"
        parts.append(f"<p>status: <span class=\"{css}\">{_esc(status_text)}</span>"
                     + (f" · exit: {_esc(exit_code)}" if exit_code is not None else "")
                     + (" · timed out" if timed_out else "") + "</p>")
    elif exit_code is not None:
        css = "ok" if str(exit_code) in ("0", "0.0") else "bad"
        parts.append(
            f"<p>exit: <span class=\"{css}\">{_esc(exit_code)}</span>"
            + (" · timed out" if timed_out else "") + "</p>"
        )
    for key in ("stdout", "stderr"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            body = value if len(value) <= 1200 else value[:1200] + "\n… (truncated)"
            parts.append(
                f"<p><strong>{_esc(key)}</strong></p>"
                f"<pre class=\"out\">{_esc(body)}</pre>"
            )
    return "".join(parts)


def _render_replay_report_panel(report: DecisionLogEntry) -> str:
    """Final panel: the full report rendered from the latest report entry."""
    data = report.data if isinstance(report.data, dict) else {}
    timeline = _collect_timeline(decision_log_entries_sentinel(report), report)
    body = (
        _render_header(data, report)
        + _render_summary_cards(data)
        + _render_convergence_chart(data)
        + _render_distribution(data)
        + _render_findings_table(data)
        + _render_fix_timeline(timeline)
    )
    return f"<section class=\"rp-panel\"><h2>Final report</h2>{body}</section>"


def decision_log_entries_sentinel(report: DecisionLogEntry) -> list[DecisionLogEntry]:
    """Placeholder for report-only rendering; see _render_replay_report_panel.

    The timeline for the replay page is intentionally minimal (the full
    report panel reuses the single-file renderers which need the log tail);
    callers pass the report entry alone so the timeline section shows the
    placeholder message instead of duplicating every round.
    """
    return [report]


__all__ = [
    "MAX_REPLAY_DIFF_LINES",
    "MAX_REPLAY_FINDINGS",
    "MAX_TIMELINE_ENTRIES",
    "REPLAY_ENTRY_TYPES",
    "REPLAY_TAG_COLORS",
    "SEVERITY_COLORS",
    "TIMELINE_TYPES",
    "build_html_report",
    "build_replay_page",
]
