"""种子、探测结果与报告的数据模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse


@dataclass
class Site:
    key: str
    name: str
    api: str
    type: int = 1
    searchable: int = 1
    quickSearch: int = 1
    filterable: int = 1
    ext: str = ""
    tags: list[str] = field(default_factory=list)
    added_at: str = ""
    note: str = ""
    kind: str = "site"  # site=点播 | live=直播
    # 直播专用字段
    url: str = ""
    group: str = ""
    logo: str = ""
    origin: str = "trusted"  # trusted | pending

    @property
    def domain(self) -> str:
        target = self.api or self.url
        try:
            host = urlparse(target).hostname or ""
        except Exception:
            host = ""
        return host.lower()

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, kind: str = "site", origin: str = "trusted"
    ) -> Site:
        if kind == "live":
            return cls(
                key=str(data.get("key") or data.get("name") or ""),
                name=str(data.get("name") or ""),
                api="",
                url=str(data.get("url") or ""),
                group=str(data.get("group") or ""),
                logo=str(data.get("logo") or ""),
                tags=list(data.get("tags") or []),
                added_at=str(data.get("added_at") or ""),
                note=str(data.get("note") or ""),
                kind="live",
                origin=origin,
            )
        return cls(
            key=str(data["key"]),
            name=str(data["name"]),
            api=str(data["api"]),
            type=int(data.get("type", 1)),
            searchable=int(data.get("searchable", 1)),
            quickSearch=int(data.get("quickSearch", data.get("searchable", 1))),
            filterable=int(data.get("filterable", 1)),
            ext=str(data.get("ext") or ""),
            tags=list(data.get("tags") or []),
            added_at=str(data.get("added_at") or ""),
            note=str(data.get("note") or ""),
            kind="site",
            origin=origin,
            group=str(data.get("group") or ""),
        )

    def to_index_site(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "key": self.key,
            "name": self.name,
            "type": self.type,
            "api": self.api,
            "searchable": self.searchable,
            "quickSearch": self.quickSearch,
            "filterable": self.filterable,
        }
        if self.ext:
            out["ext"] = self.ext
        return out

    def to_index_live(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url,
            "group": self.group,
            "logo": self.logo,
        }


@dataclass
class ProbeChecks:
    reachable: bool = False
    json_ok: bool = False
    shape_ok: bool = False
    search_ok: bool = False

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)


@dataclass
class ProbeResult:
    key: str
    status: str  # ok | degraded | fail | skipped
    level: str = "L0"
    latency_ms: int = 0
    score: float = 0.0
    http_code: int | None = None
    checks: ProbeChecks = field(default_factory=ProbeChecks)
    error: str | None = None
    checked_at: str = ""

    @property
    def ok(self) -> bool:
        return self.status in {"ok", "degraded"}

    def as_fail(self, error: str | None = None) -> ProbeResult:
        self.status = "fail"
        if error:
            self.error = error
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "status": self.status,
            "level": self.level,
            "latency_ms": self.latency_ms,
            "score": round(self.score, 2),
            "http_code": self.http_code,
            "checks": self.checks.to_dict(),
            "error": self.error,
            "checked_at": self.checked_at,
        }


@dataclass
class Report:
    generated_at: str
    runner: str
    summary: dict[str, int]
    sites: list[ProbeResult] = field(default_factory=list)

    def by_key(self) -> dict[str, ProbeResult]:
        return {s.key: s for s in self.sites}

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "runner": self.runner,
            "summary": self.summary,
            "sites": [s.to_dict() for s in self.sites],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Report:
        sites: list[ProbeResult] = []
        for item in data.get("sites") or []:
            checks_raw = item.get("checks") or {}
            sites.append(
                ProbeResult(
                    key=item["key"],
                    status=item.get("status", "fail"),
                    level=item.get("level", "L0"),
                    latency_ms=int(item.get("latency_ms") or 0),
                    score=float(item.get("score") or 0),
                    http_code=item.get("http_code"),
                    checks=ProbeChecks(
                        reachable=bool(checks_raw.get("reachable")),
                        json_ok=bool(checks_raw.get("json_ok")),
                        shape_ok=bool(checks_raw.get("shape_ok")),
                        search_ok=bool(checks_raw.get("search_ok")),
                    ),
                    error=item.get("error"),
                    checked_at=str(item.get("checked_at") or ""),
                )
            )
        return cls(
            generated_at=str(data.get("generated_at") or ""),
            runner=str(data.get("runner") or ""),
            summary=dict(data.get("summary") or {}),
            sites=sites,
        )
