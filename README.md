# Telegram 批量下载器（Vault · Telegram Archive）

一个跑在本机的 Telegram 批量下载工具：扫码登录后浏览频道、群组、我的收藏和评论区，按类型/关键词/日期/大小筛选，预览、批量下载、自动分类、下载前去重，支持定时追更。

界面是本地网页（`http://127.0.0.1:8765`），后端用 Telethon 走 MTProto，Flask 提供 REST + SSE 实时进度。旧版桌面 GUI（`main.py`，Tk/ttkbootstrap）保留可用。

> **仓库里没有的东西**：`config.json`（含 api_id / api_hash）、`tg_session.session`（登录会话，等于账号凭证）、`download_history.json`、`subscriptions.json` 全部在 `.gitignore` 里。
> 第一次跑请 `copy config.example.json config.json`，再填自己的 `api_id` / `api_hash`。

---

## 功能

**账号与会话**
- 扫码登录（二维码 40 秒自动刷新），支持两步验证密码
- 登录状态保存在本地 `tg_session.session`，重启无需重复扫码

**会话列表**
- 「我的收藏」（Saved Messages）置顶
- 频道、群组、评论区分类排序，标签 `CH` / `GRP` / `SAV` / `CMT`
- 广播频道自动识别关联讨论组，顶部「查看评论」一键打开评论区，插入列表并可直接浏览/筛选/下载

**浏览与预览**
- 列表视图 + 相册视图，均为虚拟滚动，加载上万条不卡
- 「加载更多」按页加载；「查看全部」以 2000 条/页连续加载，可随时「停止加载」
- 图片/视频/音频在线预览（磁盘缓存），相册缩略图服务端缓存

**筛选**
- 内容类型（视频/图片/文件/音频/文本）、关键词、日期区间、大小区间
- 「只看未下载」：按下载历史过滤

**下载**
- 支持勾选、全选当前可见、单文件下载；并发 1–64 可调
- 512KB 分块 + 断点续传（`.part`），失败重试、`FLOOD_WAIT` 自动等待
- 掉线自动重建连接；45 秒（小文件 30 秒）无进展自动取消重试
- 小于 512KB 的文件走官方单次请求路径，大文件走分块 + DC 定向
- **小文件优先**，大于 8MB 的大文件单独限流（最多占 1/6 并发），避免大文件堵死队列
- 自动分类：按相册标题、10 分钟时间批次、文件名公共前缀建子文件夹
- 评论区的下载会并入所属频道的文件夹，不再单独建「频道 · 评论」目录
- 进度条显示百分比、完成/总数、实时 MB/s 与 个/s，结束后显示平均速度

**下载前去重**（`web/dedupe.js`）
- 在下载前剔除重复项，不必先下再删
- 识别 `0-140` / `0-180`、`01_187`、`第1-120章` 等区间写法
- 规则：同书名（含作者）分组；同区间保留较新；区间被更大范围完整覆盖的跳过；分卷 `part1/2/3` 不会被误合并；无区间时按同名同格式判断
- 弹窗预览「跳过哪些、省多少空间」，可选：去重后下载 / 全部下载 / 仅取消重复勾选

**整理与追更**
- 「整理」按相册与文件名前缀重新归置已下载文件，并清理 0 字节 `.part`
- 「追更」按 5/10/30/60 分钟检查订阅频道的新消息，自动下载新增文件

---

## 技术栈与架构

