"""subscriptions：采集 → pending → trusted → 三份产物。"""

from __future__ import annotations

import json
from pathlib import Path

from tvboxgen.build import build, dumps
from tvboxgen.config import load_config
from tvboxgen.promote import promote_trusted
from tvboxgen.sync import is_vod_config_payload, sync_upstreams


SPIDER_CONFIG = {
    "spider": "./spider.jar",
    "sites": [
        {
            "key": "csp_demo",
            "name": "蜘蛛站",
            "type": 3,
            "api": "csp_Demo",
            "searchable": 1,
        },
        {
            "key": "cms_also",
            "name": "顺带CMS",
            "type": 1,
            "api": "https://cms.example.com/api.php/provide/vod",
            "searchable": 1,
        },
    ],
}

CMS_ONLY = {
    "sites": [
        {
            "key": "ikun",
            "name": "iKun",
            "type": 1,
            "api": "https://ikunzyapi.com/api.php/provide/vod",
            "searchable": 1,
        }
    ]
}


def test_is_vod_config_detects_spider_and_relative() -> None:
    assert is_vod_config_payload(json.dumps(SPIDER_CONFIG).encode())
    assert not is_vod_config_payload(json.dumps(CMS_ONLY).encode())
    assert not is_vod_config_payload("极速|https://a.com/api.php/provide/vod".encode())


