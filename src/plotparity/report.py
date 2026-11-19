"""report.json (machine) and report.md (agent-facing)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_PER_CASE = 12


def exit_code(cases: list[dict]) -> int:
    st = {c["status"] for c in cases}
    if "fail" in st:
        return 1
    if st & {"unmeasured", "infrastructure_error"}:
        return 2
    return 0


def tag_against_previous(report: dict, previous: dict) -> None:
    """new / persisting / fixed. `fixed` only when the component is still matched and the check still ran
    (i.e. the case is measured and has no inventory failure for that component); otherwise `gone`."""
    prev = {f["fingerprint"]: f for c in previous.get("cases", []) for f in c["findings"] if f["status"] == "fail"}
    now = {f["fingerprint"] for c in report["cases"] for f in c["findings"] if f["status"] == "fail"}
    for c in report["cases"]:
        for f in c["findings"]:
            if f["status"] == "fail":
                f["change"] = "persisting" if f["fingerprint"] in prev else "new"
    by_case = {c["case"]: c for c in report["cases"]}
    resolved = []
    for fp, f in prev.items():
        if fp in now:
            continue
        c = by_case.get(f["case"])
        if c is None:
            continue
        comp_gone = c["status"] not in ("pass", "fail") or any(
            g["tier"] == "inventory" and g["status"] == "fail" for g in c["findings"])
        resolved.append({"fingerprint": fp, "case": f["case"], "check": f["check"], "component": f["component"],
                         "change": "gone" if comp_gone else "fixed"})
    report["resolved_since_previous"] = resolved


def _fmt(v: Any) -> str:
    s = json.dumps(v) if isinstance(v, (dict, list)) else (f"{v:.2f}" if isinstance(v, float) else str(v))
    return s if len(s) < 90 else s[:87] + "..."


def to_markdown(report: dict, show_findings: bool = True) -> str:
    cases = report["cases"]
    n = {s: sum(c["status"] == s for c in cases) for s in ("pass", "fail", "unmeasured", "infrastructure_error")}
    head = (f"PARITY: {report['status'].upper()}  ({n['pass']}/{len(cases)} pass, {n['fail']} fail, "
            f"{n['unmeasured']} unmeasured, {n['infrastructure_error']} infrastructure error)")
    lines = [head]
    if report.get("resolved_since_previous"):
        fixed = sum(r["change"] == "fixed" for r in report["resolved_since_previous"])
        lines.append(f"since previous run: {fixed} fixed, "
                     f"{sum(r['change'] == 'gone' for r in report['resolved_since_previous'])} gone (component/case no longer measured)")
    if not show_findings:
        return "\n".join(lines + [f"- {c['case']}: {c['status'].upper()}" for c in cases]) + "\n"
    lines.append("Fix inventory findings first, then layout, then style: later findings are often consequences.")
    for c in cases:
        fails = [f for f in c["findings"] if f["status"] != "known_difference"]
        lines += ["", f"## {c.get('relpath', c['case'])} [{c['split'] or 'no split'}]: {c['status'].upper()}"
                  + (f" ({len(fails)} findings)" if fails else "")]
        if c.get("reference_png"):
            lines.append(f"   R reference: {c['reference_png']}")
        if c.get("candidate_png"):
            lines.append(f"   your render: {c['candidate_png']}")
        for i, f in enumerate(fails[:MAX_PER_CASE], 1):
            tag = f" ({f['change']})" if f.get("change") else ""
            sev = "" if f["severity"] == "gating" else f" [{f['severity']}]"
            st = "" if f["status"] == "fail" else f" [{f['status']}]"
            lines.append(f"{i:>3}. [{f['tier']}] {f['component']} :: {f['check']}{st}{sev}{tag}  "
                         f"expected {_fmt(f['expected'])} → observed {_fmt(f['observed'])}")
            lines.append(f"       {f['message']}")
            if f.get("traceback"):
                lines.append("       " + f["traceback"].strip().replace("\n", "\n       "))
        if len(fails) > MAX_PER_CASE:
            lines.append(f"     … {len(fails) - MAX_PER_CASE} more in report.json")
        for f in c["findings"]:
            if f["status"] == "known_difference":
                lines.append(f"   (known difference, not gating) {f['component']} :: {f['check']}: {f['known_reason'][:110]}")
    return "\n".join(lines) + "\n"


def write(report: dict, out: Path, show_findings: bool = True) -> str:
    out.mkdir(parents=True, exist_ok=True)
    report["schema_version"] = SCHEMA_VERSION
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str))
    md = to_markdown(report, show_findings)
    (out / "report.md").write_text(md)
    return md
