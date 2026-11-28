"""分级探测：L0 可达 → L1 协议 → L2 结构 → L3 搜索。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp

from tvboxgen.build import dumps
from tvboxgen.cdn import github_to_jsdelivr
from tvboxgen.config import Config
from tvboxgen.diag import log_unavailable
from tvboxgen.logger import get_logger, log_kv
from tvboxgen.models import ProbeResult, Report, Site
from tvboxgen.sources import load_probe_targets
from tvboxgen.streak import (
    load_streaks,
    save_streaks,
    should_skip_probe,
    skipped_result,
    update_streaks_from_results,
)

log = get_logger()

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
MAX_BODY = 4 << 20


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _join_query(api: str, query: str) -> str:
    sep = "&" if "?" in api else "?"
    return f"{api.rstrip()}{sep}{query}"


def _history_stability(cfg: Config, key: str) -> float:
    """近几日历史成功率，无数据时默认 0.5。"""
    hist = cfg.history_dir
    if not hist.is_dir():
        return 0.5
    files = sorted(hist.glob("*.json"))[-7:]
    if not files:
        return 0.5
    ok = total = 0
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for item in data.get("sites") or []:
            if item.get("key") != key:
                continue
            total += 1
            if item.get("status") == "ok":
                ok += 1
    if total == 0:
        return 0.5
    return ok / total


def _score(result: ProbeResult, stability: float, max_latency_ms: int) -> float:
    # latency: 100 at 0ms → 0 at max_latency
    lat = max(0.0, min(1.0, 1.0 - (result.latency_ms / max(max_latency_ms, 1))))
    level_map = {"L0": 0.25, "L1": 0.5, "L2": 0.75, "L3": 1.0}
    level = level_map.get(result.level, 0.0)
    if result.status == "fail":
        level = min(level, 0.25)
    elif result.status == "degraded":
        level = min(level, 0.6)
    raw = 0.3 * lat * 100 + 0.5 * level * 100 + 0.2 * stability * 100
    return round(raw, 2)


async def _fetch(
    session: aiohttp.ClientSession,
    url: str,
    *,
    method: str = "GET",
    timeout: aiohttp.ClientTimeout,
) -> tuple[int | None, bytes, float, str | None]:
    start = time.perf_counter()
    try:
        async with session.request(method, url, timeout=timeout, allow_redirects=True) as resp:
            body = await resp.content.read(MAX_BODY)
            ms = (time.perf_counter() - start) * 1000
            return resp.status, body, ms, None
    except Exception as exc:  # noqa: BLE001 — probe must never crash the run
        ms = (time.perf_counter() - start) * 1000
        return None, b"", ms, str(exc)


def _looks_json(body: bytes) -> Any | None:
    text = body.decode("utf-8", errors="ignore").strip()
    if not text or text[0] not in "{[":
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _looks_xml(body: bytes) -> bool:
    text = body.decode("utf-8", errors="ignore").strip()
    if not text.startswith("<"):
        return False
    try:
        ET.fromstring(text)
        return True
    except ET.ParseError:
        return False


def _shape_ok(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    if "list" in data or "data" in data:
        return True
    # macCMS style
    if "code" in data and ("list" in data or "msg" in data):
        return True
    return False


def _search_has_content(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    lst = data.get("list")
    if isinstance(lst, list) and len(lst) > 0:
        return True
    nested = data.get("data")
    if isinstance(nested, dict):
        lst = nested.get("list")
        if isinstance(lst, list) and len(lst) > 0:
            return True
    if isinstance(nested, list) and len(nested) > 0:
        return True
    return False


async def probe_one(
    site: Site,
    session: aiohttp.ClientSession,
    domain_sems: dict[str, asyncio.Semaphore],
    global_sem: asyncio.Semaphore,
    cfg: Config,
) -> ProbeResult:
    checked_at = _now()
    if site.kind != "live" and site.searchable == 0:
        return ProbeResult(
            key=site.key,
            status="skipped",
            level="L0",
            checked_at=checked_at,
            error="searchable=0",
        )

    domain = site.domain or "unknown"
    if domain not in domain_sems:
        domain_sems[domain] = asyncio.Semaphore(cfg.per_domain_concurrency)
    timeout = aiohttp.ClientTimeout(
        sock_connect=cfg.timeout_connect,
        sock_read=cfg.timeout_read,
        total=cfg.timeout_connect + cfg.timeout_read + 2,
    )

    result = ProbeResult(key=site.key, status="fail", level="L0", checked_at=checked_at)

    async with global_sem, domain_sems[domain]:
        # Live / subscription: L0 reachability on url only
        if site.kind in ("live", "subscription"):
            # 订阅优先探测 jsDelivr（GitHub blob/raw 在 GHA 易失败或返回 HTML）
            probe_url = site.url
            if site.kind == "subscription":
                alt = github_to_jsdelivr(site.url)
                if alt:
                    probe_url = alt
            code, body, ms, err = await _fetch(session, probe_url, timeout=timeout)
            if (
                site.kind == "subscription"
                and probe_url != site.url
                and (err or code is None or (code is not None and code >= 400 and code != 405))
            ):
                code, body, ms, err = await _fetch(session, site.url, timeout=timeout)
            result.latency_ms = int(ms)
            result.http_code = code
            if err or code is None or (code >= 400 and code != 405):
                return result.as_fail(error=err or f"{site.kind}_http_{code}")
            result.checks.reachable = True
            result.level = "L0"
            result.status = "ok"
            if site.kind == "subscription" and body:
                data = _looks_json(body)
                if isinstance(data, dict) and isinstance(data.get("sites"), list):
                    result.checks.json_ok = True
                    result.level = "L1"
            result.score = _score(result, _history_stability(cfg, site.key), cfg.max_latency_ms)
            return result

        # L0 reachable
        code, body, ms, err = await _fetch(session, site.api, timeout=timeout)
        result.latency_ms = int(ms)
        result.http_code = code
        if err or code is None:
            return result.as_fail(error=err or "unreachable")
        result.checks.reachable = True
        result.level = "L0"

        # L1 protocol
        data = None
        if site.type in (0,):  # xml
            if not _looks_xml(body):
                # try GET already done; some xml endpoints need ac=
                code2, body2, ms2, err2 = await _fetch(
                    session, _join_query(site.api, "ac=list"), timeout=timeout
                )
                result.latency_ms = int(ms2)
                result.http_code = code2
                if err2 or not _looks_xml(body2):
                    return result.as_fail(error="xml_parse_fail")
                body = body2
            result.checks.json_ok = True  # protocol ok (xml)
            result.level = "L1"
        else:
            data = _looks_json(body)
            if data is None:
                # common: root returns HTML; try list endpoint
                code2, body2, ms2, err2 = await _fetch(
                    session, _join_query(site.api, "ac=list"), timeout=timeout
                )
                result.latency_ms = int(max(ms, ms2))
                result.http_code = code2
                if err2:
                    return result.as_fail(error=err2)
                data = _looks_json(body2)
                if data is None:
                    return result.as_fail(error="json_parse_fail")
            result.checks.json_ok = True
            result.level = "L1"

        # L2 shape (json only; xml treated as shape_ok after parse)
        if site.type == 0:
            result.checks.shape_ok = True
            result.level = "L2"
            result.status = "ok"
        else:
            if not _shape_ok(data):
                return result.as_fail(error="shape_missing")
            result.checks.shape_ok = True
            result.level = "L2"
            result.status = "ok"

        # L3 search
        if cfg.probe_search and site.searchable:
            kw = quote(cfg.search_keyword)
            search_url = _join_query(site.api, f"wd={kw}")
            code3, body3, ms3, err3 = await _fetch(session, search_url, timeout=timeout)
            result.latency_ms = int(max(result.latency_ms, ms3))
            if err3 or code3 != 200:
                result.status = "degraded"
                result.error = err3 or f"search_http_{code3}"
            else:
                sdata = _looks_json(body3)
                if sdata is None or not _search_has_content(sdata):
                    # fallback listing like reference project
                    list_url = _join_query(site.api, "ac=detail&pg=1")
                    _, body4, ms4, err4 = await _fetch(session, list_url, timeout=timeout)
                    result.latency_ms = int(max(result.latency_ms, ms4))
                    ldata = None if err4 else _looks_json(body4)
                    if ldata is not None and _search_has_content(ldata):
                        result.checks.search_ok = False
                        result.status = "degraded"
                        result.error = "search_empty_listing_ok"
                        result.level = "L2"
                    else:
                        result.status = "degraded"
                        result.error = "search_empty"
                else:
                    result.checks.search_ok = True
                    result.level = "L3"
                    result.status = "ok"

        result.score = _score(result, _history_stability(cfg, site.key), cfg.max_latency_ms)
        return result


async def _probe_with_retry(
    site: Site,
    session: aiohttp.ClientSession,
    domain_sems: dict[str, asyncio.Semaphore],
    global_sem: asyncio.Semaphore,
    cfg: Config,
) -> ProbeResult:
    last = await probe_one(site, session, domain_sems, global_sem, cfg)
    for attempt in range(cfg.retries):
        if last.status != "fail":
            break
        await asyncio.sleep(1.0 * (3**attempt))
        last = await probe_one(site, session, domain_sems, global_sem, cfg)
    return last


def _checkpoint(
    cfg: Config,
    completed: list[ProbeResult],
    base_streaks: dict[str, int] | None,
    *,
    done: int,
    total: int,
) -> None:
    """中途落盘：超时取消时仍能留下 streak / 半成品报告。"""
    if base_streaks is not None:
        save_streaks(cfg, update_streaks_from_results(dict(base_streaks), completed))
    summary: dict[str, int] = defaultdict(int)
    summary["total"] = len(completed)
    for r in completed:
        summary[r.status] = summary.get(r.status, 0) + 1
    for key in ("ok", "degraded", "fail", "skipped"):
        summary.setdefault(key, 0)
    runner = os.environ.get("TVBOXGEN_RUNNER") or (
        "gha-linux" if os.environ.get("GITHUB_ACTIONS") else "local"
    )
    report = Report(
        generated_at=_now(),
        runner=runner,
        summary=dict(summary),
        sites=list(completed),
    )
    cfg.report_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.report_path.write_text(dumps(report.to_dict()), encoding="utf-8")
    log_kv(
        log,
        logging.INFO,
        "probe_checkpoint",
        done=done,
        probe_total=total,
        path=str(cfg.report_path),
        ok=summary.get("ok", 0),
        degraded=summary.get("degraded", 0),
        fail=summary.get("fail", 0),
        skipped=summary.get("skipped", 0),
    )


async def run_probe(
    sites: list[Site],
    cfg: Config,
    *,
    base_streaks: dict[str, int] | None = None,
) -> Report:
    global_sem = asyncio.Semaphore(cfg.concurrency)
    domain_sems: dict[str, asyncio.Semaphore] = {}
    timeout = aiohttp.ClientTimeout(
        sock_connect=cfg.timeout_connect,
        sock_read=cfg.timeout_read,
        total=15,
    )
    connector = aiohttp.TCPConnector(limit=cfg.concurrency * 2, ttl_dns_cache=True)
    headers = {"User-Agent": UA, "Accept": "application/json, text/plain, */*"}
    total = len(sites)
    n_sites = sum(1 for s in sites if s.kind != "live")
    n_lives = total - n_sites
    log_kv(log, logging.INFO, "probe_start", total=total, sites=n_sites, lives=n_lives)

    done = 0
    completed: list[ProbeResult] = []
    checkpoint_every = 200

    async with aiohttp.ClientSession(
        connector=connector, headers=headers, timeout=timeout
    ) as session:

        async def _one(site: Site) -> ProbeResult:
            nonlocal done
            r = await _probe_with_retry(site, session, domain_sems, global_sem, cfg)
            done += 1
            completed.append(r)
            if done == 1 or done % 50 == 0 or done == total:
                log_kv(
                    log,
                    logging.INFO,
                    "probe_progress",
                    done=done,
                    total=total,
                    key=site.key,
                    status=r.status,
                )
            if done % checkpoint_every == 0:
                _checkpoint(
                    cfg, completed, base_streaks, done=done, total=total
                )
            return r

        results = list(await asyncio.gather(*[_one(site) for site in sites]))

    summary: dict[str, int] = defaultdict(int)
    summary["total"] = len(results)
    for r in results:
        summary[r.status] = summary.get(r.status, 0) + 1
    for key in ("ok", "degraded", "fail", "skipped"):
        summary.setdefault(key, 0)

    runner = os.environ.get("TVBOXGEN_RUNNER") or (
        "gha-linux" if os.environ.get("GITHUB_ACTIONS") else "local"
    )
    return Report(
        generated_at=_now(),
        runner=runner,
        summary=dict(summary),
        sites=list(results),
    )


def save_report(report: Report, cfg: Config) -> Path:
    """写入本轮报告与按日历史快照。"""
    cfg.report_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.history_dir.mkdir(parents=True, exist_ok=True)
    text = dumps(report.to_dict())
    cfg.report_path.write_text(text, encoding="utf-8")
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    hist = cfg.history_dir / f"{day}.json"
    hist.write_text(text, encoding="utf-8")
    log_kv(
        log,
        logging.INFO,
        "probe_report_saved",
        path=str(cfg.report_path),
        history=str(hist),
        **report.summary,
    )
    return cfg.report_path


def probe(cfg: Config) -> Report:
    """探测入口：熔断过滤 → 并发探测 → 更新 streak → 落盘报告。"""
    log_kv(log, logging.INFO, "probe_begin", lives=cfg.probe_lives)
    targets = load_probe_targets(cfg)
    streaks = load_streaks(cfg)
    limit = cfg.fail_streak_limit

    to_probe: list = []
    pre_skipped: list[ProbeResult] = []
    for site in targets:
        if should_skip_probe(site.key, streaks, limit):
            pre_skipped.append(skipped_result(site.key, streaks.get(site.key, limit)))
        else:
            to_probe.append(site)

    log_kv(
        log,
        logging.INFO,
        "probe_filter",
        total=len(targets),
        probe=len(to_probe),
        streak_skip=len(pre_skipped),
        fail_streak_limit=limit,
    )

    if to_probe:
        report = asyncio.run(run_probe(to_probe, cfg, base_streaks=streaks))
        report.sites = list(report.sites) + pre_skipped
        # refresh summary
        summary: dict[str, int] = defaultdict(int)
        summary["total"] = len(report.sites)
        for r in report.sites:
            summary[r.status] = summary.get(r.status, 0) + 1
        for key in ("ok", "degraded", "fail", "skipped"):
            summary.setdefault(key, 0)
        report.summary = dict(summary)
    else:
        runner = os.environ.get("TVBOXGEN_RUNNER") or (
            "gha-linux" if os.environ.get("GITHUB_ACTIONS") else "local"
        )
        summary = defaultdict(int)
        summary["total"] = len(pre_skipped)
        summary["skipped"] = len(pre_skipped)
        for key in ("ok", "degraded", "fail"):
            summary.setdefault(key, 0)
        report = Report(
            generated_at=_now(),
            runner=runner,
            summary=dict(summary),
            sites=pre_skipped,
        )

    streaks = update_streaks_from_results(streaks, report.sites)
    save_streaks(cfg, streaks)
    save_report(report, cfg)

    # 逐条输出不可用源，方便 CI / 本地定位
    by_site = {s.key: s for s in targets}
    fail_n = skip_n = 0
    for r in report.sites:
        if r.status not in {"fail", "skipped"}:
            continue
        site = by_site.get(r.key)
        kind = site.kind if site else "unknown"
        name = site.name if site else r.key
        target = (site.api or site.url) if site else ""
        if r.status == "fail":
            fail_n += 1
        else:
            skip_n += 1
        log_unavailable(
            stage="probe",
            kind=kind,
            key=r.key,
            name=name,
            target=target,
            reason=r.error or r.status,
            status=r.status,
            probe_level=r.level,
            latency_ms=r.latency_ms,
            http_code=r.http_code if r.http_code is not None else "",
        )
    log_kv(
        log,
        logging.INFO,
        "probe_unavailable_summary",
        fail=fail_n,
        skipped=skip_n,
        ok=report.summary.get("ok", 0),
        degraded=report.summary.get("degraded", 0),
    )
    return report
