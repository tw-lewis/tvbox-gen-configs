from __future__ import annotations

import json
from pathlib import Path

from tvboxgen.config import load_config


def test_load_collectors_from_single_feeds_json(tmp_path: Path) -> None:
    collectors = tmp_path / "sources" / "collectors"
    collectors.mkdir(parents=True)
    (tmp_path / "sources" / "pending").mkdir(parents=True)
    (tmp_path / "sources" / "trusted").mkdir(parents=True)
    (collectors / "feeds.json").write_text(
        json.dumps(
            [
                {
                    "id": "a",
                    "name": "A",
                    "url": "https://example.com/a.json",
                    "enabled": True,
                    "format": "json",
                },
                {
                    "id": "b",
                    "name": "B",
                    "url": "https://example.com/b.txt",
                    "enabled": False,
                    "format": "txt",
                },
            ]
        ),
        encoding="utf-8",
    )
    # stray files must be ignored
    (collectors / "other.json").write_text(
        json.dumps({"id": " Ignored", "url": "https://x", "enabled": True}),
        encoding="utf-8",
    )
    (tmp_path / "config.toml").write_text(
        """
[sources]
collectors_dir = "sources/collectors"
pending_dir = "sources/pending"
trusted_dir = "sources/trusted"
""",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    ids = {u.id for u in cfg.upstreams}
    assert ids == {"a", "b"}
    assert next(u for u in cfg.upstreams if u.id == "a").enabled is True
    assert next(u for u in cfg.upstreams if u.id == "b").format == "txt"
