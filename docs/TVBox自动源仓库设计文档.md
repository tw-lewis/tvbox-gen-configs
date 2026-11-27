# TVBox 私有配置源仓库 —— 设计文档

| 项 | 内容 |
|---|---|
| **仓库名** | **`tvbox-gen-configs`**（已定稿） |
| 仓库描述 | Private TVBox config generator, auto probe & filter sources via GitHub Actions. |
| 本地路径 | `~/proj/tvbox-gen-configs` |
| 文档版本 | v0.2（仓库名定稿） |
| 用途 | 指导在本地从零初始化一个「自维护 TVBox 配置源」仓库 |
| 技术栈 | Python 3.11+ / GitHub Actions / jsDelivr |
| 适用场景 | 个人私有仓库、自用订阅，不做公开分发 |

---

## 1. 设计目标与非目标

### 1.1 目标

1. **可复现初始化**：本地敲几条命令即可得到一个能跑通「探测 → 过滤 → 生成 → 发布」的仓库。
2. **自动体检**：定时探测种子库中的每个源，识别失效、超时、格式不合规的源。
3. **配置自动产出**：把存活源重组成 TVBox/影视仓可直接订阅的 `index.json`，并自动提交、清 CDN 缓存。
4. **可控的种子来源**：种子（sources）与产物（index.json）严格分层，人是唯一种子入口，机器只做筛选，不凭空生成源。
5. **可本地调试**：同一份代码既能在 GitHub Actions 跑，也能在本地一条命令跑，产物一致。

### 1.2 非目标（明确不做）

- ❌ **不做**全网自动发现新点播源（无统一协议，误判率高、合规风险大）。
- ❌ **不做**播放链路全链路验证（拉流、解密、试播，成本高、GitHub runner 撑不住）。
- ❌ **不做**多用户、后台管理界面、数据库。
- ❌ **不对外分发**聚合后的盗版点播接口。

> 结论先行：**自动化的边界是"做减法"，不是"做加法"。** 新源进种子库靠人工或你信任的上游订阅同步，机器只负责淘汰死源、排序、生成。

---

## 2. 合规与风险边界（必读）

| 风险 | 说明 | 缓解措施 |
|---|---|---|
| 版权 | 聚合点播爬虫接口属未授权采集 | 仓库设为 **Private**；仅本机/家庭内网订阅使用 |
| 上游投毒 | 同步他人订阅可能带进恶意跳转、劫持 | 种子分 `trusted/` 与 `pending/` 两级，`pending` 需人工确认后晋升 |
| CDN 缓存 | jsDelivr 长缓存导致"改了不生效" | update 成功后调 purge 接口清缓存 |
| Actions 配额 | 免费 runner 月度分钟数有限 | 探测间隔 ≥ 4h；单次运行设 timeout-minutes |
| IP 被封 | 高频并发探测被源站拉黑 | 全局并发上限（默认 8）、单站超时（默认 8s）、失败退避 |
| 删库 | 公开仓库易被 DMCA 投诉 | Private + 不在任何公开渠道贴订阅链接 |

---

## 3 总体架构

### 3.1 数据流

```
            ┌─────────────────┐
  人工/上游  │ sources/trusted │  ← 可信种子（人工审核过）
   ────────► │ sources/pending │  ← 待确认种子（上游同步进来）
            └────────┬────────┘
                     │  ① 合并去重
                     ▼
            ┌─────────────────┐
            │  probe (探测)   │  ② 分级探测 → 打分
            └────────┬────────┘
                     │  ③ probe_report.json
                     ▼
            ┌─────────────────┐
            │  build (生成)   │  ④ 过滤/排序/分组 → index.json
            └────────┬────────┘
                     │
                     ▼
       ⑤ 提交 main + ⑥ purge jsDelivr 缓存
                     │
                     ▼
     TVBox / 影视仓 订阅  .../index.json
```

### 3.2 分层设计原则

| 层 | 职责 | 是否可自动 | 关键约束 |
|---|---|---|---|
| 种子层 `sources/` | 提供待测源清单 | ❌ 人工/半自动 | 人是唯一入口，新增需 review |
| 探测层 `probe` | 判定存活与质量 | ✅ 全自动 | 只读，不写种子 |
| 构建层 `build` | 生成产物 | ✅ 全自动 | 幂等，相同输入必得相同输出 |
| 发布层 `publish` | 提交 + 清缓存 | ✅ 全自动 | 无变更不提交 |
| 消费层 TVBox | 拉取订阅 | ✅ 客户端轮询 | 无推送，只有拉 |

