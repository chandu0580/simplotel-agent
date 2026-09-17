"""AI evaluation runner.

    python -m evals.run_evals --mode offline                       # deterministic engine, free
    python -m evals.run_evals --mode ai --label <label> --provider-note "..."   # live model from LLM_PROVIDER (costs tokens)
    python -m evals.run_evals --mode offline --baseline evals/results/offline.json   # fail on regressions
    python -m evals.run_evals --suite holdout --fail-on-critical   # adversarial holdout set; fail if any critical scenario fails

Suites: `development` (evals/scenarios.json) was used while writing prompts and guardrails. `holdout`
(evals/holdout.json) was written afterwards and must never be used to tune prompts; its purpose is to
estimate behaviour on attacks nobody optimised for. Scenarios are "critical" when a failure is a safety,
injection or tenant-isolation problem (explicit "critical" field, or a safety/prompt_injection tag or the
Multi-tenant category).

Each scenario plays one or more guest turns through `ConversationService`, the same code path
as the v1 API, with server-side context. Checks are structured wherever possible:
  * reply type, availability fields, cited sources            (from the API response)
  * decision taken (answer / which tool / blocked), tool args  (from the AI trace)
  * evidence, guardrail interventions, input flags, latency    (from the AI trace)
Text checks (include/exclude) are used only for facts with no structured form.

Output: evals/results/<label>.json and .md, with per-category pass rates, quality metrics
(groundedness, decision accuracy, fallback correctness, guardrail interventions), latency
percentiles and the prompt/tool/knowledge versions under test.
"""

import argparse
from dataclasses import asdict
from datetime import date, timedelta
import json
import logging
from pathlib import Path
import re
import statistics
import sys
import time

from app.container import Container, build_container
from app.core.clock import local_today
from app.core.config import Settings
from app.core.tracing import AITrace
from app.llm.provider import LLMProviderError
from app.llm.scripted_provider import ScriptedLLMProvider
from app.tenancy import Channel

EVAL_DIR = Path(__file__).parent
DEFAULT_HOTEL = "hotel-goa-001"
SUITES = {"development": "scenarios.json", "holdout": "holdout.json"}
CRITICAL_TAGS = {"safety", "prompt_injection"}
CATEGORY_TAGS = {
    "Normal question": ["functional", "grounding"],
    "Normal question (room reasoning)": ["functional", "grounding"],
    "Ambiguous question": ["functional", "conversation"],
    "Ambiguous wording": ["functional", "regression"],
    "Missing information": ["tool_calling", "conversation"],
    "Availability / tool call": ["tool_calling"],
    "Incorrect input": ["tool_calling", "functional"],
    "Incorrect assumption": ["grounding"],
    "Unsupported question": ["grounding", "safety"],
    "Unsupported request": ["grounding", "safety"],
    "Conversation follow-up": ["conversation"],
    "Conversation follow-up (relative)": ["conversation", "tool_calling"],
    "Safety / grounding": ["safety", "prompt_injection"],
    "Prompt injection": ["safety", "prompt_injection"],
    "Model failure": ["regression", "safety"],
    "Multi-tenant": ["grounding", "regression"],
    "Conversational": ["conversational", "functional"],
}


class _FailingProvider(ScriptedLLMProvider):
    name = "failing"

    def generate_with_tools(self, request):
        raise LLMProviderError("connection", "simulated outage")


def date_vars(today: date) -> dict[str, str]:
    wed = today + timedelta(days=14)
    while wed.weekday() != 2:
        wed += timedelta(days=1)
    sat = wed + timedelta(days=3)
    return {
        "wed": wed.isoformat(),
        "fri": (wed + timedelta(days=2)).isoformat(),
        "sat": sat.isoformat(),
        "sun": (sat + timedelta(days=1)).isoformat(),
        "wed_human": f"{wed:%A} {wed.day} {wed:%B %Y}",
    }


def fill(value, variables):
    if isinstance(value, str):
        return value.format(**variables)
    if isinstance(value, list):
        return [fill(v, variables) for v in value]
    if isinstance(value, dict):
        return {k: fill(v, variables) for k, v in value.items()}
    return value


def decision_of(trace: AITrace) -> str:
    if trace.mode == "guardrail":
        return "blocked"
    model_calls = [c for c in trace.tool_calls if c.invoked_by == "model"]
    if model_calls:
        return model_calls[0].name
    return "answer_guest" if trace.mode == "ai" else "offline"


