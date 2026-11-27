"""拉取远程采集清单，解析后追加合并到 pending/trusted。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from tvboxgen.build import dumps
from tvboxgen.config import Config, Upstream
from tvboxgen.diag import log_duplicate, log_unavailable
from tvboxgen.logger import get_logger, log_kv

log = get_logger()

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
MAX_BODY = 4 << 20
REFUSE_WORDS = ("adult", "nsfw", "porn", "hentai", "18+", "r18")
STRIP_SUFFIX = ("影视资源站", "资源站", "采集站", "接口站", "资源", "影视站")


def fetch_url(url: str, timeout: float = 40.0) -> bytes:
    if _refuse_url(url):
        raise ValueError(f"refused feed url: {url}")
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.get(
                url,
                timeout=timeout,
                headers={"User-Agent": UA, "Accept": "text/plain,application/json,*/*"},
            )
            if resp.status_code != 200:
                last_err = RuntimeError(f"http {resp.status_code}")
                continue
            return resp.content[:MAX_BODY]
        except requests.RequestException as exc:
            last_err = exc
    raise RuntimeError(f"fetch failed: {url}: {last_err}")


def _refuse_url(raw: str) -> bool:
    u = raw.lower()
    for safe in ("no-adult", "no_adult", "noadult", "noadults"):
        u = u.replace(safe, "")
    return any(w in u for w in REFUSE_WORDS)


def _usable_url(raw: str) -> bool:
    raw = (raw or "").strip()
    if not raw.lower().startswith("http"):
        return False
    try:
        u = urlparse(raw)
    except Exception:
        return False
    return bool(u.hostname)


def _clean_name(name: str, api: str) -> str:
    name = (name or "").strip()
    if not name:
        host = urlparse(api).hostname or api
        name = host.removeprefix("www.")
    # keep letters/digits/space/-/_/CJK
    name = "".join(
        ch for ch in name if ch.isalnum() or ch in " -_" or ("\u4e00" <= ch <= "\u9fff")
    ).strip(" -_")
    changed = True
    while changed:
        changed = False
        for suffix in STRIP_SUFFIX:
            if name.endswith(suffix) and len(name) > len(suffix):
                name = name[: -len(suffix)].strip()
                changed = True
    return name or api


def _short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:4]


def _make_key(name: str, api: str, explicit: str | None = None) -> str:
    if explicit:
        return re.sub(r"[^a-zA-Z0-9_-]", "_", explicit)[:64]
    host = (urlparse(api).hostname or "src").lower().removeprefix("www.")
    host = re.sub(r"[^a-z0-9.-]", "", host).replace(".", "")
    base = re.sub(r"[^a-zA-Z0-9_]", "", name)[:24] or host[:16] or "src"
    return f"{base}_{_short_hash(api)}"


_ADULT_NAME_MARKERS = (
    "adult",
    "nsfw",
    "18+",
    "r18",
    "色情",
    "番号",
    "麻豆",
    "色猫",
    "老色",
    "湿乐园",
    "黑料",
    "辣椒",
    "香蕉",
    "鲨鱼",
    "jkun",
    "a-v",
    "av资源",
    "x色",
    "搜a",
    "美少女",
    "白嫖",
    "清水源",
    "香系源",
    "细胞网",
    "奶香",
    "大x子",
    "精品x",
)


def _is_adult_item(item: dict[str, Any]) -> bool:
    if item.get("isAdult") or item.get("is_adult"):
        return True
    for field in ("category", "type", "group", "name", "tags", "note"):
        val = item.get(field)
        if isinstance(val, list):
            text = " ".join(str(x) for x in val).lower()
        else:
            text = str(val or "").lower()
        if any(w in text for w in _ADULT_NAME_MARKERS):
            return True
    return False


def _disabled(item: dict[str, Any]) -> bool:
    if "enabled" in item and item["enabled"] is False:
        return True
    if "isEnabled" in item and item["isEnabled"] is False:
        return True
    return False


def _item_api(item: dict[str, Any]) -> str:
    for key in ("api", "url", "baseUrl"):
        v = item.get(key)
        if isinstance(v, str) and v.strip():
            # live entries use url for stream — handled separately
            return v.strip().rstrip("/")
    return ""


def _normalize_site(item: dict[str, Any], *, prefer_api: bool = True) -> dict[str, Any] | None:
    if _is_adult_item(item) or _disabled(item):
        return None
    api = ""
    if prefer_api:
        api = (item.get("api") or item.get("baseUrl") or "").strip().rstrip("/")
        if not api:
            # OuonnkiTV / MoonTV style: url is the CMS api endpoint
            cand = str(item.get("url") or item.get("detailUrl") or "").strip().rstrip("/")
            if _usable_url(cand) and not any(
                cand.lower().endswith(ext) for ext in (".m3u8", ".m3u", ".flv")
            ):
                api = cand
    else:
        api = _item_api(item)
    if not _usable_url(api):
        return None
    # skip obvious live playlists in site parse
    if any(api.lower().endswith(ext) for ext in (".m3u8", ".m3u", ".flv")):
        return None
    name = _clean_name(str(item.get("name") or item.get("title") or ""), api)
    key = _make_key(name, api, explicit=str(item.get("key") or item.get("id") or "") or None)
    site_type = item.get("type", 1)
    try:
        site_type = int(site_type)
    except (TypeError, ValueError):
        site_type = 1
    searchable = int(item.get("searchable", 1) or 0)
    return {
        "key": key,
        "name": name,
        "api": api,
        "type": site_type if site_type in (0, 1, 3, 4) else 1,
        "searchable": searchable,
        "quickSearch": int(item.get("quickSearch", searchable) or 0),
        "filterable": int(item.get("filterable", 1) or 0),
        "ext": str(item.get("ext") or ""),
        "tags": list(item.get("tags") or (["点播", str(item.get("group") or "综合")])),
        "added_at": str(item.get("added_at") or date.today().isoformat()),
        "note": str(item.get("note") or ""),
    }


def _normalize_live(item: dict[str, Any]) -> dict[str, Any] | None:
    if _is_adult_item(item) or _disabled(item):
        return None
    url = str(item.get("url") or "").strip()
    if not _usable_url(url):
        return None
    name = str(item.get("name") or "").strip() or (urlparse(url).hostname or "live")
    key = _make_key(name, url, explicit=str(item.get("key") or "") or None)
    return {
        "key": key,
        "name": name,
        "url": url,
        "group": str(item.get("group") or "直播"),
        "logo": str(item.get("logo") or ""),
        "added_at": str(item.get("added_at") or date.today().isoformat()),
        "note": str(item.get("note") or ""),
    }


def _from_items(raw_items: list[Any], *, kind: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        item = _normalize_site(raw) if kind == "site" else _normalize_live(raw)
        if not item:
            continue
        fp = item.get("api") or item.get("url") or ""
        if fp in seen:
            continue
        seen.add(fp)
        out.append(item)
    return out


def _strip_json_comments(text: str) -> str:
    """去掉 // 与 /* */ 注释，保留字符串字面量内的斜杠。"""
    out: list[str] = []
    i = 0
    n = len(text)
    in_str = False
    esc = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            i += 2
            while i < n and text[i] not in "\r\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i = min(i + 2, n)
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _loads_json_lenient(text: str) -> Any | None:
    """解析 TVBox 配置常见的带注释 JSON；失败返回 None。"""
    raw = text.lstrip("\ufeff").strip()
    if not raw:
        return None
    candidates = [raw]
    stripped = _strip_json_comments(raw).strip()
    if stripped != raw:
        candidates.append(stripped)
    for cand in candidates:
        for start_ch in ("{", "["):
            idx = cand.find(start_ch)
            if idx < 0:
                continue
            blob = cand[idx:]
            try:
                return json.loads(blob)
            except json.JSONDecodeError:
                continue
    return None