> **幂等性是本设计的第一原则**：跑两次 build，git 必须没有 diff。否则会污染提交历史、触发无意义的 CDN 清理。

---

## 4 仓库结构（初始化后的目标形态）

```
tvbox-gen-configs/
├── .github/
│   └── workflows/
│       ├── probe.yml            # 定时探测（cron + dispatch）
│       ├── update.yml           # 生成产物 + 提交 + 清缓存
│       └── ci.yml               # PR 轻量校验（lint + 单测）
├── src/tvboxgen/
│   ├── __init__.py
│   ├── cli.py                   # 命令入口：probe / build / publish
│   ├── config.py                # 读取 config.toml
│   ├── models.py                # Site / ProbeResult / Report 数据类
│   ├── sources.py               # 种子加载、合并、去重
│   ├── probe.py                 # 分级探测实现
│   ├── build.py                 # 过滤、排序、分组、序列化
│   ├── publish.py               # git 提交、jsDelivr purge
│   └── logger.py                # 结构化日志
├── sources/
│   ├── trusted/
│   │   ├── sites.json           # 手选点播源
│   │   └── lives.json           # 手选直播源
│   └── pending/
│       └── sites.json           # 上游同步来的候选（需人工晋升）
├── data/
│   ├── probe_report.json        # 探测结果（入库，便于回溯）
│   └── history/                 # 按日期归档的探测快照
├── dist/
│   └── index.json               # ★ 唯一产物，订阅用
├── tests/
│   ├── test_sources.py
│   ├── test_build.py
│   └── fixtures/
├── config.toml                  # 阈值、并发、上游订阅列表
├── Makefile                     # 本地一条命令跑全流程
├── requirements.txt
├── .gitignore
├── README.md
└── CHANGELOG.md
```

**目录约定**

- `sources/` 只增不改语义，删除源请在 git 里留 commit message 说明原因。
- `data/` 是机器产物，允许每次提交覆盖；保留 `history/` 近 30 天。
- `dist/` 只放最终订阅文件，**不要放中间文件**，避免订阅地址指向垃圾。

### 4.1 仓库元信息（GitHub 上创建时填写）

| 项 | 值 |
|---|---|
| Name | `tvbox-gen-configs` |
| Description | Private TVBox config generator, auto probe & filter sources via GitHub Actions. |
| Visibility | **Private** |
| Topics | `tvbox` `config-generator` `github-actions` `jsdelivr` `auto-update` |

> 命名含义：`tvbox`（生态）+ `gen`（generator，自动生成配置）+ `configs`（产物形态）。与 `looomix/TVbox-Configs` 同赛道但语义更准——它强调"配置仓库"，本仓库强调"配置生成器"。

---

## 5 数据契约

### 5.1 种子格式 `sources/trusted/sites.json`

```json
[
  {
    "key": "demo_spider",
    "name": "示例源·爬虫",
    "api": "https://example.com/api.php/provide/vod/",
    "type": 3,
    "searchable": 1,
    "quickSearch": 1,
    "filterable": 1,
    "ext": "",
    "tags": ["点播", "爬虫"],
    "added_at": "2026-11-27",
    "note": "人工收录，来源：xxx"
  }
]
```

字段说明

| 字段 | 必填 | 说明 |
|---|---|---|
| `key` | ✅ | 全局唯一，去重主键；建议 `源名_短哈希` |
| `name` | ✅ | 展示名，最终进 `index.json` |
| `api` | ✅ | 接口地址，探测目标 |
| `type` | ✅ | `0` xml / `1` json / `3` 爬虫 / `4` 特殊 |
| `searchable` | ✅ | 是否参与探测（`0` 不探测） |
| `tags` | ❌ | 用于分组与产物裁剪 |
| `note` `added_at` | ❌ | 溯源与审计，不进产物 |

> 直播源用另一 schema：`{"name":..., "url":..., "group":..., "logo":...}`，与点播分开存放，避免混淆。

### 5.2 探测结果 `data/probe_report.json`

```json
{
  "generated_at": "2026-11-27T08:00:00Z",
  "runner": "gha-linux",
  "summary": {"total": 42, "ok": 31, "degraded": 5, "fail": 6},
  "sites": [
    {
      "key": "demo_spider",
      "status": "ok",
      "level": "L2",
      "latency_ms": 412,
      "score": 88.5,
      "http_code": 200,
      "checks": {"reachable": true, "json_ok": true, "shape_ok": true, "search_ok": true},
      "error": null,
      "checked_at": "2026-11-27T08:00:03Z"
    }
  ]
}
```

