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

# 工作流必须入库的 pending / trusted（及整棵 sources）
_SOURCES_PATHS = (
    "sources/pending",
    "sources/trusted",
    "sources/manual",
    "sources/collectors",
)
_DIST_PATHS = (
    "dist/tvbox.json",
    "dist/lives.txt",
    "dist/subscriptions.json",
    "dist/urls.json",
)


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
    """清理 jsDelivr 上全部 dist 产物缓存（@branch 别名常滞后，需逐文件 purge）。"""
    base = f"https://purge.jsdelivr.net/gh/{cfg.owner}/{cfg.name}@{cfg.branch}"
    paths = [
        "dist/tvbox.json",
        "dist/urls.json",
        "dist/subscriptions.json",
        "dist/lives.txt",
    ]
    all_ok = True
    for rel in paths:
        url = f"{base}/{rel}"
        log_kv(log, logging.INFO, "purge_start", url=url)
        try:
            resp = requests.get(url, timeout=20)
            ok = resp.status_code < 400
            log_kv(
                log,
                logging.INFO if ok else logging.WARNING,
                "purge_done",
                url=url,
                code=resp.status_code,
            )
            all_ok = all_ok and ok
        except requests.RequestException as exc:
            log_kv(log, logging.WARNING, "purge_failed", url=url, error=str(exc))
            all_ok = False
    return all_ok


def publish(
    cfg: Config,
    *,
    local: bool = False,
    do_purge: bool = True,
    sources_only: bool = False,
) -> int:
    """暂存并提交；默认 sources + dist + data。sources_only 时只提交 pending/trusted 等源目录。"""
    log_kv(
        log,
        logging.INFO,
        "publish_start",
        local=local,
        purge=do_purge,
        sources_only=sources_only,
        root=str(cfg.root),
    )
    root = cfg.root

    # -A：包含新增/修改/删除，确保 pending、trusted 一定入库
    add_cmd = ["git", "add", "-A", *_SOURCES_PATHS]
    if not sources_only:
        add_cmd.extend(_DIST_PATHS)
        add_cmd.append("data/")
    add = _run(add_cmd, cwd=root)
    if add.returncode != 0:
        log_kv(log, logging.ERROR, "git_add_failed", stderr=add.stderr.strip())
        return 1

    # 再显式点名 pending/trusted，避免漏掉未跟踪文件
    explicit = _run(
        [
            "git",
            "add",
            "-A",
            "sources/pending/",
            "sources/trusted/",
        ],
        cwd=root,
    )
    if explicit.returncode != 0:
        log_kv(log, logging.ERROR, "git_add_pending_trusted_failed", stderr=explicit.stderr.strip())
        return 1

    diff = _run(["git", "diff", "--cached", "--quiet"], cwd=root)
    if diff.returncode == 0:
        log_kv(log, logging.INFO, "publish_skip", reason="no_changes")
        return 0

    staged = _run(["git", "diff", "--cached", "--name-only"], cwd=root)
    log_kv(
        log,
        logging.INFO,
        "publish_staged",
        files=(staged.stdout or "").replace("\n", ",").strip(","),
    )

    if sources_only:
        msg = f"chore(auto): refresh pending/trusted ({_summary_from_report(cfg)}) [skip ci]"
    else:
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

    if do_purge and not sources_only:
        purge_jsdelivr(cfg)
    log_kv(log, logging.INFO, "publish_done", push=True)
    return 0
