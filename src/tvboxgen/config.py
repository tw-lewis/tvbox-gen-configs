"""加载 config.toml 与 collectors/feeds.json。"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Upstream:
    """一条采集源配置（自动解析 json 或 txt）。"""

    url: str
    enabled: bool = False
    target: str = "pending"  # pending=全量池 | trusted=少见覆盖
    id: str = ""
    name: str = ""  # 日志展示名
    kind: str = "sites"  # sites | lives | auto
    format: str = "auto"  # auto | json | txt


@dataclass
class Config:
    """运行时配置。"""

    root: Path
    owner: str = "your-name"
    name: str = "tvbox-gen-configs"
    branch: str = "main"
    trusted_dir: Path = field(default_factory=lambda: Path("sources/trusted"))
    pending_dir: Path = field(default_factory=lambda: Path("sources/pending"))
    manual_dir: Path = field(default_factory=lambda: Path("sources/manual"))
    collectors_dir: Path = field(default_factory=lambda: Path("sources/collectors"))
    pending_affects_output: bool = False
    concurrency: int = 8
    per_domain_concurrency: int = 2
    timeout_connect: float = 5
    timeout_read: float = 8
    retries: int = 1
    probe_search: bool = True
    probe_lives: bool = True
    search_keyword: str = "庆余年"
    accept_status: list[str] = field(default_factory=lambda: ["ok"])
    allow_degraded: bool = False
    max_latency_ms: int = 8000
    fail_streak_limit: int = 3
    sort: str = "score_desc"
    watchlist_keys: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    wallpaper: str = ""
    spider: str = ""
    logo: str = ""
    lives_name: str = "直播"
    pure_name: str = "纯源"
    static_parses: list[Any] = field(default_factory=list)
    static_rules: list[Any] = field(default_factory=list)
    static_doh: list[Any] = field(default_factory=list)
    static_ads: list[str] = field(default_factory=list)
    upstreams: list[Upstream] = field(default_factory=list)

    @property
    def report_path(self) -> Path:
        return self.root / "data" / "probe_report.json"

    @property
    def history_dir(self) -> Path:
        return self.root / "data" / "history"

    @property
    def index_path(self) -> Path:
        """纯源（CMS + 直播引用）。"""
        return self.root / "dist" / "index.json"

    @property
    def lives_path(self) -> Path:
        return self.root / "dist" / "lives.txt"

    @property
    def subscriptions_path(self) -> Path:
        """订阅源清单（完整 TVBox JSON URL）。"""
        return self.root / "dist" / "subscriptions.json"

    @property
    def urls_path(self) -> Path:
        """多仓源（TVBox urls depot）。"""
        return self.root / "dist" / "urls.json"

    def dist_public_url(self, filename: str) -> str:
        """jsDelivr 上 dist 文件的公开地址。"""
        return (
            f"https://cdn.jsdelivr.net/gh/{self.owner}/{self.name}"
            f"@{self.branch}/dist/{filename}"
        )


def _resolve(root: Path, value: str | Path) -> Path:
    p = Path(value)
    return p if p.is_absolute() else root / p


def _upstream_from_dict(u: dict[str, Any]) -> Upstream | None:
    url = str(u.get("url") or "").strip()
    if not url:
        return None
    return Upstream(
        url=url,
        enabled=bool(u.get("enabled", False)),
        target=str(u.get("target") or "pending"),
        id=str(u.get("id") or ""),
        name=str(u.get("name") or ""),
        kind=str(u.get("kind") or "sites"),
        format=str(u.get("format") or "auto"),
    )


def load_collectors(directory: Path) -> list[Upstream]:
    """从 sources/collectors/feeds.json 加载手动维护的采集源。"""
    path = directory / "feeds.json"
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(raw, dict) and isinstance(raw.get("feeds"), list):
        rows = raw["feeds"]
    elif isinstance(raw, list):
        rows = raw
    elif isinstance(raw, dict):
        rows = [raw]
    else:
        return []

    items: list[Upstream] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        up = _upstream_from_dict(row)
        if up is None:
            continue
        uid = up.id or up.url
        if uid in seen:
            continue
        seen.add(uid)
        items.append(up)
    return items


def load_config(root: Path | None = None, path: Path | None = None) -> Config:
    """加载仓库配置；采集源以 feeds.json 为主，toml [[upstream]] 为补充。"""
    root = (root or Path.cwd()).resolve()
    cfg_path = path or (root / "config.toml")
    raw: dict[str, Any] = {}
    if cfg_path.is_file():
        with cfg_path.open("rb") as f:
            raw = tomllib.load(f)

    repo = raw.get("repo") or {}
    sources = raw.get("sources") or {}
    probe = raw.get("probe") or {}
    build = raw.get("build") or {}
    watch = raw.get("watchlist") or {}
    static = raw.get("static") or {}

    accept = list(build.get("accept_status") or ["ok"])
    if build.get("allow_degraded") and "degraded" not in accept:
        accept.append("degraded")

    collectors_dir = _resolve(root, sources.get("collectors_dir") or "sources/collectors")
    upstreams = load_collectors(collectors_dir)
    seen = {u.id or u.url for u in upstreams}
    for u in raw.get("upstream") or []:
        if not isinstance(u, dict):
            continue
        up = _upstream_from_dict(u)
        if up is None:
            continue
        uid = up.id or up.url
        if uid in seen:
            continue
        seen.add(uid)
        upstreams.append(up)

    return Config(
        root=root,
        owner=str(repo.get("owner") or "your-name"),
        name=str(repo.get("name") or "tvbox-gen-configs"),
        branch=str(repo.get("branch") or "main"),
        trusted_dir=_resolve(root, sources.get("trusted_dir") or "sources/trusted"),
        pending_dir=_resolve(root, sources.get("pending_dir") or "sources/pending"),
        manual_dir=_resolve(root, sources.get("manual_dir") or "sources/manual"),
        collectors_dir=collectors_dir,
        pending_affects_output=bool(sources.get("pending_affects_output", False)),
        concurrency=int(probe.get("concurrency") or 8),
        per_domain_concurrency=int(probe.get("per_domain_concurrency") or 2),
        timeout_connect=float(probe.get("timeout_connect") or 5),
        timeout_read=float(probe.get("timeout_read") or 8),
        retries=int(probe.get("retries") or 1),
        probe_search=bool(probe.get("probe_search", True)),
        probe_lives=bool(probe.get("probe_lives", True)),
        search_keyword=str(probe.get("search_keyword") or "庆余年"),
        accept_status=accept,
        allow_degraded=bool(build.get("allow_degraded", False)),
        max_latency_ms=int(build.get("max_latency_ms") or 8000),
        fail_streak_limit=int(
            probe.get("fail_streak_limit") or build.get("fail_streak_limit") or 3
        ),
        sort=str(build.get("sort") or "score_desc"),
        watchlist_keys=list(watch.get("keys") or []),
        flags=list(static.get("flags") or ["qq", "iqiyi", "youku", "mgtv"]),
        wallpaper=str(static.get("wallpaper") or ""),
        spider=str(static.get("spider") or ""),
        logo=str(static.get("logo") or ""),
        lives_name=str(static.get("lives_name") or "直播"),
        pure_name=str(static.get("pure_name") or "纯源"),
        static_parses=list(static.get("parses") or []),
        static_rules=list(static.get("rules") or []),
        static_doh=list(static.get("doh") or []),
        static_ads=[str(x) for x in (static.get("ads") or [])],
        upstreams=upstreams,
    )