def test_sync_spider_feed_writes_subscriptions_not_sites(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path
    for d in ("trusted", "pending", "manual", "collectors"):
        (root / "sources" / d).mkdir(parents=True)
    (root / "sources" / "pending" / "sites.json").write_text("[]\n", encoding="utf-8")
    (root / "sources" / "pending" / "lives.json").write_text("[]\n", encoding="utf-8")
    (root / "sources" / "collectors" / "feeds.json").write_text(
        dumps(
            [
                {
                    "id": "gao-js",
                    "name": "gao js",
                    "url": "https://example.com/js.json",
                    "enabled": True,
                    "kind": "sites",
                    "format": "json",
                },
                {
                    "id": "cms-txt",
                    "name": "cms",
                    "url": "https://example.com/cms.json",
                    "enabled": True,
                    "kind": "sites",
                    "format": "json",
                },
            ]
        ),
        encoding="utf-8",
    )
    (root / "config.toml").write_text(
        """
[sources]
trusted_dir = "sources/trusted"
pending_dir = "sources/pending"
manual_dir = "sources/manual"
collectors_dir = "sources/collectors"
""",
        encoding="utf-8",
    )

    payloads = {
        "https://example.com/js.json": json.dumps(SPIDER_CONFIG).encode(),
        "https://example.com/cms.json": json.dumps(CMS_ONLY).encode(),
    }
    monkeypatch.setattr("tvboxgen.sync.fetch_url", lambda url, timeout=40.0: payloads[url])

    cfg = load_config(root)
    stats = sync_upstreams(cfg)
    assert stats["subscriptions"] == 1
    assert stats["sites"] == 1
    assert stats.get("written") == 1

    sites = json.loads((root / "sources" / "pending" / "sites.json").read_text(encoding="utf-8"))
    subs = json.loads(
        (root / "sources" / "pending" / "subscriptions.json").read_text(encoding="utf-8")
    )
    assert len(sites) == 1
    assert sites[0]["api"].endswith("/provide/vod")
    assert len(subs) == 1
    assert subs[0]["url"] == "https://example.com/js.json"
    assert subs[0]["id"] == "gao-js"
    # 蜘蛛配置整包进 subscriptions，不拆站
    assert not any("csp" in (s.get("api") or "") for s in sites)


def test_sync_explicit_subscriptions_kind(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path
    for d in ("pending", "collectors"):
        (root / "sources" / d).mkdir(parents=True)
    (root / "sources" / "collectors" / "feeds.json").write_text(
        dumps(
            [
                {
                    "id": "liucn",
                    "name": "liucn box",
                    "url": "https://example.com/m.json",
                    "enabled": True,
                    "kind": "subscriptions",
                }
            ]
        ),
        encoding="utf-8",
    )
    (root / "config.toml").write_text(
        '[sources]\npending_dir="sources/pending"\ncollectors_dir="sources/collectors"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "tvboxgen.sync.fetch_url",
        lambda url, timeout=40.0: json.dumps(CMS_ONLY).encode(),
    )
    cfg = load_config(root)
    stats = sync_upstreams(cfg)
    assert stats["subscriptions"] == 1
    assert stats["sites"] == 0
    subs = json.loads(
        (root / "sources" / "pending" / "subscriptions.json").read_text(encoding="utf-8")
    )
    assert subs[0]["url"] == "https://example.com/m.json"


def test_promote_and_build_three_products(tmp_path: Path) -> None:
    root = tmp_path
    pending = root / "sources" / "pending"
    manual = root / "sources" / "manual"
    trusted = root / "sources" / "trusted"
    for d in (pending, manual, trusted, root / "data", root / "dist"):
        d.mkdir(parents=True)

    (pending / "sites.json").write_text("[]\n", encoding="utf-8")
    (pending / "lives.json").write_text("[]\n", encoding="utf-8")
    (pending / "subscriptions.json").write_text(
        dumps(
            [
                {
                    "id": "sub_ok",
                    "name": "可用订阅",
                    "url": "https://ok.example.com/box.json",
                    "kind": "vod_config",
                    "origin": "upstream:sub_ok",
                },
                {
                    "id": "sub_bad",
                    "name": "挂掉订阅",
                    "url": "https://bad.example.com/box.json",
                    "kind": "vod_config",
                    "origin": "upstream:sub_bad",
                },
            ]
        ),
        encoding="utf-8",
    )
    (manual / "sites.json").write_text(
        dumps(
            [
                {
                    "key": "cms_ok",
                    "name": "CMS",
                    "api": "https://cms.example.com/api.php/provide/vod",
                    "type": 1,
                    "searchable": 1,
                    "quickSearch": 1,
                    "filterable": 1,
                }
            ]
        ),
        encoding="utf-8",
    )
    (manual / "lives.json").write_text(
        dumps(
            [
                {
                    "key": "live_ok",
                    "name": "CCTV1",
                    "url": "https://live.example.com/1.m3u8",
                    "group": "央视",
                }
            ]
        ),
        encoding="utf-8",
    )
    (manual / "subscriptions.json").write_text(
        dumps(
            [
                {
                    "id": "manual_sub",
                    "name": "手维订阅",
                    "url": "https://manual.example.com/api.json",
                    "kind": "vod_config",
                }
            ]
        ),
        encoding="utf-8",
    )
    (trusted / "sites.json").write_text("[]\n", encoding="utf-8")
    (trusted / "lives.json").write_text("[]\n", encoding="utf-8")

    report = {
        "generated_at": "2026-11-27T08:00:00Z",
        "runner": "test",
        "summary": {"ok": 4, "fail": 1, "degraded": 0, "skipped": 0, "total": 5},
        "sites": [
            {
                "key": "cms_ok",
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
                "checked_at": "2026-11-27T08:00:02Z",
            },
            {
                "key": "sub_ok",
                "status": "ok",
                "level": "L0",
                "latency_ms": 80,
                "score": 60,
                "http_code": 200,
                "checks": {
                    "reachable": True,
                    "json_ok": True,
                    "shape_ok": False,
                    "search_ok": False,
                },
                "error": None,
                "checked_at": "2026-11-27T08:00:03Z",
            },
            {
                "key": "sub_bad",
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
                "checked_at": "2026-11-27T08:00:04Z",
            },
            {
                "key": "manual_sub",
                "status": "ok",
                "level": "L0",
                "latency_ms": 90,
                "score": 60,
                "http_code": 200,
                "checks": {
                    "reachable": True,
                    "json_ok": True,
                    "shape_ok": False,
                    "search_ok": False,
                },
                "error": None,
                "checked_at": "2026-11-27T08:00:05Z",
            },
        ],
    }
    (root / "data" / "probe_report.json").write_text(dumps(report), encoding="utf-8")
    (root / "config.toml").write_text(
        """
[repo]
owner = "tw-lewis"
name = "tvbox-gen-configs"
branch = "main"

[sources]
trusted_dir = "sources/trusted"
pending_dir = "sources/pending"
manual_dir = "sources/manual"

[build]
accept_status = ["ok"]

[static]
flags = ["qq"]
wallpaper = ""
parses = []
pure_name = "纯源"
""",
        encoding="utf-8",
    )

    cfg = load_config(root)
    stats = promote_trusted(cfg)
    assert stats["sites"] == 1
    assert stats["lives"] == 1
    assert stats["subscriptions"] == 2

    trusted_subs = json.loads((trusted / "subscriptions.json").read_text(encoding="utf-8"))
    ids = {s["id"] for s in trusted_subs}
    assert ids == {"sub_ok", "manual_sub"}
    assert "sub_bad" not in ids

    index = build(cfg)
    assert "spider" in index
    assert len(index["sites"]) == 1
    assert index["lives"][0]["url"] == "./lives.txt"

    # 纯源
    pure = json.loads((root / "dist" / "index.json").read_text(encoding="utf-8"))
    assert pure["sites"][0]["key"] == "cms_ok"

    # 订阅源
    subs_out = json.loads((root / "dist" / "subscriptions.json").read_text(encoding="utf-8"))
    assert {s["url"] for s in subs_out} == {
        "https://ok.example.com/box.json",
        "https://manual.example.com/api.json",
    }

    # 多仓源
    depot = json.loads((root / "dist" / "urls.json").read_text(encoding="utf-8"))
    assert "urls" in depot
    names = [u["name"] for u in depot["urls"]]
    assert names[0] == "纯源"
    assert depot["urls"][0]["url"].endswith("/dist/index.json")
    assert len(depot["urls"]) == 3
