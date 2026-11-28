"""诊断日志：不可用 / 重复合并。"""

from __future__ import annotations

from pathlib import Path

from tvboxgen.build import dumps
from tvboxgen.config import load_config
from tvboxgen.diag import log_unavailable
from tvboxgen.promote import promote_trusted
from tvboxgen.sync import _dedupe_rows


def test_log_unavailable_accepts_probe_level_kwarg(monkeypatch) -> None:
    """probe 结果的 level（L0/L1…）不得与 log_kv(level=…) 参数冲突。"""
    seen: list[tuple[str, dict]] = []

    def capture(logger, level, msg, **kv):
        seen.append((msg, kv))

    monkeypatch.setattr("tvboxgen.diag.log_kv", capture)
    log_unavailable(
        stage="probe",
        kind="site",
        key="4K_49d9",
        reason="timeout",
        status="fail",
        level="L0",
        latency_ms=12,
    )
    assert len(seen) == 1
    msg, kv = seen[0]
    assert msg == "source_unavailable"
    assert "level" not in kv
    assert kv.get("probe_level") == "L0"
    assert kv.get("status") == "fail"


def test_dedupe_rows_logs_duplicate(monkeypatch) -> None:
    seen: list[tuple[str, dict]] = []

    def capture(logger, level, msg, **kv):
        seen.append((msg, kv))

    monkeypatch.setattr("tvboxgen.diag.log_kv", capture)
    rows = [
        {
            "key": "a",
            "name": "甲",
            "api": "https://a.example.com/api.php/provide/vod",
            "origin": "upstream:u1",
        },
        {
            "key": "b",
            "name": "乙",
            "api": "https://a.example.com/api.php/provide/vod",
            "origin": "upstream:u2",
        },
    ]
    out = _dedupe_rows(rows, kind="site")
    assert len(out) == 1
    assert any(msg == "source_duplicate" and kv.get("drop_key") == "b" for msg, kv in seen)


def test_promote_logs_unavailable(tmp_path: Path, monkeypatch) -> None:
    seen: list[tuple[str, dict]] = []

    def capture(logger, level, msg, **kv):
        seen.append((msg, kv))

    monkeypatch.setattr("tvboxgen.diag.log_kv", capture)
    monkeypatch.setattr("tvboxgen.promote.log_kv", lambda *a, **k: None)

    pending = tmp_path / "sources" / "pending"
    manual = tmp_path / "sources" / "manual"
    trusted = tmp_path / "sources" / "trusted"
    for d in (pending, manual, trusted, tmp_path / "data"):
        d.mkdir(parents=True)
    (pending / "sites.json").write_text(
        dumps(
            [
                {
                    "key": "bad",
                    "name": "坏站",
                    "api": "https://bad.example.com/api.php/provide/vod",
                    "type": 1,
                    "searchable": 1,
                    "quickSearch": 1,
                    "filterable": 1,
                }
            ]
        ),
        encoding="utf-8",
    )
    (pending / "lives.json").write_text("[]\n", encoding="utf-8")
    (manual / "sites.json").write_text("[]\n", encoding="utf-8")
    (manual / "lives.json").write_text("[]\n", encoding="utf-8")
    (manual / "subscriptions.json").write_text("[]\n", encoding="utf-8")
    (trusted / "sites.json").write_text("[]\n", encoding="utf-8")
    (trusted / "lives.json").write_text("[]\n", encoding="utf-8")
    (tmp_path / "data" / "probe_report.json").write_text(
        dumps(
            {
                "generated_at": "2026-11-28T00:00:00Z",
                "runner": "test",
                "summary": {"ok": 0, "fail": 1, "total": 1},
                "sites": [
                    {
                        "key": "bad",
                        "status": "fail",
                        "level": "L0",
                        "latency_ms": 10,
                        "score": 0,
                        "http_code": None,
                        "checks": {
                            "reachable": False,
                            "json_ok": False,
                            "shape_ok": False,
                            "search_ok": False,
                        },
                        "error": "down",
                        "checked_at": "2026-11-28T00:00:01Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "config.toml").write_text(
        """
[sources]
trusted_dir = "sources/trusted"
pending_dir = "sources/pending"
manual_dir = "sources/manual"
[build]
accept_status = ["ok"]
""",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    stats = promote_trusted(cfg)
    assert stats["sites"] == 0
    assert any(
        msg == "source_unavailable" and kv.get("key") == "bad" and kv.get("stage") == "promote"
        for msg, kv in seen
    )
