from __future__ import annotations

from pathlib import Path

from tvboxgen.config import load_config
from tvboxgen.models import ProbeResult
from tvboxgen.streak import (
    load_streaks,
    save_streaks,
    should_skip_probe,
    update_streaks_from_results,
)


def test_skip_after_three_fails(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "config.toml").write_text(
        """
[build]
fail_streak_limit = 3
""",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    streaks = {"a": 3, "b": 2}
    save_streaks(cfg, streaks)
    loaded = load_streaks(cfg)
    assert should_skip_probe("a", loaded, cfg.fail_streak_limit)
    assert not should_skip_probe("b", loaded, cfg.fail_streak_limit)


def test_update_streaks_reset_on_ok() -> None:
    streaks = {"a": 2, "b": 1}
    results = [
        ProbeResult(key="a", status="ok"),
        ProbeResult(key="b", status="fail"),
        ProbeResult(key="c", status="fail"),
        ProbeResult(key="d", status="skipped", error="fail_streak>=3"),
    ]
    out = update_streaks_from_results(streaks, results)
    assert out["a"] == 0
    assert out["b"] == 2
    assert out["c"] == 1
    assert out.get("d", 0) == streaks.get("d", 0)  # unchanged / absent
