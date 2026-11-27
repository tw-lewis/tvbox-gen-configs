from __future__ import annotations

import json
from pathlib import Path

from tvboxgen.build import dumps
from tvboxgen.config import load_config
from tvboxgen.promote import promote_trusted


def _setup(tmp: Path) -> Path:
    pending = tmp / "sources" / "pending"
    manual = tmp / "sources" / "manual"
    trusted = tmp / "sources" / "trusted"
    pending.mkdir(parents=True)
    manual.mkdir(parents=True)
    trusted.mkdir(parents=True)
    (tmp / "data").mkdir(parents=True)
    pending_sites = [
        {
            "key": "fail_one",
            "name": "不可用",
            "api": "https://fail.example.com/api.php/provide/vod",
            "type": 1,
            "searchable": 1,
            "quickSearch": 1,
            "filterable": 1,
            "origin": "upstream:u1",
        },
    ]
    manual_sites = [
        {
            "key": "ok_one",
            "name": "可用",
            "api": "https://ok.example.com/api.php/provide/vod",
            "type": 1,
            "searchable": 1,
            "quickSearch": 1,
            "filterable": 1,
            "origin": "manual",
        },
        {
            "key": "degraded_one",
            "name": "降级",
            "api": "https://deg.example.com/api.php/provide/vod",
            "type": 1,
            "searchable": 1,
            "quickSearch": 1,
            "filterable": 1,
            "origin": "manual",
        },
    ]
    pending_lives = [
        {
            "key": "live_bad",
            "name": "挂掉",
            "url": "https://dead.example.com/x.m3u8",
            "group": "其他",
            "logo": "",
            "origin": "upstream:live1",
        },
    ]
    manual_lives = [
        {
            "key": "live_ok",
            "name": "CCTV1",
            "url": "https://live.example.com/1.m3u8",
            "group": "央视",
            "logo": "",
            "origin": "manual",
        },
    ]
    (pending / "sites.json").write_text(dumps(pending_sites), encoding="utf-8")
    (pending / "lives.json").write_text(dumps(pending_lives), encoding="utf-8")
    (manual / "sites.json").write_text(dumps(manual_sites), encoding="utf-8")
    (manual / "lives.json").write_text(dumps(manual_lives), encoding="utf-8")
    (trusted / "sites.json").write_text("[]\n", encoding="utf-8")
    (trusted / "lives.json").write_text("[]\n", encoding="utf-8")
    report = {
        "generated_at": "2026-11-27T08:00:00Z",
        "runner": "test",
        "summary": {"total": 3, "ok": 1, "degraded": 1, "fail": 1, "skipped": 0},
        "sites": [
            {
                "key": "ok_one",
                "status": "ok",
                "level": "L3",
                "latency_ms": 100,
                "score": 90,
                "http_code": 200,
                "checks": {
                    "reachable": True,
                    "json_ok": True,
                    "shape_ok": True,
                    "search_ok": True,
                },
                "error": None,
                "checked_at": "2026-11-27T08:00:01Z",
            },
            {
                "key": "fail_one",
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
                "checked_at": "2026-11-27T08:00:02Z",
            },
            {
                "key": "degraded_one",
                "status": "degraded",
                "level": "L2",
                "latency_ms": 200,
                "score": 50,
                "http_code": 200,
                "checks": {
                    "reachable": True,
                    "json_ok": True,
                    "shape_ok": True,
                    "search_ok": False,
                },
                "error": "search_empty",
                "checked_at": "2026-11-27T08:00:03Z",
            },
            {
                "key": "live_ok",
                "status": "ok",
                "level": "L0",
                "latency_ms": 50,
                "score": 70,
                "http_code": 200,
                "checks": {
                    "reachable": True,
                    "json_ok": False,
                    "shape_ok": False,
                    "search_ok": False,
                },
                "error": None,
                "checked_at": "2026-11-27T08:00:04Z",
            },
            {
                "key": "live_bad",
                "status": "fail",
                "level": "L0",
                "latency_ms": 50,
                "score": 0,
                "http_code": None,
                "checks": {
                    "reachable": False,
                    "json_ok": False,
                    "shape_ok": False,
                    "search_ok": False,
                },
                "error": "down",
                "checked_at": "2026-11-27T08:00:05Z",
            },
        ],
    }
    (tmp / "data" / "probe_report.json").write_text(dumps(report), encoding="utf-8")
    (tmp / "config.toml").write_text(
        """
[sources]
trusted_dir = "sources/trusted"
pending_dir = "sources/pending"
manual_dir = "sources/manual"
pending_affects_output = false

[build]
accept_status = ["ok"]
allow_degraded = false
""",
        encoding="utf-8",
    )
    return tmp


def test_promote_only_ok_into_trusted(tmp_path: Path) -> None:
    root = _setup(tmp_path)
    cfg = load_config(root)
    stats = promote_trusted(cfg)
    assert stats["sites"] == 1
    assert stats["lives"] == 1

    sites = json.loads((root / "sources" / "trusted" / "sites.json").read_text(encoding="utf-8"))
    lives = json.loads((root / "sources" / "trusted" / "lives.json").read_text(encoding="utf-8"))
    assert [s["key"] for s in sites] == ["ok_one"]
    assert [lv["key"] for lv in lives] == ["live_ok"]
    # pending / manual untouched
    pending = json.loads((root / "sources" / "pending" / "sites.json").read_text(encoding="utf-8"))
    manual = json.loads((root / "sources" / "manual" / "sites.json").read_text(encoding="utf-8"))
    assert len(pending) == 1
    assert len(manual) == 2
