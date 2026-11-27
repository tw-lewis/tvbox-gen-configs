from __future__ import annotations

import json
from pathlib import Path

from tvboxgen.sync import merge_into_pool


def test_merge_into_pool_overwrites_file_with_deduped_batch(tmp_path: Path) -> None:
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
            "key": "dup_same_api",
            "name": "重复API",
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
    # 覆盖写入：旧文件内容不保留；本批内按 api 去重
    assert "manual_keep" not in keys
    assert "new_feed" in keys
    assert "auto_old" in keys or "dup_same_api" in keys
    assert len(apis) == len(set(apis))
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert {s["key"] for s in on_disk} == set(keys)
