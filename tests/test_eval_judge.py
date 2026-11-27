"""B6: the offline brief-eval judge is a separate model from the writer, and the run records which one."""
from __future__ import annotations

from evals import run_brief_eval as ev
from hri.config.settings import Settings, settings
from hri.privacy.masker import Masker


def test_judge_model_defaults_to_opus_and_reads_env(monkeypatch):
    monkeypatch.delenv("EVAL_JUDGE_MODEL", raising=False)
    assert Settings(_env_file=None).eval_judge_model == "claude-opus-5-5"
    monkeypatch.setenv("EVAL_JUDGE_MODEL", "claude-judge-test")
    assert Settings(_env_file=None).eval_judge_model == "claude-judge-test"
    assert Settings(_env_file=None).eval_judge_model != Settings(_env_file=None).brief_model


def test_judge_calls_the_judge_model_not_the_writer(monkeypatch):
    from hri.llm import LLMResult
    calls = []

    def stub(**kw):
        calls.append(kw)
        return {"verdicts": [{"i": 0, "supported": True, "reason": "ok"}]}, LLMResult("{}", kw["model"], 1, 1, 0.0, 1)
    mk = Masker()
    mk.load_known_entities([{"name": "Guy Cameron", "org": "Graham Endowment"}])
    monkeypatch.setattr(mk, "flush", lambda: None)
    monkeypatch.setattr(ev, "masker", lambda: mk)
    monkeypatch.setattr(ev, "complete_json", stub)
    monkeypatch.setattr(ev.db, "q", lambda *a, **k: [])                       # no cited messages needed
    monkeypatch.setattr(settings(), "eval_judge_model", "claude-judge-test")
    out = ev.judge({"sections": {"who": [{"text": "Guy Cameron is an MD.", "cites": ["PROFILE"]}]},
                    "profile_source": "Name: Guy Cameron"})
    assert out == {"claims": 1, "unsupported": [], "judge_cost_usd": 0.0}
    assert calls[0]["model"] == "claude-judge-test" != settings().brief_model
    assert "Guy Cameron" not in calls[0]["user"]                               # still masked


def test_baseline_run_is_preserved():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    before = json.loads((root / ev.BEFORE_FIXES).read_text())
    assert before["summary"]["run_at"].startswith("2026-11-25")
