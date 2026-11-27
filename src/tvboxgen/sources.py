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
        try:
            site = Site.from_dict(item, kind="site", origin=origin)
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
                drop=origin,
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
        seen_keys[site.key] = origin
        if fp:
            seen_fps[fp] = site.key
        merged.append(site)
    return merged


def _parse_live_items(batch: list[dict[str, Any]], *, origin: str) -> list[Site]:
    lives: list[Site] = []
    seen: set[str] = set()
    for item in batch:
        try:
            live = Site.from_dict(item, kind="live", origin=origin)
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


def load_sites(cfg: Config, *, include_pending: bool = True) -> list[Site]:
    """加载点播种子；默认读 pending 全量池。"""
    pending_raw = read_json_dir(cfg.pending_dir, kind="site") if include_pending else []
    trusted_raw = read_json_dir(cfg.trusted_dir, kind="site") if not include_pending else []

    if include_pending:
        # pending is the full inventory for probe
        merged = _parse_site_items(pending_raw, origin="pending")
        log_kv(log, logging.INFO, "sites_loaded", pending=len(pending_raw), merged=len(merged))
        return merged

    merged = _parse_site_items(trusted_raw, origin="trusted")
    log_kv(log, logging.INFO, "sites_loaded", trusted=len(trusted_raw), merged=len(merged))
    return merged


def load_lives(cfg: Config, *, from_pending: bool = False) -> list[Site]:
    directory = cfg.pending_dir if from_pending else cfg.trusted_dir
    origin = "pending" if from_pending else "trusted"
    raw = read_json_dir(directory, kind="live")
    lives = _parse_live_items(raw, origin=origin)
    log_kv(
        log,
        logging.INFO,
        "lives_loaded",
        pending=len(raw) if from_pending else 0,
        trusted=0 if from_pending else len(raw),
        merged=len(lives),
    )
    return lives


def kind_guess(item: dict[str, Any]) -> str:
    if "url" in item and "api" not in item:
        return "live"
    return "site"


def sites_for_build(cfg: Config) -> list[Site]:
    """产物输入：默认仅 trusted（除非 pending_affects_output）。"""
    if cfg.pending_affects_output:
        return load_sites(cfg, include_pending=True)
    return load_sites(cfg, include_pending=False)


def load_probe_targets(cfg: Config) -> list[Site]:
    """探测目标：pending 点播（可选含直播）。"""
    targets = load_sites(cfg, include_pending=True)
    if cfg.probe_lives:
        targets = targets + load_lives(cfg, from_pending=True)
    return targets
