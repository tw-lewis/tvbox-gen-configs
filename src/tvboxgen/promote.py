"""将探测可用的 pending 种子晋升到 trusted（产物输入）。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from tvboxgen.build import dumps, load_report
from tvboxgen.config import Config
from tvboxgen.logger import get_logger, log_kv
from tvboxgen.sources import load_inventory_rows

log = get_logger()


def _write_list(path: Path, rows: list[dict[str, Any]]) -> None:
    """按 name/key 排序后写入，保证幂等。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda x: (str(x.get("name") or ""), str(x.get("key") or "")))
    path.write_text(dumps(rows), encoding="utf-8")
    log_kv(log, logging.INFO, "promote_write", path=str(path), count=len(rows))


def promote_trusted(cfg: Config) -> dict[str, int]:
    """用探测报告过滤全量池（pending∪manual），重写 trusted。"""
    log_kv(log, logging.INFO, "promote_start", report=str(cfg.report_path))
    report = load_report(cfg)
    by_key = report.by_key()
    accept = set(cfg.accept_status)
    if cfg.allow_degraded:
        accept.add("degraded")
    log_kv(
        log,
        logging.INFO,
        "promote_rules",
        accept=",".join(sorted(accept)),
        max_latency_ms=cfg.max_latency_ms,
        report_sites=len(by_key),
    )

    inventory_sites = load_inventory_rows(cfg, kind="site")
    inventory_lives = load_inventory_rows(cfg, kind="live")
    inventory_subs = load_inventory_rows(cfg, kind="subscription")
    log_kv(
        log,
        logging.INFO,
        "promote_inventory",
        sites=len(inventory_sites),
        lives=len(inventory_lives),
        subscriptions=len(inventory_subs),
        pending=str(cfg.pending_dir),
        manual=str(cfg.manual_dir),
    )

    trusted_sites: list[dict[str, Any]] = []
    skip_no_report = skip_status = skip_latency = 0
    for row in inventory_sites:
        key = str(row.get("key") or "")
        pr = by_key.get(key)
        if pr is None:
            skip_no_report += 1
            continue
        if pr.status not in accept:
            skip_status += 1
            continue
        if cfg.max_latency_ms and pr.latency_ms > cfg.max_latency_ms:
            skip_latency += 1
            continue
        trusted_sites.append(dict(row))

    trusted_lives: list[dict[str, Any]] = []
    live_skip = 0
    for row in inventory_lives:
        key = str(row.get("key") or row.get("name") or "")
        pr = by_key.get(key)
        if pr is None or pr.status not in accept:
            live_skip += 1
            continue
        trusted_lives.append(dict(row))

    trusted_subs: list[dict[str, Any]] = []
    sub_skip = 0
    for row in inventory_subs:
        key = str(row.get("id") or row.get("key") or "")
        pr = by_key.get(key)
        if pr is None or pr.status not in accept:
            sub_skip += 1
            continue
        if cfg.max_latency_ms and pr.latency_ms > cfg.max_latency_ms:
            sub_skip += 1
            continue
        trusted_subs.append(dict(row))

    log_kv(
        log,
        logging.INFO,
        "promote_filter",
        sites_ok=len(trusted_sites),
        sites_skip_no_report=skip_no_report,
        sites_skip_status=skip_status,
        sites_skip_latency=skip_latency,
        lives_ok=len(trusted_lives),
        lives_skip=live_skip,
        subscriptions_ok=len(trusted_subs),
        subscriptions_skip=sub_skip,
    )

    _write_list(cfg.trusted_dir / "sites.json", trusted_sites)
    _write_list(cfg.trusted_dir / "lives.json", trusted_lives)
    _write_list(cfg.trusted_dir / "subscriptions.json", trusted_subs)

    stats = {
        "sites": len(trusted_sites),
        "lives": len(trusted_lives),
        "subscriptions": len(trusted_subs),
    }
    log_kv(log, logging.INFO, "promote_done", **stats)
    return stats