def parse_sites_payload(data: bytes, *, fmt: str = "auto") -> list[dict[str, Any]]:
    """解析远程点播清单。fmt: auto | json | txt。"""
    text = data.decode("utf-8", errors="ignore").lstrip("\ufeff").strip()
    fmt = (fmt or "auto").lower()
    if fmt in ("txt", "text", "lines"):
        return _parse_site_lines(text)
    if fmt == "json" or text.startswith("{") or text.startswith("[") or text.startswith("//"):
        root = _loads_json_lenient(text)
        if root is None:
            if fmt == "json":
                return []
            return _parse_site_lines(text)
        return _parse_sites_json(root)
    return _parse_site_lines(text)


def parse_lives_payload(data: bytes) -> list[dict[str, Any]]:
    text = data.decode("utf-8", errors="ignore").lstrip("\ufeff").strip()
    if text.startswith("{") or text.startswith("[") or text.startswith("//"):
        root = _loads_json_lenient(text)
        if root is None:
            return _parse_live_lines(text)
        return _parse_lives_json(root)
    return _parse_live_lines(text)


def _parse_sites_json(root: Any) -> list[dict[str, Any]]:
    if isinstance(root, list):
        return _from_items(root, kind="site")
    if not isinstance(root, dict):
        return []
    for name in ("api_site", "apiSite", "sites_map"):
        block = root.get(name)
        if isinstance(block, dict):
            items = []
            for key, val in block.items():
                if isinstance(val, dict):
                    items.append({**val, "key": val.get("key") or key})
            return _from_items(items, kind="site")
    for name in ("sites", "list", "sources"):
        arr = root.get(name)
        if isinstance(arr, list):
            # sources may use id/api shape
            normalized = []
            for x in arr:
                if not isinstance(x, dict):
                    continue
                if "key" not in x and "id" in x:
                    x = {**x, "key": x["id"]}
                normalized.append(x)
            return _from_items(normalized, kind="site")
    return []


