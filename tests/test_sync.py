from __future__ import annotations

import json
from pathlib import Path

from tvboxgen.config import load_config
from tvboxgen.sync import (
    merge_into_pool,
    parse_lives_payload,
    parse_sites_payload,
    sync_upstreams,
)


TVBOX_SAMPLE = {
    "sites": [
        {
            "key": "ikun",
            "name": "iKun",
            "type": 1,
            "api": "https://ikunzyapi.com/api.php/provide/vod",
            "searchable": 1,
            "quickSearch": 1,
            "filterable": 1,
        },
        {
            "key": "adult_skip",
            "name": "Adult站",
            "api": "https://bad.example.com/api.php/provide/vod",
            "type": 1,
            "is_adult": True,
        },
    ],
    "lives": [
        {"name": "CCTV1", "url": "https://live.example.com/cctv1.m3u8", "group": "央视", "logo": ""},
        {"name": "CCTV2", "url": "http://live.example.com/cctv2.m3u8", "group": "央视"},
    ],
}

TXT_SAMPLE = """# comment
极速|https://jszyapi.com/api.php/provide/vod|1|100
https://orphan.example.com/api.php/provide/vod
"""

API_SITE_SAMPLE = {
    "api_site": {
        "maoyan": {"name": "猫眼", "api": "https://api.maoyanapi.top/api.php/provide/vod"},
        "off": {"name": "关闭", "api": "https://off.example.com/x", "enabled": False},
    }
}


def test_parse_tvbox_sites_and_skip_adult() -> None:
    sites = parse_sites_payload(json.dumps(TVBOX_SAMPLE).encode())
    keys = {s["key"] for s in sites}
    assert "ikun" in keys or any(s["api"].endswith("/provide/vod") for s in sites)
    assert all("adult" not in (s.get("name") or "").lower() for s in sites)


def test_parse_txt_lines() -> None:
    sites = parse_sites_payload(TXT_SAMPLE.encode())
    apis = {s["api"] for s in sites}
    assert "https://jszyapi.com/api.php/provide/vod" in apis
    assert "https://orphan.example.com/api.php/provide/vod" in apis


def test_parse_api_site_object() -> None:
    sites = parse_sites_payload(json.dumps(API_SITE_SAMPLE).encode())
    names = {s["name"] for s in sites}
    assert "猫眼" in names
    assert "关闭" not in names


def test_parse_lives() -> None:
    lives = parse_lives_payload(json.dumps(TVBOX_SAMPLE).encode())
    assert len(lives) == 2
    assert lives[0]["name"] == "CCTV1"


def test_parse_ouonnki_json_url_as_api() -> None:
    raw = [
        {
            "id": "ikunzy-com",
            "name": "iKun资源",
            "url": "https://ikunzyapi.com/api.php/provide/vod",
            "detailUrl": "https://ikunzyapi.com/api.php/provide/vod",
            "isEnabled": True,
        },
        {
            "id": "off",
            "name": "关闭",
            "url": "https://off.example.com/api.php/provide/vod",
            "isEnabled": False,
        },
    ]
    sites = parse_sites_payload(json.dumps(raw).encode(), fmt="json")
    assert len(sites) == 1
    assert sites[0]["api"] == "https://ikunzyapi.com/api.php/provide/vod"
    assert sites[0]["key"] == "ikunzy-com"


def test_parse_diy_live_genre_list() -> None:
    text = """CCTV,#genre#
CCTV1,http://example.com/cctv1.m3u8
CCTV2,http://example.com/cctv2.m3u8
卫视,#genre#
湖南卫视,http://example.com/hunan.m3u8
"""
    lives = parse_lives_payload(text.encode())
    assert len(lives) == 3
    assert lives[0]["group"] == "CCTV"
    assert lives[0]["name"] == "CCTV1"
    assert lives[2]["group"] == "卫视"
    assert lives[2]["name"] == "湖南卫视"


def test_parse_ziyuanzu_txt_and_skip_adultish() -> None:
    text = """# header
极速资源站|https://jszyapi.com/api.php/provide/vod/|json|100%
番号资源站|http://fhapi9.com/api.php/provide/vod/|json|100%
魔都资源站|https://www.mdzyapi.com/api.php/provide/vod/|json|100%
"""
    sites = parse_sites_payload(text.encode(), fmt="txt")
    apis = {s["api"] for s in sites}
    assert "https://jszyapi.com/api.php/provide/vod" in apis
    assert "https://www.mdzyapi.com/api.php/provide/vod" in apis
    assert not any("fhapi9" in a for a in apis)


def test_merge_into_pool_alias(tmp_path: Path) -> None:
    path = tmp_path / "sites.json"
    path.write_text("[]\n", encoding="utf-8")
    merged = merge_into_pool(
        path,
        [
            {
                "key": "a",
                "name": "A",
                "api": "https://a.example.com/api.php/provide/vod",
                "type": 1,
                "searchable": 1,
            }
        ],
        upstream_id="u1",
        kind="site",
    )
    assert merged[0]["origin"] == "upstream:u1"


def test_sync_appends_to_pending(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path
    (root / "sources" / "trusted").mkdir(parents=True)
    (root / "sources" / "pending").mkdir(parents=True)
    (root / "sources" / "trusted" / "sites.json").write_text("[]\n", encoding="utf-8")
    (root / "sources" / "trusted" / "lives.json").write_text("[]\n", encoding="utf-8")
    (root / "sources" / "pending" / "sites.json").write_text(
        json.dumps(
            [
                {
                    "key": "manual_keep",
                    "name": "手工",
                    "api": "https://manual.example.com/api.php/provide/vod",
                    "type": 1,
                    "searchable": 1,
                    "origin": "manual",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "sources" / "pending" / "lives.json").write_text("[]\n", encoding="utf-8")
    collectors = root / "sources" / "collectors"
    collectors.mkdir(parents=True)
    (collectors / "feeds.json").write_text(
        json.dumps(
            [
                {
                    "id": "vod1",
                    "name": "点播源库1",
                    "url": "https://example.com/tvbox.json",
                    "enabled": True,
                    "target": "pending",
                    "kind": "sites",
                    "format": "json",
                },
                {
                    "id": "live1",
                    "name": "直播源库1",
                    "url": "https://example.com/lives.json",
                    "enabled": True,
                    "target": "pending",
                    "kind": "lives",
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
collectors_dir = "sources/collectors"
""",
        encoding="utf-8",
    )

    payloads = {
        "https://example.com/tvbox.json": json.dumps(TVBOX_SAMPLE).encode(),
        "https://example.com/lives.json": json.dumps(
            {"lives": TVBOX_SAMPLE["lives"]}
        ).encode(),
    }

    def fake_fetch(url: str, timeout: float = 40.0) -> bytes:
        return payloads[url]

    monkeypatch.setattr("tvboxgen.sync.fetch_url", fake_fetch)
    cfg = load_config(root)
    stats = sync_upstreams(cfg)
    assert stats["sites"] >= 1
    assert stats["lives"] >= 1

    sites = json.loads((root / "sources" / "pending" / "sites.json").read_text(encoding="utf-8"))
    lives = json.loads((root / "sources" / "pending" / "lives.json").read_text(encoding="utf-8"))
    assert any(s.get("key") == "manual_keep" for s in sites)
    assert any(s.get("origin") == "upstream:vod1" for s in sites)
    assert any(lv.get("origin") == "upstream:live1" for lv in lives)
    # trusted untouched by sync
    trusted = json.loads((root / "sources" / "trusted" / "sites.json").read_text(encoding="utf-8"))
    assert trusted == []
