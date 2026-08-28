# AI Information Radar

一个本地优先的单用户信息雷达 MVP：采集 RSS，统一保存内容，去重并聚合事件，生成带原始来源的中文每日简报，并可导出到 Obsidian。

## MVP 边界

- RSS/Atom/RSSHub 采集与旧 `Daily Newsletter` feeds 配置导入
- 统一 Source / Item / Cluster / Brief 数据模型
- URL / external ID 硬去重与标题相似度轻量事件聚类
- 无外部模型也能运行的确定性简报
- 可选 Ollama/OpenAI-compatible AI 增强
- Today、信息流、事件、信息源、任务五个 Web 页面
- 收件箱 / 收藏 / 忽略反馈闭环，反馈会影响简报筛选与排序
- 缺失发布时间的内容显式隔离，不伪装成“刚刚发布”
- 每日最多 10 个跨来源事件，并提供证据时间线
- 按需翻译标题和摘要，在原文下方保留中英对照并缓存结果
- Obsidian Markdown 导出

暂不包含登录态平台爬取、多用户、原生移动端和自动投资建议。

## 本地运行

需要 Python 3.11 或更高版本。

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
uvicorn app.main:app --reload --env-file .env
```

打开 <http://127.0.0.1:8000>。

### Raycast 一键启动

仓库提供了 Script Command：`scripts/raycast/open-ai-information-radar.sh`。它会先检查服务健康状态；未运行时通过 macOS `launchctl` 托管最新版工作树并复用 `data/radar.db`，就绪后自动打开浏览器，重复执行不会启动多个进程。

首次配置只需在 Raycast 中打开 `Settings → Script Commands → Add Script Directory`，选择本仓库的 `scripts/raycast` 目录。之后搜索“打开 AI 信息雷达”即可。Script Command 的元数据格式遵循 [Raycast 官方规范](https://github.com/raycast/script-commands)。

现有 Newsboat 配置通过 `LEGACY_FEEDS_FILE` 幂等导入；应用不会修改旧脚本或 cron。YouTube、Bilibili 第一版通过它们的 RSS/RSSHub Feed 接入，字幕和 Whisper Worker 属于下一里程碑。

设置 `LLM_BASE_URL` 和 `LLM_MODEL` 后，每日任务会调用 OpenAI-compatible 接口润色确定性简报；服务端会校验证据 ID 集合，模型增删证据时拒绝保存。留空则完全使用本地确定性模式。

### 按需翻译

信息流卡片的“译”按钮只翻译当前标题与摘要，结果写入本地 SQLite，同一内容不会重复调用模型。

默认配置为 `TRANSLATION_PROVIDER=mock`。Mock 不访问网络，也不提供真实语言翻译；它会显示带有“模拟译文 · 非真实翻译”标记的占位结果，用来验证交互、布局和缓存。

安装 Ollama 与所需模型后，可切换为本地真实翻译：

```dotenv
TRANSLATION_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434/v1
OLLAMA_MODEL=<已安装的模型名称>
```

`TRANSLATION_PROVIDER=auto` 会优先使用配置好的 Ollama；没有 `OLLAMA_MODEL` 时再尝试现有 `LLM_BASE_URL` / `LLM_MODEL`。系统不会静默调用公共翻译服务。

## 验证

```bash
pytest
```

## Docker

```bash
cp .env.example .env
docker compose up --build
```

Docker 默认只绑定 `127.0.0.1`。MVP 没有用户认证，不要直接暴露到公网；需要远程访问时应先放在带认证的反向代理或 VPN 后面。

容器内导出 Obsidian 时，将 Compose 中的 Vault volume 改为 `- /你的/Vault/绝对路径:/vault`，并设置 `OBSIDIAN_VAULT_PATH=/vault`。

## 每日调度

应用保持运行后，可用系统 cron 在每天 7:00 触发完整采集和简报：

```cron
0 7 * * * /usr/bin/curl -fsS -X POST 'http://127.0.0.1:8000/api/jobs/run?kind=daily' >> /tmp/ai-information-radar.log 2>&1
```

首次切换前建议与旧日报双跑三天；确认覆盖率与引用正确后，再停掉旧 `daily_newsletter.py` cron，避免两个系统覆盖同一天的 Obsidian 文件。

## 设计原则

1. 指标由程序计算，LLM 只负责叙述。
2. 每条结论保留来源和证据。
3. 先过滤、去重、聚类，再调用模型。
4. 采集适配器与应用核心隔离，RSSHub 等外部组件独立部署。
5. 外部内容一律按不可信数据处理，不能覆盖系统指令。
