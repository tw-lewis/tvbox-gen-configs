"""把 GitHub 资源地址改写为 cdn.jsdelivr，并校验改写后可访问。"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import unquote

import requests

from tvboxgen.logger import get_logger, log_kv

log = get_logger()

_RAW_GH = re.compile(
    r"^https?://raw\.githubusercontent\.com/"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+)/(?P<rest>.+)$",
    re.IGNORECASE,
)
_GITHUB_FILE = re.compile(
    r"^https?://(?:www\.)?github\.com/"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+)/(?:raw|blob)/(?P<rest>.+)$",
    re.IGNORECASE,
)
_ALREADY_JSD = re.compile(r"^https?://(?:cdn\.|fastly\.)?jsdelivr\.net/gh/", re.IGNORECASE)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# 进程内缓存，避免同一次 build 重复探测
_REACH_CACHE: dict[str, bool] = {}


def _split_ref_path(rest: str) -> tuple[str, str]:
    """从 raw 路径中拆出 ref 与文件路径（支持 refs/heads|tags）。"""
    rest = unquote(rest).lstrip("/")
    for prefix in ("refs/heads/", "refs/tags/"):
        if rest.startswith(prefix):
            body = rest[len(prefix) :]
            ref, _, path = body.partition("/")
            return ref, path
    ref, _, path = rest.partition("/")
    return ref, path


def github_to_jsdelivr(url: str) -> str:
    """GitHub raw/blob URL → cdn.jsdelivr；其它地址原样返回。"""
    raw = (url or "").strip()
    if not raw or not raw.lower().startswith("http"):
        return raw
    if _ALREADY_JSD.match(raw):
        return raw

    m = _RAW_GH.match(raw) or _GITHUB_FILE.match(raw)
    if not m:
        return raw
    owner = m.group("owner")
    repo = m.group("repo")
    ref, path = _split_ref_path(m.group("rest"))
    if not ref or not path:
        return raw
    return f"https://cdn.jsdelivr.net/gh/{owner}/{repo}@{ref}/{path}"


def clear_reach_cache() -> None:
    _REACH_CACHE.clear()


def url_reachable(url: str, timeout: float = 12.0) -> bool:
    """HEAD 优先，失败再 GET；2xx/3xx 视为可达。"""
    url = (url or "").strip()
    if not url.lower().startswith("http"):
        return False
    cached = _REACH_CACHE.get(url)
    if cached is not None:
        return cached

    headers = {"User-Agent": UA, "Accept": "*/*"}
    ok = False
    try:
        resp = requests.head(url, timeout=timeout, headers=headers, allow_redirects=True)
        code = resp.status_code
        if code in (403, 405):
            resp = requests.get(
                url, timeout=timeout, headers=headers, allow_redirects=True, stream=True
            )
            code = resp.status_code
            resp.close()
        ok = 200 <= code < 400
    except requests.RequestException:
        try:
            resp = requests.get(
                url, timeout=timeout, headers=headers, allow_redirects=True, stream=True
            )
            ok = 200 <= resp.status_code < 400
            resp.close()
        except requests.RequestException:
            ok = False

    _REACH_CACHE[url] = ok
    log_kv(
        log,
        logging.INFO if ok else logging.WARNING,
        "cdn_url_check",
        url=url,
        ok=ok,
    )
    return ok


def resolve_public_url(url: str, *, timeout: float = 12.0) -> str | None:
    """改写为 jsDelivr（若适用）并校验可达；不可达返回 None（不写入产物）。"""
    raw = (url or "").strip()
    if not raw:
        return None
    candidate = github_to_jsdelivr(raw)
    rewritten = candidate != raw
    if not url_reachable(candidate, timeout=timeout):
        log_kv(
            log,
            logging.WARNING,
            "cdn_url_reject",
            original=raw,
            candidate=candidate,
            rewritten=rewritten,
        )
        return None
    if rewritten:
        log_kv(log, logging.INFO, "cdn_url_rewritten", original=raw, url=candidate)
    return candidate


def _rewrite_one(url: str, *, verify: bool) -> str:
    if not verify:
        return github_to_jsdelivr(url)
    rewritten = github_to_jsdelivr(url)
    if rewritten == url:
        return url
    if url_reachable(rewritten):
        return rewritten
    log_kv(
        log,
        logging.WARNING,
        "cdn_rewrite_skip",
        original=url,
        candidate=rewritten,
        reason="unreachable",
    )
    return url


def rewrite_urls_in_obj(obj: Any, *, verify: bool = False) -> Any:
    """递归改写对象中的 URL；verify=True 时仅在 jsDelivr 可达时采用改写。"""
    if isinstance(obj, str):
        if obj.lower().startswith("http"):
            return _rewrite_one(obj, verify=verify)
        return obj
    if isinstance(obj, list):
        return [rewrite_urls_in_obj(x, verify=verify) for x in obj]
    if isinstance(obj, dict):
        return {k: rewrite_urls_in_obj(v, verify=verify) for k, v in obj.items()}
    return obj


def rewrite_diy_lives(text: str, *, verify: bool = False) -> str:
    """改写 DIY 直播文本中逗号后的播放地址。"""
    lines: list[str] = []
    for line in text.splitlines():
        if not line or line.endswith("#genre#") or "," not in line:
            lines.append(line)
            continue
        name, url = line.split(",", 1)
        url = url.strip()
        if url.lower().startswith("http"):
            lines.append(f"{name},{_rewrite_one(url, verify=verify)}")
        else:
            lines.append(line)
    if text.endswith("\n"):
        return "\n".join(lines) + "\n"
    return "\n".join(lines) + ("\n" if lines else "")