def run_conversation(container: Container, hotel_id: str, turns: list[str]):
    ctx = container.tenants.resolve(hotel_id, Channel.WEB, "eval", "eval")
    conversation = container.conversations.start(ctx)
    outcome = None
    for turn in turns:
        _, outcome = container.conversations.post_message(ctx, conversation.id, turn)
    return outcome


def check(outcome, expect: dict, mode: str) -> tuple[list[str], dict]:
    failures: list[str] = []
    reply, trace = outcome.reply, outcome.trace
    text = reply.text + (" " + reply.availability.message if reply.availability else "")
    lowered = text.lower()
    observed = {"decision": decision_of(trace)}

    if "mode" in expect and outcome.mode != expect["mode"]:
        failures.append(f"mode={outcome.mode}, expected {expect['mode']}")
    if "types" in expect and reply.type not in expect["types"]:
        failures.append(f"type={reply.type}, expected one of {expect['types']}")
    for needle in expect.get("include_all", []):
        if needle.lower() not in lowered:
            failures.append(f"missing text {needle!r}")
    if expect.get("include_any") and not any(n.lower() in lowered for n in expect["include_any"]):
        failures.append(f"none of {expect['include_any']} in reply")
    for needle in expect.get("exclude", []):
        if needle.lower() in lowered:
            failures.append(f"forbidden text {needle!r} present")
    if expect.get("sources_any") and not {s.id for s in reply.sources} & set(expect["sources_any"]):
        failures.append(f"sources {sorted(s.id for s in reply.sources)} lack any of {expect['sources_any']}")
    if expect.get("form_error") and not reply.form_error:
        failures.append("expected a form_error")
    if (avail := expect.get("availability")) is not None:
        if reply.availability is None:
            failures.append("no availability result")
        else:
            actual = reply.availability.model_dump(mode="json")
            failures += [f"availability.{k}={actual.get(k)!r}, expected {v!r}" for k, v in avail.items() if actual.get(k) != v]
    if expect.get("guardrails_any") and not set(trace.guardrails + trace.input_flags) & set(expect["guardrails_any"]):
        failures.append(f"none of guardrails {expect['guardrails_any']} triggered (got {trace.guardrails + trace.input_flags})")
    if expect.get("no_model_call") and trace.llm_latency_ms is not None:
        failures.append("model was called but the request should have been blocked first")
    # Decision / tool-call correctness is only meaningful when a model made the decision.
    if mode == "ai" and (decisions := expect.get("decision_any")):
        if observed["decision"] not in decisions:
            failures.append(f"decision={observed['decision']}, expected one of {decisions}")
    if mode == "ai" and (args := expect.get("tool_args")):
        call = next((c for c in trace.tool_calls if c.invoked_by == "model" and c.arguments), None)
        actual_args = call.arguments if call else {}
        failures += [f"tool arg {k}={actual_args.get(k)!r}, expected {v!r}" for k, v in args.items() if actual_args.get(k) != v]
    if (limit := expect.get("max_latency_ms")) and trace.total_latency_ms and trace.total_latency_ms > limit:
        failures.append(f"latency {trace.total_latency_ms} ms > {limit} ms")
    return failures, observed


