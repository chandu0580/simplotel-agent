"""Scenario-based evaluation of the chat service.

Usage (from backend/):
    python -m evals.run_evals --mode offline   # deterministic, free
    python -m evals.run_evals --mode ai        # live Claude calls; needs ANTHROPIC_API_KEY, costs tokens

Each scenario plays one or more guest turns through ChatService (the same code the
API uses) and checks the final reply against expectations. Results are printed and
written to evals/results/<mode>.md.
"""

import argparse
from datetime import date, timedelta
import json
import logging
from pathlib import Path
import re
import sys
import time

import anthropic
import httpx

from app.availability import hotel_today
from app.claude_assistant import ClaudeAssistant, build_claude_assistant
from app.config import get_settings
from app.knowledge import get_knowledge_base
from app.offline import OfflineAssistant
from app.schemas import BookingContext, ChatHistoryItem, ChatRequest, ChatResponse
from app.service import ChatService

EVAL_DIR = Path(__file__).parent


class FailingMessages:
    def create(self, **_kwargs):
        raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))


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


def run_conversation(service: ChatService, turns: list[str], today: date) -> ChatResponse:
    history: list[ChatHistoryItem] = []
    context: BookingContext | None = None
    response = None
    for i, turn in enumerate(turns):
        response = service.handle(ChatRequest(message=turn, history=history, booking_context=context), f"eval-{i}", today)
        history += [ChatHistoryItem(role="user", content=turn), ChatHistoryItem(role="assistant", content=response.reply.text)]
        if (result := response.reply.availability) is not None:
            context = BookingContext(check_in=result.check_in, check_out=result.check_out, adults=result.adults, children=result.children)
        elif (prefill := response.reply.booking_prefill) is not None:
            known = {k: v for k, v in prefill.model_dump().items() if v is not None}
            context = BookingContext(**{**(context.model_dump() if context else {}), **known})
    return response


def check(response: ChatResponse, expect: dict) -> list[str]:
    failures = []
    reply = response.reply
    text = reply.text + (" " + reply.availability.message if reply.availability else "")
    lowered = text.lower()

    if "mode" in expect and response.mode != expect["mode"]:
        failures.append(f"mode={response.mode}, expected {expect['mode']}")
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
    if expect.get("sources_any"):
        ids = {s.id for s in reply.sources}
        if not ids & set(expect["sources_any"]):
            failures.append(f"sources {sorted(ids)} lack any of {expect['sources_any']}")
    if expect.get("form_error") and not reply.form_error:
        failures.append("expected a form_error")
    if (avail := expect.get("availability")) is not None:
        if reply.availability is None:
            failures.append("no availability result")
        else:
            actual = reply.availability.model_dump(mode="json")
            for key, value in avail.items():
                if actual.get(key) != value:
                    failures.append(f"availability.{key}={actual.get(key)!r}, expected {value!r}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["offline", "ai"], default="offline")
    parser.add_argument("--only", help="regex filter on scenario id")
    parser.add_argument(
        "--label",
        help="results file name (default: the mode). Use a distinct label when --mode ai is pointed at a non-Anthropic "
        "endpoint via ANTHROPIC_BASE_URL, so those results can't be mistaken for Claude results.",
    )
    parser.add_argument("--provider-note", default="", help="note written at the top of the results file")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    settings = get_settings()
    kb = get_knowledge_base()
    offline = OfflineAssistant(kb)
    if args.mode == "ai":
        if not settings.anthropic_api_key:
            print("ANTHROPIC_API_KEY is not set (backend/.env); cannot run --mode ai", file=sys.stderr)
            return 2
        ai = build_claude_assistant(kb, settings.anthropic_api_key, settings.model, settings.effort, settings.llm_timeout_seconds, settings.llm_max_retries, settings.refusal_fallback)
        normal_service = ChatService(offline=offline, ai=ai)
    else:
        normal_service = ChatService(offline=offline, ai=None)
    failing_service = ChatService(offline=offline, ai=ClaudeAssistant(kb, FailingMessages(), model=settings.model, effort=settings.effort))

    today = hotel_today()
    variables = date_vars(today)
    scenarios = json.loads((EVAL_DIR / "scenarios.json").read_text(encoding="utf-8"))

    rows = []
    for scenario in scenarios:
        if args.only and not re.search(args.only, scenario["id"]):
            continue
        if scenario.get("ai_only") and args.mode != "ai":
            rows.append({"id": scenario["id"], "category": scenario["category"], "status": "SKIP", "latency_ms": 0, "type": "-", "reply": "AI-only scenario", "failures": []})
            continue
        service = failing_service if scenario.get("simulate_llm_failure") else normal_service
        turns = fill(scenario["turns"], variables)
        started = time.perf_counter()
        response = run_conversation(service, turns, today)
        latency = int((time.perf_counter() - started) * 1000)
        failures = check(response, fill(scenario["expect"], variables))
        # The offline fallback can satisfy many expectations on its own; in AI mode that must not
        # count as a pass, or a broken model integration would look healthy.
        if args.mode == "ai" and not scenario.get("simulate_llm_failure") and response.mode != "ai":
            failures.insert(0, "model call failed; answered by offline fallback")
        rows.append(
            {
                "id": scenario["id"],
                "category": scenario["category"],
                "status": "PASS" if not failures else "FAIL",
                "latency_ms": latency,
                "mode": response.mode,
                "type": response.reply.type,
                "input": turns,
                "reply": response.reply.text,
                "sources": [s.id for s in response.reply.sources],
                "failures": failures,
            }
        )
        print(f"{rows[-1]['status']:4}  {scenario['id']:<32} {latency:>6} ms  {'; '.join(failures)}")

    ran = [r for r in rows if r["status"] != "SKIP"]
    passed = sum(r["status"] == "PASS" for r in ran)
    print(f"\n{passed}/{len(ran)} passed ({len(rows) - len(ran)} skipped) in mode={args.mode}")

    out_dir = EVAL_DIR / "results"
    out_dir.mkdir(exist_ok=True)
    label = args.label or args.mode
    (out_dir / f"{label}.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    header = f"# Eval results — mode `{args.mode}` — run on {today.isoformat()}"
    if args.mode == "ai":
        header += f" — model `{settings.model}`"
    lines = [header, ""]
    if args.provider_note:
        lines += [f"> {args.provider_note}", ""]
    lines += [f"**{passed}/{len(ran)} passed**, {len(rows) - len(ran)} skipped", "",
              "| Scenario | Category | Result | Served by | Reply type | Latency | Notes |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        notes = "; ".join(r["failures"]) or r["reply"].replace("\n", " ")[:110]
        lines.append(f"| `{r['id']}` | {r['category']} | {r['status']} | {r.get('mode', '-')} | {r['type']} | {r['latency_ms']} ms | {notes.replace('|', '/')} |")
    (out_dir / f"{label}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if passed == len(ran) else 1


if __name__ == "__main__":
    sys.exit(main())
