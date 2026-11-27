from __future__ import annotations

import json
from pathlib import Path

from tvboxgen.config import load_config
from tvboxgen.sources import load_sites, sites_for_build


def _write_cfg(tmp: Path) -> Path:
    (tmp / "sources" / "trusted").mkdir(parents=True)
    (tmp / "sources" / "pending").mkdir(parents=True)
    (tmp / "sources" / "manual").mkdir(parents=True)
    fixtures = Path(__file__).parent / "fixtures"
    (tmp / "sources" / "pending" / "sites.json").write_text(
        (fixtures / "sites.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp / "sources" / "pending" / "lives.json").write_text("[]\n", encoding="utf-8")
    (tmp / "sources" / "manual" / "sites.json").write_text("[]\n", encoding="utf-8")
    (tmp / "sources" / "manual" / "lives.json").write_text("[]\n", encoding="utf-8")
    # trusted starts empty; filled by promote
    (tmp / "sources" / "trusted" / "sites.json").write_text("[]\n", encoding="utf-8")
    (tmp / "sources" / "trusted" / "lives.json").write_text("[]\n", encoding="utf-8")
    # also put a pending-only and a duplicate-key style pending file already in fixtures
    pending_extra = json.loads((fixtures / "pending_sites.json").read_text(encoding="utf-8"))
    # merge into pending sites for dedupe test via second file — use single file
    base = json.loads((tmp / "sources" / "pending" / "sites.json").read_text(encoding="utf-8"))
    # append pending_only only
    for row in pending_extra:
        if row["key"] == "pending_only":
            base.append(row)
    (tmp / "sources" / "pending" / "sites.json").write_text(
        json.dumps(base, ensure_ascii=False), encoding="utf-8"
    )
    (tmp / "config.toml").write_text(
        """
[sources]
trusted_dir = "sources/trusted"
pending_dir = "sources/pending"
manual_dir = "sources/manual"
pending_affects_output = false
""",
        encoding="utf-8",
    )
    return tmp


def test_probe_loads_pending_and_manual_inventory(tmp_path: Path) -> None:
    root = _write_cfg(tmp_path)
    (root / "sources" / "manual" / "sites.json").write_text(
        json.dumps(
            [
                {
                    "key": "manual_only",
                    "name": "手维",
                    "api": "https://manual.example.com/api.php/provide/vod",
                    "type": 1,
                    "searchable": 1,
                    "origin": "manual",
                }
            ]
        ),
        encoding="utf-8",
    )
    cfg = load_config(root)
    sites = load_sites(cfg, include_pending=True)
    keys = {s.key for s in sites}
    assert "alpha" in keys
    assert "pending_only" in keys
    assert "manual_only" in keys
    assert any(s.origin == "manual" for s in sites if s.key == "manual_only")


def test_build_uses_trusted_only(tmp_path: Path) -> None:
    root = _write_cfg(tmp_path)
    # put one site into trusted
    (root / "sources" / "trusted" / "sites.json").write_text(
        json.dumps(
            [
                {
                    "key": "alpha",
                    "name": "Alpha源",
                    "api": "https://alpha.example.com/api.php/provide/vod",
                    "type": 1,
                    "searchable": 1,
                    "quickSearch": 1,
                    "filterable": 1,
                }
            ]
        ),
        encoding="utf-8",
    )
    cfg = load_config(root)
    built = sites_for_build(cfg)
    assert {s.key for s in built} == {"alpha"}
    assert all(s.origin == "trusted" for s in built)


def test_example_json_ignored(tmp_path: Path) -> None:
    root = _write_cfg(tmp_path)
    example = {
        "key": "example_only",
        "name": "示例",
        "api": "https://example.com/x",
        "type": 1,
        "searchable": 1,
    }
    (root / "sources" / "pending" / "sites.example.json").write_text(
        json.dumps([example]),
        encoding="utf-8",
    )
    cfg = load_config(root)
    keys = {s.key for s in load_sites(cfg)}
    assert "example_only" not in keys