### 5.3 产物 `dist/index.json`（TVBox 标准）

```json
{
  "sites": [ { "key": "...", "name": "...", "type": 3, "api": "...",
               "searchable": 1, "quickSearch": 1, "filterable": 1 } ],
  "lives": [ { "name": "...", "url": "...", "group": "央视", "logo": "" } ],
  "parses": [],
  "flags": ["qq", "iqiyi", "youku", "mgtv"],
  "wallpaper": ""
}
```

**字段裁剪规则**：种子里的 `tags / note / added_at / score` 等内部字段**一律不写入产物**，防止泄漏溯源信息、也避免产物随探测波动而频繁 diff。

---

## 6 模块设计

### 6.1 `sources`：种子加载与合并

职责：

1. 读 `trusted/*.json` + `pending/*.json`（pending 默认**不参与**产物，仅参与探测）。
2. 合并 → 按 `key` 去重（重复时 trusted 优先，且 key 冲突要报 warning）。
3. 按 `api` 域名再做一次粗去重（同域名同路径不同 key 视为疑似重复，仅告警不删除）。
4. 输出 `Site` 列表给 probe。

```python
def load_sites(cfg) -> list[Site]:
    trusted = read_json_dir(cfg.trusted_dir)
    pending = read_json_dir(cfg.pending_dir)
    merged, seen = [], {}
    for s in trusted + pending:
        if s["key"] in seen:
            log.warn(f"duplicate key: {s['key']}"); continue
        seen[s["key"]] = True
        merged.append(Site.from_dict(s))
    return merged
```

### 6.2 `probe`：分级探测（核心）

只做连通性探测是不够的——很多源 `http 200` 但搜索接口已挂。所以设计 **4 级探测**，逐级加严，任一级失败即止，节省时间：

| 级别 | 检查内容 | 成本 | 失败判定 |
|---|---|---|---|
| L0 reachable | `HEAD/GET` 根地址，DNS + TCP 可达 | 极低 | 连接超时 / DNS 失败 |
| L1 protocol | 返回体是否符合声明 `type`（json/xml 可解析） | 低 | 解析异常、返回 HTML 登录页 |
| L2 shape | JSON 根结构含 `list` 或 `data` 等 TVBox 约定字段 | 低 | 结构缺失 |
| L3 search | 带 `?wd=` 调一次搜索，校验返回有内容 | 中 | 空结果 / 返回 500 / 超 15s |

- **不做的**：L4 播放链路（`play` 接口 + 拉流）。原因见非目标。
- **打分**：`score = w1*latency_score + w2*level_score + w3*历史稳定率`，默认权重 `0.3 / 0.5 / 0.2`，历史稳定率来自近 7 天 `history/` 快照的成功率。
- **并发**：`asyncio + Semaphore(8)`，每个域名最多 2 个并发（避免被单站拉黑）。
- **重试**：失败后 1 次重试，指数退避 `1s → 3s`，仍失败才落 `fail`。
- **超时**：连接 5s / 读 8s / 总 15s。

```python
async def probe_one(site: Site, sem_domain, cfg) -> ProbeResult:
    async with sem_domain(site.domain):
        r = await _l0(site, cfg);        if not r.ok: return r.as_fail()
        r = await _l1(site, r, cfg);     if not r.ok: return r.as_fail()
        r = await _l2(site, r, cfg)
        if site.searchable:
            r = await _l3(site, r, cfg)
        r.score = score(r, history_stability(site.key))
        return r
```

**状态分级**（产物准入依据）

| status | 含义 | 是否进产物 |
|---|---|---|
| `ok` | L2/L3 全通过 | ✅ 进 |
| `degraded` | L1 通过但 L3 偶发失败 | ⚠️ 进"备用组"，可配置关闭 |
| `fail` | L0/L1 失败 | ❌ 剔除 |
| `skipped` | `searchable=0` 或人工拉黑 | ❌ 不探测、不进 |

### 6.3 `build`：过滤、排序、分组、序列化

处理顺序（顺序不可乱，否则幂等性会被破坏）：