def percentile(values: list[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def summarise(rows: list[dict], mode: str) -> dict:
    ran = [r for r in rows if r["status"] != "SKIP"]
    by_tag: dict[str, list[bool]] = {}
    for r in ran:
        for tag in r["tags"]:
            by_tag.setdefault(tag, []).append(r["status"] == "PASS")
    answers = [r for r in ran if r["type"] == "answer"]
    grounded = [r for r in answers if r["cited"] and set(r["cited"]) <= set(r["evidence"])]
    ai_turns = [r for r in ran if r["served_by"] == "ai"]
    hallucination_guards = {"uncited_answer", "unsupported_price", "availability_claim", "unknown_source", "secret_leak", "prompt_leak"}
    intervened = [r for r in ran if set(r["guardrails"]) & hallucination_guards]
    decision_rows = [r for r in ran if r.get("decision_checked")]
    fallback_rows = [r for r in ran if "fallback" in r["expected_types"] and len(r["expected_types"]) == 1]
    latencies = [r["latency_ms"] for r in ran if r["served_by"] == mode or mode == "offline"]
    return {
        "scenarios": len(rows),
        "ran": len(ran),
        "passed": sum(r["status"] == "PASS" for r in ran),
        "skipped": len(rows) - len(ran),
        "pass_rate_by_tag": {tag: f"{sum(v)}/{len(v)}" for tag, v in sorted(by_tag.items())},
        "groundedness": f"{len(grounded)}/{len(answers)} answers cite only retrieved evidence",
        "decision_accuracy": f"{sum(r['status'] == 'PASS' for r in decision_rows)}/{len(decision_rows)}" if mode == "ai" else "n/a (no model decisions offline)",
        "fallback_correctness": f"{sum(r['type'] == 'fallback' for r in fallback_rows)}/{len(fallback_rows)} unsupported questions got a fallback",
        "guardrail_interventions": f"{len(intervened)}/{len(ran)} turns (hallucination/leak guards)",
        "served_by_ai": f"{len(ai_turns)}/{len(ran)}",
        "critical_passed": f"{sum(r['status'] == 'PASS' for r in ran if r.get('critical'))}/{sum(1 for r in ran if r.get('critical'))}",
        "latency_ms_p50": percentile(latencies, 0.5),
        "latency_ms_p95": percentile(latencies, 0.95),
        "latency_ms_mean": int(statistics.mean(latencies)) if latencies else None,
    }


def is_critical(scenario: dict, tags: list[str]) -> bool:
    if "critical" in scenario:
        return bool(scenario["critical"])
    return bool(CRITICAL_TAGS & set(tags)) or scenario["category"] == "Multi-tenant"


def load_baseline(baseline_path: Path) -> dict[str, str]:
    raw = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_rows = raw["rows"] if isinstance(raw, dict) else raw
    return {r["id"]: r["status"] for r in baseline_rows}


def compare_with_baseline(rows: list[dict], before: dict[str, str]) -> list[str]:
    return [r["id"] for r in rows if before.get(r["id"]) == "PASS" and r["status"] == "FAIL"]


def build(mode: str) -> tuple[Container, Container]:
    if mode == "ai":
        settings = Settings.from_env()
        if not settings.llm_configured:
            raise SystemExit("--mode ai needs a configured provider: LLM_PROVIDER=glm with LLM_API_KEY and LLM_BASE_URL, or LLM_PROVIDER=anthropic with ANTHROPIC_API_KEY")
    else:
        settings = Settings.for_tests(log_level="ERROR")
    failing = build_container(Settings.for_tests(log_level="CRITICAL"), llm_provider=_FailingProvider())
    return build_container(settings), failing


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["offline", "ai"], default="offline")
    parser.add_argument("--only", help="regex filter on scenario id")
    parser.add_argument("--tag", help="only run scenarios with this tag")
    parser.add_argument("--label", help="results file name (default: the mode, or <suite>-<mode> for non-development suites)")
    parser.add_argument("--provider-note", default="", help="note written at the top of the results file")
    parser.add_argument("--baseline", type=Path, help="previous results .json; exit 3 if a previously passing scenario now fails")
    parser.add_argument("--suite", choices=sorted(SUITES), default="development")
    parser.add_argument("--fail-on-critical", action="store_true", help="exit 4 if any critical scenario fails")
    args = parser.parse_args()
    # Read the baseline before anything is written: the default output file can be the baseline itself.
    baseline = load_baseline(args.baseline) if args.baseline else None

    container, failing_container = build(args.mode)
    logging.getLogger().setLevel(logging.ERROR)
    today = local_today(container.clock, container.knowledge.profile(DEFAULT_HOTEL).timezone)
    variables = date_vars(today)
    scenarios = json.loads((EVAL_DIR / SUITES[args.suite]).read_text(encoding="utf-8"))

    rows = []
    for scenario in scenarios:
        tags = scenario.get("tags") or CATEGORY_TAGS.get(scenario["category"], ["functional"])
        if (args.only and not re.search(args.only, scenario["id"])) or (args.tag and args.tag not in tags):
            continue
        base = {"id": scenario["id"], "category": scenario["category"], "tags": tags, "critical": is_critical(scenario, tags)}
        if scenario.get("ai_only") and args.mode != "ai":
            rows.append({**base, "status": "SKIP", "latency_ms": 0, "type": "-", "served_by": "-", "reply": "AI-only scenario", "failures": [],
                         "evidence": [], "cited": [], "guardrails": [], "expected_types": []})
            continue
        target = failing_container if scenario.get("simulate_llm_failure") else container
        turns = fill(scenario["turns"], variables)
        expect = fill(scenario["expect"], variables)
        started = time.perf_counter()
        outcome = run_conversation(target, scenario.get("hotel_id", DEFAULT_HOTEL), turns)
        latency = int((time.perf_counter() - started) * 1000)
        failures, observed = check(outcome, expect, args.mode)
        if args.mode == "ai" and not scenario.get("simulate_llm_failure") and outcome.mode != "ai":
            failures.insert(0, "model call failed; answered by offline fallback")
        trace = outcome.trace
        rows.append(
            {
                **base,
                "status": "PASS" if not failures else "FAIL",
                "latency_ms": latency,
                "served_by": outcome.mode,
                "type": outcome.reply.type,
                "decision": observed["decision"],
                "decision_checked": args.mode == "ai" and bool(expect.get("decision_any")),
                "input": turns,
                "reply": outcome.reply.text,
                "sources": [s.id for s in outcome.reply.sources],
                "evidence": trace.evidence_ids,
                "cited": trace.cited_ids,
                "guardrails": trace.guardrails + trace.input_flags,
                "tool_calls": [asdict(c) for c in trace.tool_calls],
                "expected_types": expect.get("types", []),
                "versions": {"prompt": trace.prompt_version, "tool_schema": trace.tool_schema_version, "knowledge": trace.knowledge_version, "model": trace.model},
                "failures": failures,
            }
        )
        print(f"{rows[-1]['status']:4}  {scenario['id']:<36} {latency:>6} ms  {'; '.join(failures)}")

    summary = summarise(rows, args.mode)
    print(f"\n{summary['passed']}/{summary['ran']} passed ({summary['skipped']} skipped) in mode={args.mode}")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("scenarios", "ran", "passed", "skipped")}, indent=2))

    label = args.label or (args.mode if args.suite == "development" else f"{args.suite}-{args.mode}")
    versions = sorted({json.dumps(r["versions"], sort_keys=True) for r in rows if r.get("versions")})
    meta = {"mode": args.mode, "suite": args.suite, "label": label, "run_date": today.isoformat(), "provider_note": args.provider_note, "versions": [json.loads(v) for v in versions]}
    out_dir = EVAL_DIR / "results"
    out_dir.mkdir(exist_ok=True)
    (out_dir / f"{label}.json").write_text(json.dumps({"meta": meta, "summary": summary, "rows": rows}, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [f"# Eval results — `{label}` (mode `{args.mode}`, suite `{args.suite}`) — run on {today.isoformat()}", ""]
    if args.provider_note:
        lines += [f"> {args.provider_note}", ""]
    lines += [f"**{summary['passed']}/{summary['ran']} passed**, {summary['skipped']} skipped", "", "## Quality metrics", "", "| Metric | Value |", "|---|---|"]
    for key in ("critical_passed", "groundedness", "decision_accuracy", "fallback_correctness", "guardrail_interventions", "served_by_ai", "latency_ms_p50", "latency_ms_p95"):
        lines.append(f"| {key} | {summary[key]} |")
    lines += ["", "## Pass rate by category", "", "| Tag | Passed |", "|---|---|"] + [f"| {t} | {v} |" for t, v in summary["pass_rate_by_tag"].items()]
    lines += ["", "## Versions under test", ""] + [f"- `{json.dumps(v)}`" for v in meta["versions"]]
    lines += ["", "## Scenarios", "", "| Scenario | Tags | Result | Served by | Decision | Reply type | Latency | Notes |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        notes = "; ".join(r["failures"]) or r["reply"].replace("\n", " ")[:110]
        lines.append(f"| `{r['id']}` | {', '.join(r['tags'])} | {r['status']} | {r['served_by']} | {r.get('decision', '-')} | {r['type']} | {r['latency_ms']} ms | {notes.replace('|', '/')} |")
    (out_dir / f"{label}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    if baseline is not None:
        regressions = compare_with_baseline(rows, baseline)
        if regressions:
            critical = [r["id"] for r in rows if r["id"] in regressions and r.get("critical")]
            print(f"REGRESSIONS vs {args.baseline}: {regressions} (critical: {critical})")
            return 3
        print(f"No regressions vs {args.baseline}")
    if args.fail_on_critical:
        failed_critical = [r["id"] for r in rows if r.get("critical") and r["status"] == "FAIL"]
        if failed_critical:
            print(f"CRITICAL SCENARIOS FAILED: {failed_critical}")
            return 4
    return 0 if summary["passed"] == summary["ran"] else 1


if __name__ == "__main__":
    sys.exit(main())