def _parse_lives_json(root: Any) -> list[dict[str, Any]]:
    if isinstance(root, list):
        return _from_items(root, kind="live")
    if isinstance(root, dict):
        for name in ("lives", "live", "channels"):
            arr = root.get(name)
            if isinstance(arr, list):
                return _from_items(arr, kind="live")
        # single live object
        if "url" in root and "name" in root:
            return _from_items([root], kind="live")
    return []


def _parse_site_lines(text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cols = [c.strip() for c in line.split("|")]
        if len(cols) >= 2:
            name, api = cols[0], cols[1]
        else:
            name, api = "", cols[0]
        api = api.rstrip("/")
        if not _usable_url(api):
            continue
        items.append({"name": name, "api": api, "type": 1, "searchable": 1})
    return _from_items(items, kind="site")


def _parse_live_lines(text: str) -> list[dict[str, Any]]:
    """解析 DIY「分组,#genre#」+「名称,url」、管道行、以及 m3u #EXTINF。"""
    items: list[dict[str, Any]] = []
    current_group = "直播"
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line:
            continue
        # DIY TVBox live list: "央视,#genre#"
        if "#genre#" in line.lower():
            current_group = line.split(",", 1)[0].strip() or current_group
            continue
        if line.startswith("#") and not line.startswith("#EXTINF"):
            continue
        if line.startswith("#EXTINF"):
            name = line.split(",", 1)[-1].strip() if "," in line else "live"
            group = current_group
            m = re.search(r'group-title="([^"]*)"', line)
            if m and m.group(1).strip():
                group = m.group(1).strip()
            if i < len(lines):
                url = lines[i].strip()
                i += 1
                items.append({"name": name, "url": url, "group": group})
            continue
        if "|" in line and not line.lower().startswith("http"):
            cols = [c.strip() for c in line.split("|")]
            name = cols[0]
            url = cols[1] if len(cols) > 1 else ""
            group = cols[2] if len(cols) > 2 else current_group
            items.append({"name": name, "url": url, "group": group})
        elif "," in line:
            name, url = [c.strip() for c in line.split(",", 1)]
            if not _usable_url(url):
                continue
            items.append({"name": name, "url": url, "group": current_group})
        elif _usable_url(line):
            items.append({"name": "", "url": line, "group": current_group})
    return _from_items(items, kind="live")


def _upstream_origin(upstream_id: str) -> str:
    return f"upstream:{upstream_id}"


def _fingerprint(item: dict[str, Any], *, kind: str) -> str:
    raw = (item.get("api") if kind == "site" else item.get("url")) or ""
    try:
        u = urlparse(str(raw).strip())
    except Exception:
        return str(raw).strip().lower()
    host = (u.hostname or "").lower().removeprefix("www.")
    path = (u.path or "").rstrip("/").lower()
    return f"{host}{path}"


def _prepare_rows(
    incoming: list[dict[str, Any]],
    *,
    upstream_id: str,
    kind: str,
) -> list[dict[str, Any]]:
    """给采集结果打 origin/note，并按 key/fingerprint 去重。"""
    origin = _upstream_origin(upstream_id)
    out: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    seen_fps: set[str] = set()
    for item in incoming:
        row = dict(item)
        row["origin"] = origin
        if not row.get("note"):
            row["note"] = f"auto sync from {upstream_id}"
        if not row.get("added_at"):
            row["added_at"] = date.today().isoformat()
        key = str(row.get("key") or "")
        name = str(row.get("name") or key)
        target = str(row.get("api") or row.get("url") or "")
        fp = _fingerprint(row, kind=kind)
        if key and key in seen_keys:
            log_duplicate(
                stage="sync",
                kind=kind,
                by="key",
                keep_key=key,
                drop_key=key,
                keep_name=name,
                drop_name=name,
                keep_origin=origin,
                drop_origin=origin,
                target=target,
                upstream=upstream_id,
            )
            continue
        if fp and fp in seen_fps:
            log_duplicate(
                stage="sync",
                kind=kind,
                by="api" if kind == "site" else "url",
                keep_key=fp,
                drop_key=key or fp,
                keep_name=name,
                drop_name=name,
                keep_origin=origin,
                drop_origin=origin,
                target=target,
                upstream=upstream_id,
            )
            continue
        if key:
            seen_keys.add(key)
        if fp:
            seen_fps.add(fp)
        out.append(row)
    return out


def _dedupe_rows(rows: list[dict[str, Any]], *, kind: str) -> list[dict[str, Any]]:
    """跨采集源去重：先到先得。"""
    out: list[dict[str, Any]] = []
    seen_keys: dict[str, dict[str, Any]] = {}
    seen_fps: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("key") or "")
        name = str(row.get("name") or key)
        origin = str(row.get("origin") or "")
        target = str(row.get("api") or row.get("url") or "")
        fp = _fingerprint(row, kind=kind)
        if key and key in seen_keys:
            keep = seen_keys[key]
            log_duplicate(
                stage="sync",
                kind=kind,
                by="key",
                keep_key=key,
                drop_key=key,
                keep_name=str(keep.get("name") or key),
                drop_name=name,
                keep_origin=str(keep.get("origin") or ""),
                drop_origin=origin,
                target=target,
            )
            continue
        if fp and fp in seen_fps:
            keep = seen_fps[fp]
            log_duplicate(
                stage="sync",
                kind=kind,
                by="api" if kind == "site" else "url",
                keep_key=str(keep.get("key") or fp),
                drop_key=key or fp,
                keep_name=str(keep.get("name") or ""),
                drop_name=name,
                keep_origin=str(keep.get("origin") or ""),
                drop_origin=origin,
                target=target,
            )
            continue
        if key:
            seen_keys[key] = row
        if fp:
            seen_fps[fp] = row
        out.append(row)
    out.sort(key=lambda x: (str(x.get("name") or ""), str(x.get("key") or "")))
    return out


