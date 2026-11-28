"""源可用性 / 重复合并诊断日志（便于 CI grep）。"""

from __future__ import annotations

import logging
from typing import Any

from tvboxgen.logger import get_logger, log_kv

log = get_logger()

# log_kv(logger, level, msg, **kv) — these names cannot appear in **kv.
_LOG_KV_COLLISIONS = {
    "level": "probe_level",
    "msg": "detail",
    "logger": "logger_name",
}


def _forward_extra(extra: dict[str, Any]) -> dict[str, Any]:
    """Rename kwargs that would collide with log_kv's positional parameters."""
    out = dict(extra)
    for src, dest in _LOG_KV_COLLISIONS.items():
        if src in out:
            out.setdefault(dest, out.pop(src))
    return out


def log_unavailable(
    *,
    stage: str,
    kind: str,
    key: str,
    reason: str,
    name: str = "",
    target: str = "",
    **extra: Any,
) -> None:
    """不可用源：stage=probe|promote|build|cdn|sync。"""
    log_kv(
        log,
        logging.WARNING,
        "source_unavailable",
        stage=stage,
        kind=kind,
        key=key or "-",
        name=name or key or "-",
        target=target or "-",
        reason=reason,
        **_forward_extra(extra),
    )


def log_duplicate(
    *,
    stage: str,
    kind: str,
    by: str,
    keep_key: str,
    drop_key: str,
    keep_name: str = "",
    drop_name: str = "",
    keep_origin: str = "",
    drop_origin: str = "",
    target: str = "",
    action: str = "drop",
    **extra: Any,
) -> None:
    """重复合并：by=key|api|url；action=drop|override|keep_both。"""
    log_kv(
        log,
        logging.WARNING,
        "source_duplicate",
        stage=stage,
        kind=kind,
        by=by,
        action=action,
        keep_key=keep_key or "-",
        keep_name=keep_name or keep_key or "-",
        keep_origin=keep_origin or "-",
        drop_key=drop_key or "-",
        drop_name=drop_name or drop_key or "-",
        drop_origin=drop_origin or "-",
        target=target or "-",
        **_forward_extra(extra),
    )
