"""种子加载、合并与去重。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tvboxgen.config import Config
from tvboxgen.logger import get_logger, log_kv
from tvboxgen.models import Site

log = get_logger()


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _normalize_list(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        if isinstance(raw.get("sites"), list):
            return [x for x in raw["sites"] if isinstance(x, dict)]
        if isinstance(raw.get("sources"), list):
            return [x for x in raw["sources"] if isinstance(x, dict)]
        if isinstance(raw.get("lives"), list):
            return [x for x in raw["lives"] if isinstance(x, dict)]
    return []


def read_json_dir(directory: Path, *, kind: str) -> list[dict[str, Any]]:
    if not directory.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        if path.name.endswith(".example.json"):
            continue
        # subscriptions 单独加载，勿混入 sites/lives
        if path.name.startswith("subscriptions"):
            continue
        if kind == "site" and path.name.startswith("lives"):
            continue
        if kind == "live" and not path.name.startswith("lives"):
            continue
        try:
            raw = _read_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            log_kv(log, logging.WARNING, "skip_bad_json", path=str(path), error=str(exc))
            continue
        items.extend(_normalize_list(raw))
    return items


def read_subscriptions_file(directory: Path) -> list[dict[str, Any]]:
    """读取目录下 subscriptions.json（完整 TVBox 配置 URL 列表）。"""
    path = directory / "subscriptions.json"
    if not path.is_file():
        return []
    try:
        raw = _read_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        log_kv(log, logging.WARNING, "skip_bad_json", path=str(path), error=str(exc))
        return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        if not url:
            continue
        sid = str(row.get("id") or row.get("key") or "")
        out.append(
            {
                "id": sid or url,
                "key": sid or url,
                "name": str(row.get("name") or sid or url),
                "url": url,
                "kind": str(row.get("kind") or "vod_config"),
                "note": str(row.get("note") or ""),
                "origin": str(row.get("origin") or ""),
                "added_at": str(row.get("added_at") or ""),
            }
        )
    return out


def _api_fingerprint(api: str) -> str:
    try:
        u = urlparse(api.strip())
    except Exception:
        return api.strip().lower()
    host = (u.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (u.path or "").rstrip("/").lower()
    return f"{host}{path}"


def _parse_site_items(batch: list[dict[str, Any]], *, origin: str) -> list[Site]:
    merged: list[Site] = []
    seen_keys: dict[str, str] = {}
    seen_fps: dict[str, str] = {}
    for item in batch:
        if "key" not in item and "id" in item:
            item = {**item, "key": item["id"]}
        if "api" not in item and "url" in item and kind_guess(item) == "site":
            item = {**item, "api": item["url"]}
        row_origin = str(item.get("origin") or origin)
        try:
            site = Site.from_dict(item, kind="site", origin=row_origin)
        except (KeyError, TypeError, ValueError) as exc:
            log_kv(log, logging.WARNING, "skip_bad_site", error=str(exc), raw=item)
            continue
        if not site.key or not site.api:
            log_kv(log, logging.WARNING, "skip_incomplete_site", name=site.name)
            continue
        if site.key in seen_keys:
            log_kv(
                log,
                logging.WARNING,
                "duplicate_key",
                key=site.key,
                keep=seen_keys[site.key],
                drop=row_origin,
            )
            continue
        fp = _api_fingerprint(site.api)
        if fp and fp in seen_fps:
            log_kv(
                log,
                logging.WARNING,
                "suspect_duplicate_api",
                key=site.key,
                other=seen_fps[fp],
                api=site.api,
            )
        seen_keys[site.key] = row_origin
        if fp:
            seen_fps[fp] = site.key
        merged.append(site)
    return merged


def _parse_live_items(batch: list[dict[str, Any]], *, origin: str) -> list[Site]:
    lives: list[Site] = []
    seen: set[str] = set()
    for item in batch:
        row_origin = str(item.get("origin") or origin)
        try:
            live = Site.from_dict(item, kind="live", origin=row_origin)
        except (KeyError, TypeError, ValueError):
            continue
        if not live.name or not live.url:
            continue
        if not live.key:
            live.key = live.name
        if live.key in seen:
            continue
        seen.add(live.key)
        lives.append(live)
    return lives


def _merge_by_key(base: list[Site], overlay: list[Site]) -> list[Site]:
    """按 key 合并；overlay 覆盖同名 key（手维优先于采集）。"""
    by_key = {s.key: s for s in base}
    for s in overlay:
        by_key[s.key] = s
    return list(by_key.values())


def load_pending_sites(cfg: Config) -> list[Site]:
    raw = read_json_dir(cfg.pending_dir, kind="site")
    return _parse_site_items(raw, origin="pending")


def load_manual_sites(cfg: Config) -> list[Site]:
    raw = read_json_dir(cfg.manual_dir, kind="site")
    return _parse_site_items(raw, origin="manual")


def load_pending_lives(cfg: Config) -> list[Site]:
    raw = read_json_dir(cfg.pending_dir, kind="live")
    return _parse_live_items(raw, origin="pending")


def load_manual_lives(cfg: Config) -> list[Site]:
    raw = read_json_dir(cfg.manual_dir, kind="live")
    return _parse_live_items(raw, origin="manual")


def load_inventory_sites(cfg: Config) -> list[Site]:
    """探测用点播全量：pending(采集) ∪ manual(手维)，手维覆盖同 key。"""
    pending = load_pending_sites(cfg)
    manual = load_manual_sites(cfg)
    merged = _merge_by_key(pending, manual)
    log_kv(
        log,
        logging.INFO,
        "sites_loaded",
        pending=len(pending),
        manual=len(manual),
        merged=len(merged),
    )
    return merged


def load_inventory_lives(cfg: Config) -> list[Site]:
    """探测用直播全量：pending ∪ manual，手维覆盖同 key。"""
    pending = load_pending_lives(cfg)
    manual = load_manual_lives(cfg)
    merged = _merge_by_key(pending, manual)
    log_kv(
        log,
        logging.INFO,
        "lives_loaded",
        pending=len(pending),
        manual=len(manual),
        merged=len(merged),
    )
    return merged


def load_sites(cfg: Config, *, include_pending: bool = True) -> list[Site]:
    """加载点播种子。

    include_pending=True → 探测全量（pending∪manual）；
    False → 仅 trusted（build 默认）。
    """
    if include_pending:
        return load_inventory_sites(cfg)

    trusted_raw = read_json_dir(cfg.trusted_dir, kind="site")
    merged = _parse_site_items(trusted_raw, origin="trusted")
    log_kv(log, logging.INFO, "sites_loaded", trusted=len(trusted_raw), merged=len(merged))
    return merged


def load_lives(cfg: Config, *, from_pending: bool = False) -> list[Site]:
    """加载直播。from_pending=True → 探测全量；False → 仅 trusted。"""
    if from_pending:
        return load_inventory_lives(cfg)

    raw = read_json_dir(cfg.trusted_dir, kind="live")
    lives = _parse_live_items(raw, origin="trusted")
    log_kv(log, logging.INFO, "lives_loaded", trusted=len(raw), merged=len(lives))
    return lives


def kind_guess(item: dict[str, Any]) -> str:
    if "url" in item and "api" not in item:
        return "live"
    return "site"


def sites_for_build(cfg: Config) -> list[Site]:
    """产物输入：默认仅 trusted（除非 pending_affects_output）。"""
    if cfg.pending_affects_output:
        return load_inventory_sites(cfg)
    return load_sites(cfg, include_pending=False)


def load_inventory_rows(cfg: Config, *, kind: str) -> list[dict[str, Any]]:
    """promote 用：合并 pending+manual 原始行，manual 覆盖同 key。"""
    if kind == "subscription":
        pending = read_subscriptions_file(cfg.pending_dir)
        manual = read_subscriptions_file(cfg.manual_dir)
        key_fn = lambda r: str(r.get("id") or r.get("key") or r.get("url") or "")
    elif kind == "live":
        pending = read_json_dir(cfg.pending_dir, kind="live")
        manual = read_json_dir(cfg.manual_dir, kind="live")
        key_fn = lambda r: str(r.get("key") or r.get("name") or "")
    else:
        pending = read_json_dir(cfg.pending_dir, kind="site")
        manual = read_json_dir(cfg.manual_dir, kind="site")
        key_fn = lambda r: str(r.get("key") or r.get("id") or "")

    by_key: dict[str, dict[str, Any]] = {}
    for row in pending:
        k = key_fn(row)
        if k:
            by_key[k] = row
    for row in manual:
        k = key_fn(row)
        if k:
            by_key[k] = row
    return list(by_key.values())


def load_inventory_subscriptions(cfg: Config) -> list[Site]:
    """探测用订阅：pending∪manual 的完整 TVBox JSON URL。"""
    rows = load_inventory_rows(cfg, kind="subscription")
    out: list[Site] = []
    for row in rows:
        sid = str(row.get("id") or row.get("key") or "")
        url = str(row.get("url") or "").strip()
        if not sid or not url:
            continue
        out.append(
            Site(
                key=sid,
                name=str(row.get("name") or sid),
                api="",
                url=url,
                kind="subscription",
                origin=str(row.get("origin") or ""),
                note=str(row.get("note") or ""),
            )
        )
    log_kv(log, logging.INFO, "subscriptions_loaded", count=len(out))
    return out


def load_trusted_subscriptions(cfg: Config) -> list[dict[str, Any]]:
    """构建用：仅 trusted/subscriptions.json。"""
    rows = read_subscriptions_file(cfg.trusted_dir)
    log_kv(log, logging.INFO, "trusted_subscriptions_loaded", count=len(rows))
    return rows


def load_probe_targets(cfg: Config) -> list[Site]:
    """探测目标：pending∪manual 的点播 / 直播 / 订阅。"""
    targets = load_inventory_sites(cfg)
    if cfg.probe_lives:
        targets = targets + load_inventory_lives(cfg)
    targets = targets + load_inventory_subscriptions(cfg)
    return targets
