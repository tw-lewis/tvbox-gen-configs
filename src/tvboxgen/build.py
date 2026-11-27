"""过滤、排序、分组并序列化 dist/index.json。"""

from __future__ import annotations

import json
import logging
from typing import Any

from tvboxgen.config import Config
from tvboxgen.logger import get_logger, log_kv
from tvboxgen.models import ProbeResult, Report, Site
from tvboxgen.sources import load_lives, sites_for_build

log = get_logger()


def dumps(obj: Any) -> str:
    """统一 JSON 序列化格式，保证幂等（相同输入无 diff）。"""
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def load_report(cfg: Config) -> Report:
    """加载探测报告。"""
    path = cfg.report_path
    if not path.is_file():
        raise FileNotFoundError(f"缺少探测报告: {path}；请先执行 tvboxgen probe")
    log_kv(log, logging.INFO, "build_load_report", path=str(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    return Report.from_dict(data)


def in_watchlist(cfg: Config, key: str) -> bool:
    """人工黑名单或连续失败熔断。"""
    if key in cfg.watchlist_keys:
        return True
    from tvboxgen.streak import load_streaks

    return load_streaks(cfg).get(key, 0) >= cfg.fail_streak_limit


def build_index(
    report: Report, sites: list[Site], lives: list[Site], cfg: Config
) -> dict[str, Any]:
    """按探测结果过滤 trusted 源，生成 TVBox 产物结构。"""
    by_key = report.by_key()
    accept = set(cfg.accept_status)
    if cfg.allow_degraded:
        accept.add("degraded")

    alive: list[tuple[Site, ProbeResult]] = []
    skip = {"no_report": 0, "status": 0, "latency": 0, "watchlist": 0}
    for site in sites:
        pr = by_key.get(site.key)
        if pr is None:
            skip["no_report"] += 1
            continue
        if pr.status not in accept:
            skip["status"] += 1
            continue
        if cfg.max_latency_ms and pr.latency_ms > cfg.max_latency_ms:
            skip["latency"] += 1
            continue
        if in_watchlist(cfg, site.key):
            skip["watchlist"] += 1
            log_kv(log, logging.INFO, "build_watchlist_skip", key=site.key)
            continue
        alive.append((site, pr))

    alive.sort(key=lambda pair: (-pair[1].score, pair[0].name))
    out_sites = [s.to_index_site() for s, _ in alive]
    # 直播：trusted 中已晋升的频道直接写入产物
    out_lives = [lv.to_index_live() for lv in lives]

    log_kv(
        log,
        logging.INFO,
        "build_filter",
        input_sites=len(sites),
        output_sites=len(out_sites),
        output_lives=len(out_lives),
        **{f"skip_{k}": v for k, v in skip.items()},
    )

    return {
        "sites": out_sites,
        "lives": out_lives,
        "parses": cfg.static_parses,
        "flags": cfg.flags,
        "wallpaper": cfg.wallpaper,
    }


def build(cfg: Config, *, dry_run: bool = False) -> dict[str, Any]:
    """生成 dist/index.json；dry_run 时只对比不写盘。"""
    log_kv(log, logging.INFO, "build_start", dry_run=dry_run, index=str(cfg.index_path))
    report = load_report(cfg)
    sites = sites_for_build(cfg)
    lives = load_lives(cfg)
    log_kv(
        log,
        logging.INFO,
        "build_inputs",
        trusted_sites=len(sites),
        trusted_lives=len(lives),
    )
    index = build_index(report, sites, lives, cfg)
    text = dumps(index)

    if dry_run:
        existing = ""
        if cfg.index_path.is_file():
            existing = cfg.index_path.read_text(encoding="utf-8")
        changed = existing != text
        log_kv(
            log,
            logging.INFO,
            "build_dry_run",
            changed=changed,
            sites=len(index["sites"]),
            lives=len(index["lives"]),
        )
        if changed:
            print(text, end="")
        return index

    cfg.index_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.index_path.write_text(text, encoding="utf-8")
    log_kv(
        log,
        logging.INFO,
        "build_done",
        path=str(cfg.index_path),
        sites=len(index["sites"]),
        lives=len(index["lives"]),
    )
    return index