- Python 3.11 + [Telethon](https://github.com/LonamiWebs/Telethon)（MTProto 客户端）
- Flask + SSE（`/api/events`）做本地服务与实时推送
- 原生 HTML/CSS/JS 前端（虚拟列表、虚拟相册、无框架）
- `cryptg`（可选但建议安装）：C 实现的 MTProto 加密，降低大文件 CPU 占用
- 可选代理：SOCKS5 / HTTP（默认 `127.0.0.1:7897`，配合 Clash）

```
telegram-batch-downloader/
├─ tg_core.py            异步核心：登录、会话、消息、缩略图、预览、下载、重连
├─ web_server.py         Flask 服务：REST 接口、SSE 推送、历史/订阅持久化
├─ main.py               旧版桌面 GUI（Tk/ttkbootstrap，可选）
├─ web/
│  ├─ index.html         页面结构
│  ├─ app.js             交互、虚拟列表/相册、下载与进度
│  ├─ dedupe.js          下载前去重规则（纯函数，可用 Node 单测）
│  └─ styles.css         样式（深/浅色主题）
├─ config.json           运行配置（含 api_id/api_hash，勿外传；不入库）
├─ config.example.json   配置模板，复制成 config.json 再填
├─ download_history.json 下载历史（按会话记录消息 id；不入库）
├─ subscriptions.json    追更订阅（不入库）
├─ tg_session.session    Telegram 登录会话（等于账号凭证；不入库）
├─ 启动.bat              启动旧版桌面界面
└─ 启动网页版.bat        启动网页版并打开浏览器
```

---

## 快速开始

1. 安装依赖：
   ```bat
   pip install -r requirements.txt
   pip install cryptg
   ```
2. 在 [my.telegram.org](https://my.telegram.org) 创建应用，获取 `api_id` / `api_hash`，填入 `config.json`：
   ```bat
   copy config.example.json config.json
   ```
   （`config.json` 不在仓库里，`web_server.py` / `main.py` 读不到时会用内置默认值，界面里也能改）
3. 确保代理可用（默认走本机 Clash SOCKS5 `127.0.0.1:7897`；不需要代理可把 `proxy_enabled` 设为 `false`）。
4. 双击 `启动网页版.bat`，浏览器打开 `http://127.0.0.1:8765`。
5. 首次使用扫码登录（若账号开了两步验证，会提示输入密码）。

> 页面打不开或一直显示二维码，多数是代理节点失效：先确认 Clash 在运行、节点能连通 Telegram，再刷新页面。

---

## 配置项（config.json）

| 字段 | 说明 | 示例 |
| --- | --- | --- |
| `api_id` / `api_hash` | Telegram 应用凭据，必填 | `123456` / `"你的 api_hash"` |
| `proxy_enabled` | 是否使用代理 | `true` |
| `proxy_type` | `socks5` / `http` | `"socks5"` |
| `proxy_host` / `proxy_port` | 代理地址端口 | `"127.0.0.1"` / `7897` |
| `download_dir` | 下载根目录 | `"E:\\Download\\Telegram"` |
| `page_size` | 每页消息条数 | `1000` |
| `concurrency` | 下载并发（1–64） | `32` |
| `classify_enabled` | 是否自动按标题前缀分类 | `true` |

`config.json` 含敏感信息，不要提交到公开仓库或发给他人。

---

## 主要接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/state` | 当前登录状态、会话列表、配置、二维码 |
| GET | `/api/events` | SSE：登录、会话、消息、缩略图、下载进度等事件 |
| POST | `/api/connect` | 重新连接 / 生成新二维码 |
| POST | `/api/password` | 提交两步验证密码 |
| POST | `/api/messages` | 按 `dialog_id`、`offset_id`、`limit` 拉取消息 |
| POST | `/api/open_comments` | 打开频道的关联评论区 |
| POST | `/api/thumb` / GET `/api/thumb_image` | 生成 / 读取缩略图 |
| POST | `/api/preview` / GET `/api/preview_media` | 预览媒体 |
| POST | `/api/download` | 按消息 id 列表下载（支持临时指定目录与并发） |
| POST | `/api/stop` | 停止当前下载 |
| POST | `/api/reorganize` | 整理已下载文件（支持 `dry_run`） |
| GET | `/api/history` | 某会话的已下载消息 id |
| GET/POST | `/api/subs` | 查询 / 新增、删除追更订阅 |
| POST | `/api/settings` | 保存下载目录、并发、代理等 |
| POST | `/api/open_folder` | 打开下载目录 |

---

## 下载速度说明

实测（同一账号、Clash 日本节点）：

- 代理节点带宽约 11 MB/s，不是瓶颈；
- Telegram 小文件下载约 2.5–5 个/秒，增加连接数没有明显提升，说明限制在账号取文件请求速率；
- 大文件单文件约 0.6–1 MB/s（受账号速率与节点时延共同影响）。

因此「刚开始很快、后面变慢」通常是两个原因：

1. 每次开始下载都会先检查文件是否已存在，命中就直接「已存在，跳过」，不发网络请求——这一段速度极高；
2. 跳过后才进入真实下载，速度回落到每秒几个文件。若队列里混有几十 MB 的大文件，旧机制下会占满并发，看起来像卡住（现已改为大文件单独限流）。

建议：勾选前先开「只看未下载」，可跳过第一阶段；不要频繁「停止→重新开始」，每次都会重扫并留下 0 字节占位文件。

---

## 常见问题

**页面显示二维码、进不去**
本地服务本身可能正常，是 Telegram 连接失败。检查 Clash 是否运行、节点是否可用（能访问 `gstatic.com` 与 Telegram），换节点后刷新页面即可，无需重新扫码（会话仍有效）。

**下载失败提示「网络连接中断」**
代理节点抖动导致掉线。程序会自动重建连接并重试；频繁出现时换一个稳定节点。

**目录里有很多 0 字节 `.part`**
旧的断线残留。现在每次下载会先清理；也可手动删除所有 0 字节 `.part`，不影响断点续传。

**评论区消息特别多**
某些频道评论区可达数万条（例如 6 万+），「查看全部」会持续较久，可先用类型/日期筛选，或中途「停止加载」。

**去重没识别出重复**
去重只依据文件名与体积、日期，不解析文件内容。文件名没有任何章节区间或书名差异时，会按「不冒险」原则保留。

---

## 已知限制

- 仅支持单 Telegram 账号，多账号需复制项目目录分别运行
- 必须能访问 Telegram（本机直连不通，需要代理）
- 下载速率受 Telegram 账号限制，提升并发并不能线性加速
- 去重是文件名启发式，不读取压缩包内容
- 旧版 `main.py` 桌面界面功能较少，建议使用网页版