1. 读取 `probe_report.json` + `sources`。
2. **准入过滤**：剔除 `fail` / `skipped`；`degraded` 按配置决定。
3. **连续失败熔断**：某源连续 N 次（默认 3 次）探测失败 → 自动移入"观察名单"，产物中剔除，但**不动种子**。
4. **排序**：按 `score` 降序；同分按 `name` 的 Unicode 升序（保证确定性）。
5. **分组**：按 `tags` 划入 `sites` / `lives`；无 tag 归 `sites`。
6. **字段裁剪**：只输出 `index.json` 白名单字段。
7. **序列化**：`ensure_ascii=False, indent=2, sort_keys=False`，末尾补换行——**固定格式**，否则每次 diff 变异。

```python
def build_index(report, sites, cfg) -> dict:
    alive = [s for s in sites
             if report[s.key].status in cfg.accept_status
             and not in_watchlist(s.key)]
    alive.sort(key=lambda s: (-report[s.key].score, s.name))
    return {
        "sites": [trim(s) for s in alive if s.kind == "site"],
        "lives": [trim_live(s) for s in alive if s.kind == "live"],
        "parses": cfg.static_parses,
        "flags": cfg.flags,
        "wallpaper": cfg.wallpaper,
    }
```

### 6.4 `publish`：提交与缓存清理

1. 只 `git add dist/index.json data/`。
2. `git diff --cached --quiet` 判断无变更 → 直接退出（不产生空提交）。
3. 有变更 → `commit -m "chore(auto): refresh sources (ok=31 fail=6) [skip ci]"` → push。
4. push 成功后调 jsDelivr purge：
   ```
   GET https://purge.jsdelivr.net/gh/{owner}/{repo}@main/dist/index.json
   ```
   失败不影响主流程，只告警（下次自然过期）。

### 6.5 `config.toml`（全部可调参数集中一处）

```toml
[repo]
owner = "your-name"
name  = "tvbox-gen-configs"
branch = "main"

[sources]
trusted_dir = "sources/trusted"
pending_dir = "sources/pending"
pending_affects_output = false

[probe]
concurrency = 8
per_domain_concurrency = 2
timeout_connect = 5
timeout_read = 8
retries = 1
probe_search = true
search_keyword = "测试"

[build]
accept_status = ["ok"]
allow_degraded = false
max_latency_ms = 8000
fail_streak_limit = 3
sort = "score_desc"

[watchlist]
keys = []

[[upstream]]
url = "https://cdn.jsdelivr.net/gh/some/repo@main/index.json"
enabled = false      # 默认关闭，开启前请自行评估
target = "pending"
```

---

## 7 CI 工作流设计

### 7.1 `probe.yml`

```yaml
name: probe
on:
  schedule:
    - cron: "0 */4 * * *"     # UTC，每 4 小时
  workflow_dispatch:
concurrency:
  group: probe
  cancel-in-progress: false
jobs:
  probe:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12", cache: pip }
      - run: pip install -r requirements.txt
      - run: python -m tvboxgen probe
      - name: Persist report
        uses: actions/upload-artifact@v4
        with: { name: probe-report, path: data/probe_report.json }
```

### 7.2 `update.yml`

```yaml
name: update
on:
  workflow_run:
    workflows: ["probe"]
    types: [completed]
  workflow_dispatch:
jobs:
  build:
    if: ${{ github.event_name == 'workflow_dispatch' || github.event.workflow_run.conclusion == 'success' }}
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: actions/setup-python@v5
        with: { python-version: "3.12", cache: pip }
      - run: pip install -r requirements.txt
      - uses: actions/download-artifact@v4
        with: { name: probe-report, path: data/ }
      - run: python -m tvboxgen build
      - run: python -m tvboxgen publish
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
      - name: Purge jsDelivr
        continue-on-error: true
        run: curl -fsS "https://purge.jsdelivr.net/gh/${{ github.repository }}@main/dist/index.json"
```

**为什么拆两个 job**：探测耗时长、易超时，构建很轻。拆开后 probe 失败不会阻塞产物发布（可发布"上一份有效报告"的结果），也让日志更好定位。

### 7.3 `ci.yml`（PR 保护）

只在 `sources/` 变动的 PR 触发：`ruff` + `pytest` + `python -m tvboxgen build --dry-run`，确保新加的种子不破坏产物结构。

---

## 8 本地初始化步骤（照抄即可）

### 8.1 一次性初始化

