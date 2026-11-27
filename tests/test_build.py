from __future__ import annotations

import json
from pathlib import Path

from tvboxgen.build import build, dumps
from tvboxgen.config import load_config


def _setup(tmp: Path) -> Path:
    fixtures = Path(__file__).parent / "fixtures"
    (tmp / "sources" / "trusted").mkdir(parents=True)
    (tmp / "sources" / "pending").mkdir(parents=True)
    (tmp / "data" / "history").mkdir(parents=True)
    (tmp / "dist").mkdir(parents=True)
    (tmp / "sources" / "trusted" / "sites.json").write_text(
        (fixtures / "sites.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp / "sources" / "trusted" / "lives.json").write_text("[]\n", encoding="utf-8")
    (tmp / "sources" / "pending" / "sites.json").write_text("[]\n", encoding="utf-8")
    (tmp / "data" / "probe_report.json").write_text(
        (fixtures / "probe_report.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp / "config.toml").write_text(
        """
[sources]
trusted_dir = "sources/trusted"
pending_dir = "sources/pending"
pending_affects_output = false

[build]
accept_status = ["ok"]
allow_degraded = false

[static]
flags = ["qq", "iqiyi"]
wallpaper = ""
parses = []
""",
        encoding="utf-8",
    )
    return tmp


def test_build_filters_skipped_and_sorts(tmp_path: Path) -> None:
    root = _setup(tmp_path)
    cfg = load_config(root)
    index = build(cfg)
    keys = [s["key"] for s in index["sites"]]
    assert keys == ["alpha", "beta"]  # score 90 then 80
    assert "gamma_skip" not in keys
    # internal fields stripped
    assert "tags" not in index["sites"][0]
    assert "note" not in index["sites"][0]
    assert index["flags"] == ["qq", "iqiyi"]


def test_build_idempotent(tmp_path: Path) -> None:
    root = _setup(tmp_path)
    cfg = load_config(root)
    build(cfg)
    first = cfg.index_path.read_text(encoding="utf-8")
    build(cfg)
    second = cfg.index_path.read_text(encoding="utf-8")
    assert first == second
    assert first.endswith("\n")


def test_dumps_stable() -> None:
    assert dumps({"b": 1, "a": 2}) == '{\n  "b": 1,\n  "a": 2\n}\n'


def test_pending_not_in_index_even_if_in_report(tmp_path: Path) -> None:
    root = _setup(tmp_path)
    fixtures = Path(__file__).parent / "fixtures"
    (root / "sources" / "pending" / "sites.json").write_text(
        (fixtures / "pending_sites.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    report = json.loads((root / "data" / "probe_report.json").read_text(encoding="utf-8"))
    report["sites"].append(
        {
            "key": "pending_only",
            "status": "ok",
            "level": "L3",
            "latency_ms": 100,
            "score": 99.0,
            "http_code": 200,
            "checks": {
                "reachable": True,
                "json_ok": True,
                "shape_ok": True,
                "search_ok": True,
            },
            "error": None,
            "checked_at": "2026-11-27T08:00:09Z",
        }
    )
    (root / "data" / "probe_report.json").write_text(dumps(report), encoding="utf-8")
    cfg = load_config(root)
    index = build(cfg)
    assert "pending_only" not in {s["key"] for s in index["sites"]}
