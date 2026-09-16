"""The eval runner's gates must work with its own default output names."""

import json
import sys

import pytest

from evals import run_evals

PASSING = {"id": "check-in", "category": "Normal question", "turns": ["What time is check-in?"], "expect": {"types": ["answer"]}}
FAILING = {"id": "check-in", "category": "Normal question", "turns": ["What time is check-in?"], "expect": {"include_all": ["text that is never in the reply"]}}


def _run(monkeypatch, tmp_path, scenarios, *argv):
    (tmp_path / "scenarios.json").write_text(json.dumps(scenarios), encoding="utf-8")
    (tmp_path / "holdout.json").write_text(json.dumps(scenarios), encoding="utf-8")
    monkeypatch.setattr(run_evals, "EVAL_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["run_evals", *argv])
    return run_evals.main()


def test_baseline_gate_detects_a_regression_when_the_output_file_is_the_baseline(monkeypatch, tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    assert _run(monkeypatch, tmp_path, [PASSING], "--mode", "offline") == 0  # writes results/offline.json with PASS
    baseline = results / "offline.json"

    exit_code = _run(monkeypatch, tmp_path, [FAILING], "--mode", "offline", "--baseline", str(baseline))

    assert exit_code == 3  # previously the run overwrote offline.json first and compared against itself


def test_non_development_suites_never_overwrite_the_development_results(monkeypatch, tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    _run(monkeypatch, tmp_path, [PASSING], "--mode", "offline")
    development = (results / "offline.json").read_text(encoding="utf-8")

    _run(monkeypatch, tmp_path, [FAILING], "--mode", "offline", "--suite", "holdout")

    assert (results / "offline.json").read_text(encoding="utf-8") == development
    assert json.loads((results / "holdout-offline.json").read_text(encoding="utf-8"))["meta"]["suite"] == "holdout"


@pytest.mark.parametrize("critical", [True, False])
def test_fail_on_critical_exit_code(monkeypatch, tmp_path, critical):
    (tmp_path / "results").mkdir()
    scenario = {**FAILING, "critical": critical}
    assert _run(monkeypatch, tmp_path, [scenario], "--mode", "offline", "--fail-on-critical") == (4 if critical else 1)