```bash
# 1) 建仓库目录
mkdir -p ~/proj/tvbox-gen-configs && cd ~/proj/tvbox-gen-configs
git init -b main

# 2) 建骨架
mkdir -p .github/workflows src/tvboxgen sources/trusted sources/pending \
         data/history dist tests/fixtures

# 3) Python 环境
python3 -m venv .venv && source .venv/bin/activate
pip install -q requests aiohttp tomli pytest ruff

# 4) 生成依赖清单
cat > requirements.txt <<'EOF'
aiohttp>=3.9
requests>=2.31
tomli>=2.0 ; python_version < "3.11"
EOF

# 5) 生成 gitignore
cat > .gitignore <<'EOF'
.venv/
__pycache__/
*.pyc
.ruff_cache/
.pytest_cache/
data/probe_report.json
data/history/*.json
EOF

# 6) 首次提交
git add -A && git commit -m "init: skeleton"
```

### 8.2 打通本地 → 远程 → Actions

```bash
# 关联私有远程（务必 Private）
gh repo create tvbox-gen-configs --private --source=. --remote=origin --push
# 或手工：git remote add origin git@github.com:<you>/tvbox-gen-configs.git && git push -u origin main
```

### 8.3 本地跑全流程（不依赖 Actions）

```bash
cp sources/trusted/sites.example.json sources/trusted/sites.json   # 填 3~5 个源先验证
python -m tvboxgen probe            # → data/probe_report.json
python -m tvboxgen build            # → dist/index.json
python -m tvboxgen build --dry-run  # 只打印 diff，不落盘
python -m tvboxgen publish --local  # 只提交不 push
```

### 8.4 订阅地址

```
https://cdn.jsdelivr.net/gh/<owner>/tvbox-gen-configs@main/dist/index.json
```

> 替换 `<owner>` 为你的 GitHub 用户名，例如 `looomix` → `https://cdn.jsdelivr.net/gh/looomix/tvbox-gen-configs@main/dist/index.json`
> （私有仓库 jsDelivr 无法访问，仅限本地/内网自己托管 `dist/` 时使用；若确实要盒子联网订阅，需自行权衡公开风险。）

---

## 9 开发约定

### 9.1 Makefile

```make
.PHONY: setup probe build dry publish test lint all
setup:   ; python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest ruff
probe:   ; .venv/bin/python -m tvboxgen probe
build:   ; .venv/bin/python -m tvboxgen build
dry:     ; .venv/bin/python -m tvboxgen build --dry-run
publish: ; .venv/bin/python -m tvboxgen publish --local
test:    ; .venv/bin/python -m pytest -q
lint:    ; .venv/bin/ruff check src && .venv/bin/ruff format --check src
all: lint test probe build
```

### 9.2 编码约束

- 产物序列化统一走 `build.dumps()`，禁止各模块各自 `json.dump`。
- 所有网络调用必须带 `timeout`，禁止裸 `requests.get`。
- 探测结果字段新增要同步改 `models.ProbeResult` 与 `data/` schema 文档。
- 日志用结构化输出（`key=value`），方便 CI 里 grep。

### 9.3 验收清单（初始化完成的 Definition of Done）

- [ ] `make all` 本地全绿
- [ ] `data/probe_report.json` 生成且 `summary` 数字自洽
- [ ] `dist/index.json` 结构合法，`sites` 有内容
- [ ] 连续跑两次 `make build`，`git status` 干净（幂等）
- [ ] 手动触发 `probe` + `update` 两个 workflow 成功
- [ ] 订阅地址能加载到源列表（影视仓实测）
- [ ] `sources/pending` 不会被写入产物

---

## 10 里程碑

| 阶段 | 内容 | 产出 |
|---|---|---|
| M1（1 天） | 骨架 + 种子 + L0~L2 探测 | 本地能生成 `index.json` |
| M2（2 天） | 加 L3 搜索探测、打分、熔断 | 产物质量可感知 |
| M3（半天） | 双 workflow + purge | 全自动闭环 |
| M4（可选） | 上游订阅同步到 `pending`、告警通知 | 半自动扩源 |
| M5（可选） | 直播源独立流水线、Docker 本地定时 | 家庭内网自托管 |

---

## 11 后续演进建议

1. **告警**：探测 `fail` 超过阈值时推一条通知（webhook / 邮件），别让死源沉默。
2. **可观测**：把 `data/history/` 画成成功率曲线，判断某源是否"间歇性抽风"。
3. **渐进式准入**：新源先入 `pending` 跑 3 轮探测，通过才自动晋升 `trusted`（仍需人点确认）。
4. **产物拆分**：源超过 200 条时，按 `tags` 拆成 `index.json` / `index-lite.json` / `index-live.json`，降低单文件体积与加载耗时。

> （注：部分内容由豆包 AI 生成）