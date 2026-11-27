"""过滤、排序并序列化三份产物：纯源 / 订阅源 / 多仓源。"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any

from tvboxgen.cdn import (
    clear_reach_cache,
    resolve_public_url,
    rewrite_diy_lives,
    rewrite_urls_in_obj,
)
from tvboxgen.config import Config
from tvboxgen.logger import get_logger, log_kv
from tvboxgen.models import ProbeResult, Report, Site
from tvboxgen.sources import load_lives, load_trusted_subscriptions, sites_for_build

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


def lives_to_diy(lives: list[Site]) -> str:
    """频道列表 → DIY 直播文本（group,#genre# / name,url），供 TVBox LiveParser 识别。"""
    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for lv in lives:
        url = (lv.url or "").strip()
        if not url:
            continue
        name = (lv.name or lv.key or url).strip() or url
        group = (lv.group or "").strip() or "未分组"
        groups[group].append((name, url))

    lines: list[str] = []
    for group in sorted(groups.keys()):
        lines.append(f"{group},#genre#")
        for name, url in sorted(groups[group], key=lambda pair: (pair[0], pair[1])):
            lines.append(f"{name},{url}")
    return "\n".join(lines) + ("\n" if lines else "")


def build_lives_entry(cfg: Config, *, has_channels: bool) -> list[dict[str, Any]]:
    """TVBox lives：指向同目录 lives.txt 的列表源，而非内嵌频道对象。"""
    if not has_channels:
        return []
    return [
        {
            "name": cfg.lives_name or "直播",
            "type": 0,
            "url": "./lives.txt",
            "playerType": 1,
        }
    ]


def build_subscriptions_product(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """订阅源产物：GitHub→jsDelivr，且改写后必须可达，否则丢弃。"""
    out: list[dict[str, Any]] = []
    skipped = 0
    for row in sorted(rows, key=lambda r: (str(r.get("name") or ""), str(r.get("url") or ""))):
        resolved = resolve_public_url(str(row.get("url") or "").strip())
        if not resolved:
            skipped += 1
            continue
        item: dict[str, Any] = {
            "name": str(row.get("name") or row.get("id") or resolved),
            "url": resolved,
        }
        sid = str(row.get("id") or row.get("key") or "")
        if sid:
            item["id"] = sid
        note = str(row.get("note") or "")
        if note:
            item["note"] = note
        out.append(item)
    if skipped:
        log_kv(log, logging.WARNING, "subscriptions_cdn_skipped", skipped=skipped, kept=len(out))
    return out


def build_urls_depot(cfg: Config, subscriptions: list[dict[str, Any]]) -> dict[str, Any]:
    """多仓源：纯源 + 已校验的订阅 URL（订阅列表应已通过 resolve）。"""
    urls: list[dict[str, str]] = [
        {
            "name": cfg.pure_name or "tvbox视频源",
            # 本仓库产物：发布后由 jsDelivr 提供；本地 build 不远程校验（可能尚未推送）
            "url": cfg.dist_public_url("tvbox.json"),
        }
    ]
    for row in subscriptions:
        name = str(row.get("name") or row.get("id") or "").strip()
        url = str(row.get("url") or "").strip()
        if not url:
            continue
        urls.append({"name": name or url, "url": url})
    return {"urls": urls}


def build_index(
    report: Report, sites: list[Site], lives: list[Site], cfg: Config
) -> dict[str, Any]:
    """按探测结果过滤 trusted 源，生成标准 TVBox 纯源 JSON。"""
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
    channel_count = sum(1 for lv in lives if (lv.url or "").strip())
    out_lives = build_lives_entry(cfg, has_channels=channel_count > 0)

    log_kv(
        log,
        logging.INFO,
        "build_filter",
        input_sites=len(sites),
        output_sites=len(out_sites),
        output_live_sources=len(out_lives),
        output_live_channels=channel_count,
        **{f"skip_{k}": v for k, v in skip.items()},
    )

    return {
        "spider": cfg.spider,
        "wallpaper": cfg.wallpaper,
        "logo": cfg.logo,
        "sites": out_sites,
        "lives": out_lives,
        "parses": cfg.static_parses,
        "flags": cfg.flags,
        "rules": cfg.static_rules,
        "doh": cfg.static_doh,
        "ads": cfg.static_ads,
    }


def build(cfg: Config, *, dry_run: bool = False) -> dict[str, Any]:
    """生成纯源 tvbox.json、订阅源 subscriptions.json、多仓 urls.json + lives.txt。"""
    log_kv(log, logging.INFO, "build_start", dry_run=dry_run, index=str(cfg.index_path))
    report = load_report(cfg)
    sites = sites_for_build(cfg)
    lives = load_lives(cfg)
    sub_rows = load_trusted_subscriptions(cfg)
    log_kv(
        log,
        logging.INFO,
        "build_inputs",
        trusted_sites=len(sites),
        trusted_lives=len(lives),
        trusted_subscriptions=len(sub_rows),
    )
    # GitHub → jsDelivr；改写后的地址必须可达，否则不采用 / 不写入订阅产物
    clear_reach_cache()
    index = rewrite_urls_in_obj(build_index(report, sites, lives, cfg), verify=True)
    subs_product = build_subscriptions_product(sub_rows)
    depot = build_urls_depot(cfg, subs_product)
    text = dumps(index)
    subs_text = dumps(subs_product)
    depot_text = dumps(depot)
    diy = rewrite_diy_lives(lives_to_diy(lives), verify=True)

    if dry_run:
        existing = cfg.index_path.read_text(encoding="utf-8") if cfg.index_path.is_file() else ""
        existing_lives = cfg.lives_path.read_text(encoding="utf-8") if cfg.lives_path.is_file() else ""
        existing_subs = (
            cfg.subscriptions_path.read_text(encoding="utf-8")
            if cfg.subscriptions_path.is_file()
            else ""
        )
        existing_urls = cfg.urls_path.read_text(encoding="utf-8") if cfg.urls_path.is_file() else ""
        changed = (
            existing != text
            or existing_lives != diy
            or existing_subs != subs_text
            or existing_urls != depot_text
        )
        log_kv(
            log,
            logging.INFO,
            "build_dry_run",
            changed=changed,
            sites=len(index["sites"]),
            lives=len(index["lives"]),
            subscriptions=len(subs_product),
            depot_urls=len(depot.get("urls") or []),
        )
        if changed:
            print(text, end="")
        return index

    cfg.index_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.index_path.write_text(text, encoding="utf-8")
    cfg.lives_path.write_text(diy, encoding="utf-8")
    cfg.subscriptions_path.write_text(subs_text, encoding="utf-8")
    cfg.urls_path.write_text(depot_text, encoding="utf-8")

    log_kv(
        log,
        logging.INFO,
        "build_done",
        pure=str(cfg.index_path),
        lives_path=str(cfg.lives_path),
        subscriptions=str(cfg.subscriptions_path),
        urls=str(cfg.urls_path),
        sites=len(index["sites"]),
        lives=len(index["lives"]),
        subscription_count=len(subs_product),
        depot_urls=len(depot.get("urls") or []),
        live_channels=sum(1 for line in diy.splitlines() if line and not line.endswith("#genre#")),
    )
    return index
