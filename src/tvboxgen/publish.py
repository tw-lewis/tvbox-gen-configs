"""提交产物到 git，并可选清理 jsDelivr 缓存。"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

import requests

from tvboxgen.config import Config
from tvboxgen.logger import get_logger, log_kv

log = get_logger()


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """执行 git 子进程。"""
    log_kv(log, logging.INFO, "git_cmd", cmd=" ".join(cmd))
    return subprocess.run(cmd, cwd=cwd, check=False, text=True, capture_output=True)


def _summary_from_report(cfg: Config) -> str:
    """从探测报告摘要拼 commit message。"""
    path = cfg.report_path
    if not path.is_file():
        return "ok=? fail=?"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        s = data.get("summary") or {}
        return f"ok={s.get('ok', '?')} fail={s.get('fail', '?')}"
    except (OSError, ValueError):
        return "ok=? fail=?"


def purge_jsdelivr(cfg: Config) -> bool:
    """清理 jsDelivr 上 dist/index.json 的缓存。"""
    url = f"https://purge.jsdelivr.net/gh/{cfg.owner}/{cfg.name}@{cfg.branch}/dist/index.json"
    log_kv(log, logging.INFO, "purge_start", url=url)
    try:
        resp = requests.get(url, timeout=15)
        ok = resp.status_code < 400
        log_kv(
            log,
            logging.INFO if ok else logging.WARNING,
            "purge_done",
            url=url,
            code=resp.status_code,
        )
        return ok
    except requests.RequestException as exc:
        log_kv(log, logging.WARNING, "purge_failed", error=str(exc))
        return False


def publish(cfg: Config, *, local: bool = False, do_purge: bool = True) -> int:
    """暂存 sources/dist/data，有变更则 commit；非 local 时 push。"""
    log_kv(log, logging.INFO, "publish_start", local=local, purge=do_purge, root=str(cfg.root))
    root = cfg.root
    add = _run(
        [
            "git",
            "add",
            "sources/",
            "dist/index.json",
            "dist/lives.txt",
            "dist/subscriptions.json",
            "dist/urls.json",
            "data/",
        ],
        cwd=root,
    )
    if add.returncode != 0:
        log_kv(log, logging.ERROR, "git_add_failed", stderr=add.stderr.strip())
        return 1

    diff = _run(["git", "diff", "--cached", "--quiet"], cwd=root)
    if diff.returncode == 0:
        log_kv(log, logging.INFO, "publish_skip", reason="no_changes")
        return 0

    msg = f"chore(auto): refresh sources ({_summary_from_report(cfg)}) [skip ci]"
    commit = _run(["git", "commit", "-m", msg], cwd=root)
    if commit.returncode != 0:
        log_kv(log, logging.ERROR, "git_commit_failed", stderr=commit.stderr.strip())
        return 1
    log_kv(log, logging.INFO, "git_committed", message=msg)

    if local:
        log_kv(log, logging.INFO, "publish_done", push=False)
        return 0

    push = _run(["git", "push"], cwd=root)
    if push.returncode != 0:
        log_kv(log, logging.ERROR, "git_push_failed", stderr=push.stderr.strip())
        return 1
    log_kv(log, logging.INFO, "git_pushed")

    if do_purge:
        purge_jsdelivr(cfg)
    log_kv(log, logging.INFO, "publish_done", push=True)
    return 0
