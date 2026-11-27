# tvbox-gen-configs

Private TVBox config generator：采集全量种子 ∪ 手维源 → 探测可用性 → 晋升高可用源 → 生成订阅配置。

> 仅供个人/家庭内网使用。仓库请保持 **Private**。

## 数据分层

| 目录 | 作用 |
|---|---|
| `sources/collectors/feeds.json` | **采集源**清单 URL（CMS 点播 / 直播频道列表） |
| `sources/pending/` | **仅** sync 覆盖写入的采集结果 |
| `sources/manual/sites.json` `lives.json` | **手维** HTTP 点播 / 直播频道 |
| `sources/manual/subscriptions.json` | **手维**完整 TVBox 配置 URL（蜘蛛/jar，勿拆站） |
| `sources/pending/subscriptions.json` | sync 识别到的仓库型配置（整包 URL） |
| `sources/trusted/` | 探测可用后自动写入（含 `subscriptions.json`）；`build` 只读这里 |

```text
collectors ──sync(覆盖 pending)──► pending sites/lives/subscriptions ─┐
                                                                      ├─ probe ─► promote ─► trusted
manual sites/lives/subscriptions ─────────────────────────────────────┘              │
                                                                                     ▼
                                                              dist/index.json      纯源
                                                              dist/lives.txt       直播 DIY
                                                              dist/subscriptions.json 订阅源
                                                              dist/urls.json       多仓源
```

- sync **整轮成功才覆盖** pending；全部失败或中途崩溃 → **保留旧 pending**，后续 probe 照常跑全量（pending∪manual）。
- 采集到的完整 TVBox 源（`spider` / type3 / 相对 jar·js）写入 `pending/subscriptions.json`，**不拆站**；`feeds.json` 也可显式 `"kind": "subscriptions"`。

## 快速开始

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt -e ".[dev]"

python -m tvboxgen sync      # collectors → 覆盖 pending
python -m tvboxgen probe     # 探测 pending∪manual（含 subscriptions）
python -m tvboxgen promote   # 可用 → trusted（含 subscriptions）
python -m tvboxgen build     # → 纯源 + 订阅源 + 多仓源
python -m tvboxgen publish --local
```

### 盒子订阅

| 产物 | 文件 | 说明 |
|---|---|---|
| **纯源** | `dist/index.json` (+ `lives.txt`) | CMS 点播 + 直播；标准 TVBox JSON |
| **订阅源** | `dist/subscriptions.json` | 可用的完整仓库配置 URL 列表 |
| **多仓源** | `dist/urls.json` | `{"urls":[纯源, …订阅]}`，TVBox 多仓入口 |

## License

Private / 自用。
