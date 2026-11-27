"""连续探测失败计数：达到阈值后跳过探测以减负。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from tvboxgen.config import Config
from tvboxgen.logger import get_logger, log_kv
from tvboxgen.models import ProbeResult

log = get_logger()


def streak_path(cfg: Config) -> Path:
    """失败计数文件路径。"""
    return cfg.root / "data" / "fail_streak.json"


def _dumps(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def load_streaks(cfg: Config) -> dict[str, int]:
    """读取 key → 连续失败次数。"""
    path = streak_path(cfg)
    if not path.is_file():
        log_kv(log, logging.INFO, "streak_load", path=str(path), count=0, empty=True)
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log_kv(log, logging.WARNING, "streak_load_fail", path=str(path), error=str(exc))
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for k, v in raw.items():
        try:
            out[str(k)] = max(0, int(v))
        except (TypeError, ValueError):
            continue
    log_kv(log, logging.INFO, "streak_load", path=str(path), count=len(out))
    return out


def save_streaks(cfg: Config, streaks: dict[str, int]) -> None:
    """保存失败计数（去掉 0 以减小文件）。"""
    path = streak_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = {k: v for k, v in sorted(streaks.items()) if v > 0}
    path.write_text(_dumps(cleaned), encoding="utf-8")
    log_kv(log, logging.INFO, "streak_save", path=str(path), marked=len(cleaned))


def should_skip_probe(key: str, streaks: dict[str, int], limit: int) -> bool:
    """是否因连续失败达到阈值而跳过探测。"""
    if limit <= 0:
        return False
    return streaks.get(key, 0) >= limit


def skipped_result(key: str, streak: int) -> ProbeResult:
    """生成因熔断而跳过的探测结果。"""
    from datetime import datetime, timezone

    return ProbeResult(
        key=key,
        status="skipped",
        level="L0",
        latency_ms=0,
        score=0.0,
        http_code=None,
        error=f"fail_streak>={streak}",
        checked_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def update_streaks_from_results(
    streaks: dict[str, int],
    results: list[ProbeResult],
) -> dict[str, int]:
    """根据本轮结果更新计数：ok/degraded 清零，fail +1，熔断跳过保持。"""
    out = dict(streaks)
    reset = inc = keep = 0
    for r in results:
        if r.status in {"ok", "degraded"}:
            out[r.key] = 0
            reset += 1
        elif r.status == "fail":
            out[r.key] = out.get(r.key, 0) + 1
            inc += 1
        elif r.status == "skipped" and r.error and r.error.startswith("fail_streak"):
            keep += 1
            continue
        elif r.status == "skipped":
            # searchable=0 等，不惩罚
            continue
    log_kv(log, logging.INFO, "streak_update", reset=reset, incremented=inc, keep_skip=keep)
    return out
