## Aibot

> 基于 [MaiBot](https://github.com/MaiM-with-u/MaiBot) 精简重构的 QQ 群聊 AI 机器人，使用 NoneBot2 + OneBot V11 协议驱动，支持多模型推理、长期记忆、情绪系统、表情包抓取与发送等功能。

---

### 功能特性

- **多模型推理**：支持配置推理模型（R1）、普通模型（V3）、蒸馏模型等多种模型，按概率混合使用
- **长期记忆**：基于话题的记忆构建与遗忘机制，embedding 向量检索
- **情绪系统**：愉悦度 / 唤醒度双维情绪，影响回复风格
- **回复意愿**：动态意愿模型，支持"被提及"、"对话上下文"、"高活跃模式"等多种触发逻辑
- **表情包系统**：自动抓取群内表情包 → VLM 生成描述 → 向量入库 → 适时发送；支持数量上限滚动删除
- **关键词反应**：可配置关键词触发特定回复提示词
- **中文错别字**：模拟人类打字习惯的错别字生成器
- **WebUI**：带登录鉴权的 Web 管理界面，支持在线修改配置、查看日志、启停 bot

---

### 环境要求

- Python **3.12+**
- 任意支持 OneBot V11 协议的 QQ 协议端（如 [NapCat](https://napneko.github.io/)、[LLOneBot](https://llonebot.github.io/) 等）
- 至少一个兼容 OpenAI API 的模型服务

---

### 安装

#### 1. 克隆仓库

```bash
git clone https://github.com/jiang068/Aibot.git
cd Aibot
```

#### 2. 创建虚拟环境并安装依赖

推荐使用 `uv`：

```bash
pip install uv
uv venv --python 3.12
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

uv pip install -r requirements.txt
```

也可以用 pip：

```bash
pip install -r requirements.txt
```

#### 3. 配置 OneBot V11 协议端

使用任意支持 OneBot V11 的协议端登录 QQ 账号，创建反向 WebSocket 服务，添加以下地址：

```
ws://127.0.0.1:8080/onebot/v11/ws
```

#### 4. 配置环境变量

复制模板并编辑：

```bash
cp config/.env.example config/.env
```

`.env` 关键字段说明：

| 字段 | 说明 |
|---|---|
| `HOST` / `PORT` | WebUI 监听地址，默认 `127.0.0.1:12000` |
| `{PROVIDER}_BASE_URL` | 模型服务 API 地址，如 `SILICONFLOW_BASE_URL` |
| `{PROVIDER}_KEY` | 对应服务的 API Key |
| `SINGLE_API_MODE` | `true`=单次调用模式（省 Token），`false`=多次调用 |
| `EMOJI_ENABLED` | 是否启用表情包抓取（`true`/`false`） |
| `EMOJI_MAX_COUNT` | 表情包库上限，超出后自动删最旧（`0`=不限制） |
| `EMOJI_SEND_ENABLED` | 是否允许发送表情包 |
| `WEBUI_USERNAME` / `WEBUI_PASSWORD` | WebUI 登录账号（首次启动自动生成） |

#### 5. 配置机器人

复制模板并编辑：

```bash
cp config/bot_config.toml.example config/bot_config.toml
```


**必填项：**

```toml
[inner]
version = "0.0.12"

[bot]
qq = 你的机器人QQ号
nickname = "艾艾"

[groups]
talk_allowed = [群号1, 群号2]   # bot 允许发言的群

[model.llm_reasoning]
name = "Pro/deepseek-ai/DeepSeek-R1"
provider = "SILICONFLOW"   # 对应 .env 中的 SILICONFLOW_BASE_URL / SILICONFLOW_KEY

# ... 其余模型配置同理
```

**模型配置说明：**

`provider` 字段值对应 `.env` 中的变量名前缀，例如 `provider = "SILICONFLOW"` 会自动读取 `SILICONFLOW_BASE_URL` 和 `SILICONFLOW_KEY`。

| 模型字段 | 作用 | 示例模型 |
|---|---|---|
| `llm_reasoning` | 主回复模型（推理） | DeepSeek-R1 |
| `llm_reasoning_minor` | 次回复模型（蒸馏） | DeepSeek-R1-Distill-32B |
| `llm_normal` | 次回复模型（普通） | DeepSeek-V3 |
| `llm_normal_minor` | 次回复模型（轻量） | DeepSeek-V2.5 |
| `llm_emotion_judge` | 情感判断 | Qwen2.5-14B |
| `llm_topic_judge` | 话题判断 | Qwen2.5-7B |
| `llm_summary_by_topic` | 记忆总结 | Qwen2.5-32B |
| `vlm` | 图像理解（可选） | Qwen2-VL-7B |
| `embedding` | 向量嵌入 | BAAI/bge-m3 |

---

### 启动

#### 方式一：WebUI（推荐）

```bash
uv run webui.py
```

浏览器访问 `http://127.0.0.1:8088`，使用 `.env` 中的账号密码登录，在界面内启动/停止 bot、修改配置、查看实时日志。

首次运行会自动在 `.env` 中生成 `WEBUI_USERNAME`、`WEBUI_PASSWORD`、`SECRET_KEY` 。

#### 方式二：直接运行

```bash
uv run bot.py
```

---

### 目录结构

```
Aibot/
├── bot.py                  # Bot 入口
├── webui.py                # WebUI 入口（FastAPI）
├── requirements.txt
├── config/
│   ├── .env                # 环境变量（API Key、WebUI 账号等）
│   └── bot_config.toml     # 机器人行为配置
├── data/
│   ├── Aibot.db           # SQLite 数据库
│   ├── emoji/              # 自动抓取的表情包文件
│   └── logs/               # 分模块日志
├── src/
│   ├── common/             # 公共模块（数据库、日志）
│   └── plugins/
│       ├── chat/           # 核心聊天插件
│       │   ├── bot.py          # 消息处理主流程
│       │   ├── config.py       # 配置加载
│       │   ├── emoji_manager.py # 表情包管理
│       │   ├── message.py      # 消息数据类
│       │   └── ...
│       ├── memory_system/  # 记忆系统
│       ├── moods/          # 情绪系统
│       └── willing/        # 回复意愿系统
└── static/                 # WebUI 前端静态文件
```

---

### License

MIT


---
