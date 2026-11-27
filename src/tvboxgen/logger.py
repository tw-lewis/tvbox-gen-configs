"""结构化日志（key=value），方便 CI 里 grep。"""

from __future__ import annotations

import logging
import sys
from typing import Any


class KeyValueFormatter(logging.Formatter):
    """将日志格式化为 level=... msg=... k=v ..."""

    def format(self, record: logging.LogRecord) -> str:
        base = f"level={record.levelname.lower()} msg={record.getMessage()}"
        extras = getattr(record, "kv", None)
        if isinstance(extras, dict) and extras:
            parts = " ".join(f"{k}={v}" for k, v in extras.items())
            return f"{base} {parts}"
        return base


def get_logger(name: str = "tvboxgen") -> logging.Logger:
    """获取全局 logger（单例 handler）。"""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(KeyValueFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def log_kv(logger: logging.Logger, level: int, msg: str, **kv: Any) -> None:
    """带结构化字段的日志。"""
    logger.log(level, msg, extra={"kv": kv})
