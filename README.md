# tvbox-gen-configs

Private TVBox config generator：采集全量种子 → 探测可用性 → 晋升高可用源 → 生成订阅配置。

> 仅供个人/家庭内网使用。仓库请保持 **Private**。

## 数据分层

| 目录 | 作用 |
|---|---|
| `sources/collectors/feeds.json` | **采集源**（手动维护）：点播 API 清单 + 直播频道清单 URL |
| `sources/pending/sites.json` | 点播全量池（手改 + 采集追加去重） |
| `sources/pending/lives.json` | 直播全量池（手改 + 直播采集追加去重） |
| `sources/trusted/` | 探测可用后自动写入；`build` 只读这里生成产物 |

## 快速开始

```bash
python -m venv .venv
# Windows
.venv\Scripts\pip install -r requirements.txt -e ".[dev]"

python -m tvboxgen sync      # collectors → pending（追加去重）
python -m tvboxgen probe     # 探测 pending
python -m tvboxgen promote   # 可用 → 重写 trusted
python -m tvboxgen build     # trusted → dist/index.json
python -m tvboxgen publish --local
```

### 采集源配置（手动维护）

编辑 `sources/collectors/feeds.json`（数组，可配多条）：

```json
[
  {
    "id": "ouonnki-full-noadult",
    "name": "OuonnkiTV 无成人 JSON",
    "url": "https://raw.githubusercontent.com/Yesbaiwan/OuonnkiTV-Source/main/tv_source/OuonnkiTV/full-noadult.json",
    "enabled": true,
    "target": "pending",
    "kind": "sites",
    "format": "json"
  }
]
```

`format`：`json` / `txt` / `auto`。  
`kind`：`sites`（点播采集）或 `lives`（直播采集，支持 DIY `分组,#genre#` + `名称,url`）。

直播也可继续手改 `sources/pending/lives.json`。

## 订阅地址

```
https://cdn.jsdelivr.net/gh/<owner>/tvbox-gen-configs@main/dist/index.json
```

私有仓库 jsDelivr 不可用；家庭场景建议自托管 `dist/`。

## 参考

探测与产物形态参考 [tw-lewis/TVbox-Configs](https://github.com/tw-lewis/TVbox-Configs)。

## License

Private / 自用。
