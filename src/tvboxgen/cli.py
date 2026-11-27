"""命令行入口：sync / probe / promote / build / publish。"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from tvboxgen.build import build
from tvboxgen.config import load_config
from tvboxgen.logger import get_logger, log_kv
from tvboxgen.probe import probe
from tvboxgen.promote import promote_trusted
from tvboxgen.publish import publish
from tvboxgen.sync import sync_upstreams

log = get_logger()


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tvboxgen", description="TVBox 配置生成器")
    p.add_argument(
        "--root",
        type=Path,
        default=None,
        help="仓库根目录（默认当前目录）",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sync", help="按 collectors/feeds.json 采集 → pending（追加去重）")
    s.add_argument("--dry-run", action="store_true", help="只拉取解析，不写文件")

    sub.add_parser("probe", help="探测 pending → data/probe_report.json")

    sub.add_parser("promote", help="探测可用的 pending∪manual → 重写 trusted")

    b = sub.add_parser(
        "build",
        help="由 trusted + 报告生成纯源/订阅源/多仓源（tvbox + subscriptions + urls）",
    )
    b.add_argument("--dry-run", action="store_true", help="只打印差异，不落盘")

    pub = sub.add_parser("publish", help="提交产物（可选 push / 清 CDN）")
    pub.add_argument("--local", action="store_true", help="只 commit，不 push")
    pub.add_argument("--no-purge", action="store_true", help="跳过 jsDelivr 清缓存")

    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = (args.root or Path.cwd()).resolve()
    log_kv(log, logging.INFO, "cli_start", cmd=args.cmd, root=str(root))
    cfg = load_config(root)
    log_kv(
        log,
        logging.INFO,
        "cli_config",
        collectors=str(cfg.collectors_dir),
        pending=str(cfg.pending_dir),
        manual=str(cfg.manual_dir),
        trusted=str(cfg.trusted_dir),
        upstreams=len(cfg.upstreams),
        probe_lives=cfg.probe_lives,
        fail_streak_limit=cfg.fail_streak_limit,
    )

    if args.cmd == "sync":
        stats = sync_upstreams(cfg, dry_run=args.dry_run)
        print(
            f"sync feeds={stats['feeds']} sites={stats['sites']} "
            f"lives={stats['lives']} subscriptions={stats.get('subscriptions', 0)} "
            f"errors={stats['errors']} written={stats.get('written', 0)}"
        )
        # 采集失败不阻断后续 probe：有成功写入或保留旧 pending 都算可继续
        code = 0 if stats.get("feeds") or stats.get("written") or not stats["errors"] else 1
        log_kv(log, logging.INFO, "cli_done", cmd="sync", exit=code, **stats)
        return code

    if args.cmd == "probe":
        report = probe(cfg)
        print(
            f"probe ok={report.summary.get('ok', 0)} "
            f"degraded={report.summary.get('degraded', 0)} "
            f"fail={report.summary.get('fail', 0)} "
            f"skipped={report.summary.get('skipped', 0)}"
        )
        log_kv(log, logging.INFO, "cli_done", cmd="probe", **report.summary)
        return 0

    if args.cmd == "promote":
        stats = promote_trusted(cfg)
        print(
            f"promote sites={stats['sites']} lives={stats['lives']} "
            f"subscriptions={stats.get('subscriptions', 0)}"
        )
        log_kv(log, logging.INFO, "cli_done", cmd="promote", **stats)
        return 0

    if args.cmd == "build":
        index = build(cfg, dry_run=args.dry_run)
        if not args.dry_run:
            import json as _json

            subs = (
                len(_json.loads(cfg.subscriptions_path.read_text(encoding="utf-8")))
                if cfg.subscriptions_path.is_file()
                else 0
            )
            urls = (
                len(_json.loads(cfg.urls_path.read_text(encoding="utf-8")).get("urls") or [])
                if cfg.urls_path.is_file()
                else 0
            )
            print(
                f"build pure_sites={len(index['sites'])} lives={len(index['lives'])} "
                f"subscriptions={subs} depot_urls={urls}"
            )
        log_kv(
            log,
            logging.INFO,
            "cli_done",
            cmd="build",
            dry_run=args.dry_run,
            sites=len(index["sites"]),
            lives=len(index["lives"]),
        )
        return 0

    if args.cmd == "publish":
        code = publish(cfg, local=args.local, do_purge=not args.no_purge)
        log_kv(log, logging.INFO, "cli_done", cmd="publish", exit=code, local=args.local)
        return code

    log_kv(log, logging.ERROR, "cli_unknown_cmd", cmd=args.cmd)
    return 1


if __name__ == "__main__":
    sys.exit(main())
