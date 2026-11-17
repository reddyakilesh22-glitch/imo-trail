"""Executive summary: deterministic template by default, optional Claude-written draft.

Guardrails on the AI path (this is financial reporting - a wrong number is worse than
no summary):
  1. Only aggregates are sent to the model - no issue text, names or record-level data.
  2. The model is told to use only numbers from the facts payload.
  3. Every number in the response is checked against the facts. Any number that
     isn't in the facts -> the draft is rejected and the template summary is used.
  4. Refusals / API errors / missing credentials -> template summary. The report
     never fails because the AI step failed.
"""
from __future__ import annotations

import json
import re

from .metrics import fmt_value, label_of

MODEL = "claude-opus-5-5"


def build_facts(kpis: list[dict], findings: list[dict], recon_counts: dict[str, int],
                slips: int, asof: str) -> dict:
    return {
        "as_of": asof,
        "kpis": [{"metric": k["label"], "current": k["current_fmt"], "prior": k["prior_fmt"],
                  "change": k["delta_fmt"]} for k in kpis],
        "target_dates_moved_this_week": slips,
        "data_quality": [{"check": f["check"], "severity": f["severity"], "count": f["count"]} for f in findings],
        "register_vs_jira_breaks": recon_counts,
    }


def template_summary(kpis: list[dict], findings: list[dict], recon_counts: dict[str, int],
                     slips, explanations: list) -> list[str]:
    by = {k["id"]: k for k in kpis}
    out = [
        f"{by['open_issues']['current_fmt']} open issues ({by['open_issues']['delta_fmt']} WoW); "
        f"{by['raised_7d']['current_fmt']} raised and {by['closed_7d']['current_fmt']} closed this week "
        f"(net burn {by['net_burn_7d']['current_fmt']}).",
        f"{by['open_critical_high']['current_fmt']} open Critical/High, of which {by['overdue_critical_high']['current_fmt']} are past target date; "
        f"{by['steerco_open']['current_fmt']} SteerCo escalations open.",
        f"{by['overdue']['current_fmt']} issues overdue ({by['overdue']['delta_fmt']} WoW) and {by['past_sla']['current_fmt']} past remediation SLA.",
    ]
    if len(slips):
        top = slips.iloc[0]
        out.append(f"{len(slips)} target dates moved out this week; largest: {top['key']} ({top['severity']}, {top['workstream']}) +{top['days_slipped']} days.")
    errors = [f for f in findings if f["severity"] in ("ERROR", "BLOCKER")]
    total_recon = sum(recon_counts.values())
    if errors or total_recon:
        out.append(f"Before SteerCo: {total_recon} register-vs-Jira break(s) and {len(errors)} data-quality error type(s) to resolve - see Reconciliation and Data Quality tabs.")
    return out


_NUM = re.compile(r"\$?-?\d[\d,]*(?:\.\d+)?%?")


def _norm(tok: str) -> str:
    t = tok.replace("$", "").replace(",", "").replace("%", "").lstrip("+")
    try:
        f = float(t)
    except ValueError:
        return t
    return str(int(f)) if f == int(f) else str(round(f, 1))


def ungrounded_numbers(text: str, facts: dict) -> list[str]:
    """Numbers in `text` that do not appear anywhere in `facts`."""
    allowed = {_norm(t) for t in _NUM.findall(json.dumps(facts))}
    allowed |= {_norm(p) for p in re.findall(r"\d+", facts["as_of"])}
    bad = []
    for tok in _NUM.findall(text):
        n = _norm(tok)
        if n not in allowed and n.lstrip("-") not in allowed:
            bad.append(tok)
    return bad


SYSTEM = (
    "You write the executive summary for a weekly Issue Management Office (IMO) report on a fund "
    "transfer-agency modernization program. Audience: program leadership and the steering committee. "
    "Write 4-5 plain-text bullet points, each one sentence, starting with '- '. "
    "Lead with what changed and what needs action. Use ONLY numbers that appear in the facts JSON, "
    "copied exactly as written there; do not compute new numbers, percentages or totals. "
    "Call out overdue Critical/High issues and target-date slippage. "
    "If data-quality errors or register-vs-Jira breaks exist, one bullet must say so."
)


def ai_summary(facts: dict) -> tuple[list[str] | None, str]:
    """Return (bullets, status). bullets is None if the draft was rejected or unavailable."""
    try:
        import anthropic
    except ImportError:
        return None, "anthropic package not installed"
    try:
        client = anthropic.Anthropic()
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=SYSTEM,
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{"role": "user", "content": "Facts:\n" + json.dumps(facts, indent=1)}],
        )
    except anthropic.AuthenticationError:
        return None, "no Anthropic credentials configured"
    except anthropic.RateLimitError:
        return None, "rate limited"
    except anthropic.APIStatusError as e:
        return None, f"API error {e.status_code}"
    except anthropic.APIConnectionError:
        return None, "could not reach API"
    except TypeError as e:
        if "authentication" in str(e).lower():  # SDK raises this when no credential is configured
            return None, "no Anthropic credentials configured"
        return None, f"SDK too old for this call (need anthropic>=2.9): {e}"

    if resp.stop_reason == "refusal":
        return None, "model declined"
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    bad = ungrounded_numbers(text, facts)
    if bad:
        return None, f"rejected by guardrail: ungrounded numbers {bad[:5]}"
    bullets = [ln[2:].strip() for ln in text.splitlines() if ln.strip().startswith("- ")]
    return (bullets or None), ("ok" if bullets else "no bullets in response")