def _atomic_write_json(path: Path, rows: list[dict[str, Any]]) -> None:
    """原子覆盖写入，避免中途失败留下半成品。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = dumps(rows)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def replace_pool(path: Path, rows: list[dict[str, Any]]) -> None:
    """覆盖写入 pending 池文件。"""
    _atomic_write_json(path, rows)
    log_kv(log, logging.INFO, "sync_replace", path=str(path), total=len(rows))


def merge_into_pool(
    path: Path,
    incoming: list[dict[str, Any]],
    *,
    upstream_id: str,
    kind: str,
) -> list[dict[str, Any]]:
    """兼容旧测试：单源写入（覆盖该文件为去重后的本批结果）。"""
    rows = _prepare_rows(incoming, upstream_id=upstream_id, kind=kind)
    replace_pool(path, rows)
    return rows


# backward-compatible alias
merge_into_trusted = merge_into_pool


def is_vod_config_payload(payload: bytes) -> bool:
    """完整 TVBox 源（蜘蛛/jar/相对脚本）→ 应按仓库 URL 整包订阅，勿拆站。"""
    text = payload.decode("utf-8", errors="ignore").lstrip("\ufeff").strip()
    if not text or not (text.startswith("{") or text.startswith("//")):
        return False
    root = _loads_json_lenient(text)
    if not isinstance(root, dict):
        return False
    spider = str(root.get("spider") or "").strip()
    if spider and not spider.lower().startswith("http"):
        return True
    sites = root.get("sites")
    if not isinstance(sites, list):
        return False
    for raw in sites:
        if not isinstance(raw, dict):
            continue
        try:
            site_type = int(raw.get("type", 1))
        except (TypeError, ValueError):
            site_type = 1
        api = str(raw.get("api") or "").strip()
        jar = str(raw.get("jar") or "").strip()
        if site_type == 3:
            return True
        if api.startswith(("./", "../", "csp_")) or api.endswith((".js", ".py", ".jar")):
            return True
        if jar and not jar.lower().startswith("http"):
            return True
    return False


def _subscription_row(upstream: Upstream, *, uid: str) -> dict[str, Any]:
    return {
        "id": uid,
        "key": uid,
        "name": upstream.name or uid,
        "url": upstream.url,
        "kind": "vod_config",
        "origin": _upstream_origin(uid),
        "note": f"auto sync subscription from {uid}",
        "added_at": date.today().isoformat(),
    }


def _dedupe_subscriptions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_ids: dict[str, dict[str, Any]] = {}
    seen_urls: dict[str, dict[str, Any]] = {}
    for row in rows:
        sid = str(row.get("id") or row.get("key") or "")
        name = str(row.get("name") or sid)
        origin = str(row.get("origin") or "")
        url = str(row.get("url") or "").strip()
        url_l = url.lower()
        if sid and sid in seen_ids:
            keep = seen_ids[sid]
            log_duplicate(
                stage="sync",
                kind="subscription",
                by="key",
                keep_key=sid,
                drop_key=sid,
                keep_name=str(keep.get("name") or sid),
                drop_name=name,
                keep_origin=str(keep.get("origin") or ""),
                drop_origin=origin,
                target=url,
            )
            continue
        if url_l and url_l in seen_urls:
            keep = seen_urls[url_l]
            log_duplicate(
                stage="sync",
                kind="subscription",
                by="url",
                keep_key=str(keep.get("id") or keep.get("key") or ""),
                drop_key=sid,
                keep_name=str(keep.get("name") or ""),
                drop_name=name,
                keep_origin=str(keep.get("origin") or ""),
                drop_origin=origin,
                target=url,
            )
            continue
        if sid:
            seen_ids[sid] = row
        if url_l:
            seen_urls[url_l] = row
        out.append(row)
    out.sort(key=lambda x: (str(x.get("name") or ""), str(x.get("id") or "")))
    return out


def _resolve_kind(upstream: Upstream, payload: bytes) -> str:
    if upstream.kind in ("subscriptions", "subscription", "vod_config", "config"):
        return "subscriptions"
    if upstream.kind in ("lives", "live"):
        return "lives"
    if upstream.kind in ("sites", "site", "vod"):
        # 点播清单若实为完整 TVBox 源，改走 subscriptions
        if is_vod_config_payload(payload):
            return "subscriptions"
        return "sites"
    # auto-detect
    if is_vod_config_payload(payload):
        return "subscriptions"
    text = payload.decode("utf-8", errors="ignore").lstrip("\ufeff").strip()
    if text.startswith("{") or text.startswith("["):
        try:
            root = json.loads(text)
        except json.JSONDecodeError:
            root = _loads_json_lenient(text)
        if isinstance(root, dict) and isinstance(root.get("lives"), list) and not root.get("sites"):
            return "lives"
        if (
            isinstance(root, list)
            and root
            and isinstance(root[0], dict)
            and "url" in root[0]
            and "api" not in root[0]
        ):
            return "lives"
    if upstream.url.lower().endswith((".m3u", ".m3u8")) or "live" in upstream.url.lower():
        return "lives"
    return "sites"


def sync_upstreams(cfg: Config, *, dry_run: bool = False) -> dict[str, int]:
    """拉取全部启用采集源，成功后**覆盖** pending；中途失败不改写旧 pending。"""
    stats = {"sites": 0, "lives": 0, "subscriptions": 0, "feeds": 0, "errors": 0, "written": 0}
    enabled = [u for u in cfg.upstreams if u.enabled and u.url]
    log_kv(
        log,
        logging.INFO,
        "sync_start",
        dry_run=dry_run,
        configured=len(cfg.upstreams),
        enabled=len(enabled),
        mode="overwrite_pending",
    )
    if not enabled:
        log_kv(log, logging.INFO, "sync_skip", reason="no_enabled_upstream")
        return stats

    collected_sites: list[dict[str, Any]] = []
    collected_lives: list[dict[str, Any]] = []
    collected_subs: list[dict[str, Any]] = []
    completed = False

    try:
        for i, upstream in enumerate(enabled, start=1):
            uid = upstream.id or _short_hash(upstream.url)
            log_kv(
                log,
                logging.INFO,
                "sync_feed_begin",
                index=i,
                total=len(enabled),
                id=uid,
                name=upstream.name or uid,
                url=upstream.url,
                kind=upstream.kind,
                format=upstream.format,
            )
            try:
                payload = fetch_url(upstream.url, timeout=cfg.timeout_read + 30)
                log_kv(log, logging.INFO, "sync_fetch_ok", id=uid, bytes=len(payload))
            except Exception as exc:  # noqa: BLE001 — 单源失败不中断整轮
                log_unavailable(
                    stage="sync",
                    kind=str(upstream.kind or "feed"),
                    key=uid,
                    name=upstream.name or uid,
                    target=upstream.url,
                    reason=f"fetch_fail:{exc}",
                )
                stats["errors"] += 1
                continue

            kind = _resolve_kind(upstream, payload)
            site_items: list[dict[str, Any]] = []
            live_items: list[dict[str, Any]] = []
            sub_items: list[dict[str, Any]] = []
            fmt = (upstream.format or "auto").lower()
            if kind == "subscriptions":
                sub_items = [_subscription_row(upstream, uid=uid)]
            elif kind == "lives":
                live_items = parse_lives_payload(payload)
            else:
                site_items = parse_sites_payload(payload, fmt=fmt)
                if upstream.kind in ("auto", ""):
                    live_items = parse_lives_payload(payload)
            log_kv(
                log,
                logging.INFO,
                "sync_parsed",
                id=uid,
                kind=kind,
                sites=len(site_items),
                lives=len(live_items),
                subscriptions=len(sub_items),
            )

            if site_items:
                collected_sites.extend(
                    _prepare_rows(site_items, upstream_id=uid, kind="site")
                )
            if live_items:
                live_uid = uid if kind == "lives" else f"{uid}-lives"
                collected_lives.extend(
                    _prepare_rows(live_items, upstream_id=live_uid, kind="live")
                )
            if sub_items:
                collected_subs.extend(sub_items)

            stats["feeds"] += 1
            log_kv(
                log,
                logging.INFO,
                "sync_feed_done",
                id=uid,
                kind=kind,
                sites=len(site_items),
                lives=len(live_items),
                subscriptions=len(sub_items),
                target=upstream.target or "pending",
            )
        completed = True
    except Exception as exc:  # noqa: BLE001 — 保旧 pending，后续 probe 仍可用
        log_kv(log, logging.ERROR, "sync_aborted", error=str(exc))
        stats["errors"] += 1
        log_kv(log, logging.INFO, "sync_keep_previous_pending", reason="aborted")
        log_kv(log, logging.INFO, "sync_done", **stats)
        return stats

    sites = _dedupe_rows(collected_sites, kind="site")
    lives = _dedupe_rows(collected_lives, kind="live")
    subs = _dedupe_subscriptions(collected_subs)
    stats["sites"] = len(sites)
    stats["lives"] = len(lives)
    stats["subscriptions"] = len(subs)

    if dry_run:
        log_kv(
            log,
            logging.INFO,
            "sync_dry",
            sites=len(sites),
            lives=len(lives),
            subscriptions=len(subs),
        )
        log_kv(log, logging.INFO, "sync_done", **stats)
        return stats

    if not completed:
        log_kv(log, logging.INFO, "sync_keep_previous_pending", reason="incomplete")
        log_kv(log, logging.INFO, "sync_done", **stats)
        return stats

    if stats["feeds"] == 0:
        # 全部采集源失败：保留旧 pending，避免写空
        log_kv(log, logging.WARNING, "sync_keep_previous_pending", reason="all_feeds_failed")
        log_kv(log, logging.INFO, "sync_done", **stats)
        return stats

    pending = cfg.pending_dir
    replace_pool(pending / "sites.json", sites)
    replace_pool(pending / "lives.json", lives)
    replace_pool(pending / "subscriptions.json", subs)
    stats["written"] = 1
    log_kv(log, logging.INFO, "sync_done", **stats)
    return stats
