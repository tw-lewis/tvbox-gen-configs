from __future__ import annotations

import json
from pathlib import Path

from tvboxgen.sync import merge_into_pool


def test_append_dedupe_keeps_manual_skips_duplicate_api(tmp_path: Path) -> None:
    pending = tmp_path / "pending"
    pending.mkdir()
    path = pending / "sites.json"
    existing = [
        {
            "key": "manual_keep",
            "name": "手工源",
            "api": "https://manual.example.com/api.php/provide/vod",
            "type": 1,
            "searchable": 1,
            "origin": "manual",
        },
        {
            "key": "auto_old",
            "name": "旧自动",
            "api": "https://old.example.com/api.php/provide/vod",
            "type": 1,
            "searchable": 1,
            "origin": "upstream:feed_a",
        },
    ]
    path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")

    incoming = [
        {
            "key": "dup_manual",
            "name": "重复手工API",
            "api": "https://manual.example.com/api.php/provide/vod",
            "type": 1,
            "searchable": 1,
        },
        {
            "key": "new_feed",
            "name": "新自动",
            "api": "https://new.example.com/api.php/provide/vod",
            "type": 1,
            "searchable": 1,
        },
        {
            "key": "auto_old",
            "name": "旧自动改名",
            "api": "https://old.example.com/api.php/provide/vod",
            "type": 1,
            "searchable": 1,
        },
    ]
    merged = merge_into_pool(path, incoming, upstream_id="feed_a", kind="site")
    keys = [s["key"] for s in merged]
    apis = [s["api"] for s in merged]
    assert "manual_keep" in keys
    assert "auto_old" in keys
    assert "new_feed" in keys
    assert keys.count("dup_manual") == 0
    assert len(apis) == len(set(apis))
    # append: old upstream entry stays; new one added
    assert len(merged) == 3
