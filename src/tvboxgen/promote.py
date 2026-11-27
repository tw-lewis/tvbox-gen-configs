"""将探测可用的 pending 种子晋升到 trusted（产物输入）。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from tvboxgen.build import dumps, load_report
from tvboxgen.config import Config
from tvboxgen.logger import get_logger, log_kv

log = get_logger()


def _read_list(path: Path) -> list[dict[str, Any]]:
    """读取 JSON 数组文件。"""
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log_kv(log, logging.WARNING, "promote_read_fail", path=str(path), error=str(exc))
        return []
    return [x for x in raw if isinstance(x, dict)] if isinstance(raw, list) else []


def _write_list(path: Path, rows: list[dict[str, Any]]) -> None:
    """按 name/key 排序后写入，保证幂等。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda x: (str(x.get("name") or ""), str(x.get("key") or "")))
    path.write_text(dumps(rows), encoding="utf-8")
    log_kv(log, logging.INFO, "promote_write", path=str(path), count=len(rows))


def promote_trusted(cfg: Config) -> dict[str, int]:
    """用探测报告过滤 pending，重写 trusted。"""
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

    pending_sites = _read_list(cfg.pending_dir / "sites.json")
    pending_lives = _read_list(cfg.pending_dir / "lives.json")
    log_kv(
        log,
        logging.INFO,
        "promote_pending",
        sites=len(pending_sites),
        lives=len(pending_lives),
    )

    trusted_sites: list[dict[str, Any]] = []
    skip_no_report = skip_status = skip_latency = 0
    for row in pending_sites:
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
    for row in pending_lives:
        key = str(row.get("key") or "")
        pr = by_key.get(key)
        if pr is None or pr.status not in accept:
            live_skip += 1
            continue
        trusted_lives.append(dict(row))

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
    )

    _write_list(cfg.trusted_dir / "sites.json", trusted_sites)
    _write_list(cfg.trusted_dir / "lives.json", trusted_lives)

    stats = {"sites": len(trusted_sites), "lives": len(trusted_lives)}
    log_kv(log, logging.INFO, "promote_done", **stats)
    return stats
