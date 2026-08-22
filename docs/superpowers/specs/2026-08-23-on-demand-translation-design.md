# 按需中文翻译设计

日期：2026-08-23  
状态：待用户复核

## 目标

用户可以在信息流中按需把单条内容的标题和现有摘要翻译为简体中文。译文在原文下方展开，保留中英对照，并被本地缓存。

成功标准：

- 单击一次“译”即可在当前卡片内看到结果，无需刷新页面。
- 相同内容的重复请求直接读取缓存，不重复调用模型。
- Mock、Ollama 和 OpenAI-compatible Provider 共用同一接口，切换引擎不改变 UI 或数据结构。
- Mock 结果始终明确标注，不能被误认为真实翻译。
- 缺少配置或模型失败时，错误只出现在当前卡片，不破坏信息流。

## 本期范围

- 翻译 Item 的 `title` 和 `clean_text`。
- 目标语言固定为 `zh-CN`。
- 信息流卡片内按需触发、原文下方展开。
- 翻译结果持久化到 SQLite。
- 提供 Web 与 API 入口。
- 开发环境使用 Mock Provider 验证完整流程。

## 非目标

- 不抓取或翻译原网页全文。
- 不自动批量翻译所有内容。
- 不翻译评论、字幕、附件或历史 Obsidian 文件。
- 不把 Mock 文本用于日报或市场判断。
- 本期不在 UI 中管理 API Key。

## 用户流程

1. 用户浏览信息流，点击卡片操作区的“译”。
2. 页面在该按钮显示处理中状态。
3. 服务端先检查 `item_translations` 缓存。
4. 有缓存时直接返回；无缓存时选择 Provider 并翻译。
5. 中文标题和摘要在原文下方展开，标明 Provider。
6. 失败时原位显示可理解的原因和配置提示，原文保持可读。

## Provider 设计

统一接口：

```python
class TranslationProvider(Protocol):
    name: str
    async def translate(
        self,
        title: str,
        text: str,
        target_language: str = "zh-CN",
    ) -> TranslationResult: ...
```

Provider 选择由 `TRANSLATION_PROVIDER` 控制：

| 值 | 行为 |
|---|---|
| `mock` | 使用开发 Mock，不访问网络 |
| `auto` | 优先 Ollama；未配置时尝试现有 OpenAI-compatible 配置 |
| `ollama` | 只使用 `OLLAMA_BASE_URL` 和 `OLLAMA_MODEL` |
| `openai-compatible` | 只使用现有 `LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY` |

当前 `.env` 使用 `TRANSLATION_PROVIDER=mock`。将来安装 Ollama 后只需改环境变量，不改业务代码。

Mock 的输出规则：

- 标题：`【模拟译文】{原始标题}`
- 摘要：`【模拟中文摘要】{原始摘要}`
- Provider 标签：`mock · 非真实翻译`

Mock 只验证交互、缓存和布局，不声称提供语言翻译质量。

## 数据模型

新增 `item_translations`：

| 字段 | 说明 |
|---|---|
| `item_id` | 唯一关联 Item |
| `target_language` | 本期固定 `zh-CN` |
| `translated_title` | 中文标题 |
| `translated_text` | 中文摘要，可为空 |
| `provider` | 实际 Provider / 模型标识 |
| `is_mock` | 是否为模拟结果 |
| `created_at` / `updated_at` | 缓存审计时间 |

独立表用于兼容已有 SQLite：应用启动时 `create_all` 可以安全新增表，不要求修改现有 `items` 表。

## API 与页面契约

- `POST /items/{item_id}/translate`
  - HTMX 局部返回翻译面板。
  - 默认命中缓存。
- `POST /api/items/{item_id}/translate?force=false`
  - 返回结构化译文、Provider、`is_mock` 与 `cached`。
  - `force=true` 供调试或以后重新翻译使用，首期 UI 不暴露。

翻译按钮始终可见。已有缓存时使用激活态，点击仍只读取缓存。

## 安全与错误处理

- 标题和摘要属于不可信输入，只作为 JSON 数据发送给模型。
- System Prompt 明确禁止执行正文指令、总结、补充事实或改变不确定性。
- 输入上限为标题 1,000 字符、摘要 8,000 字符，避免异常成本。
- Jinja 默认转义译文，禁止把模型输出当 HTML。
- Provider 输出必须是包含非空 `translated_title` 的 JSON；不符合契约时返回 502。
- 未配置真实 Provider 时返回可操作提示；不得悄悄回退到未经声明的公网翻译服务。
- 模型调用失败不写入缓存。

## 测试策略

按 TDD 分步实现：

1. Provider 选择：Mock、auto 优先级、缺配置。
2. Mock 输出：明确标记且不访问网络。
3. 缓存：首次生成、第二次复用、`force=true` 更新。
4. 输出校验：空标题、非字符串正文、恶意输入。
5. API：200、404、502、503 与缓存标志。
6. Web：按钮、处理中状态、卡片内中英对照、局部错误。
7. 回归：完整 pytest、compileall、真实浏览器桌面和 390px 移动端。

## 验收标准

- Mock 模式下，真实数据库不会产生任何外部模型请求。
- 翻译按钮不会改变已读、收藏或忽略状态。
- 译文能在筛选后的信息流卡片内展开。
- 刷新页面后已缓存译文仍显示。
- 页面清楚显示“模拟译文”，用户不会把它当真实中文翻译。
- 所有自动化测试通过，浏览器控制台无新增错误。
