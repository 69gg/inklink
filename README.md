# Inklink

墨连（Inklink）是一个面向中文长篇小说续写的 AI 驱动 TUI 工具。它读取已有章节，整理剧情、人物、伏笔和世界观状态，在用户确认大纲、章节计划和场景计划后继续生成正文，并在过程中保留日志、产物和用量统计，方便长篇项目断点续接。

## 功能概览

- 从章节目录导入已有正文，章节格式为 `title: 标题`、`---`、正文。
- 自动分析已有章节，提取剧情脉络、人物、事件、伏笔、世界观和注意事项。
- 支持长篇小说的结构化状态索引、区间摘要、活跃度衰减和按需检索，适合章节很多的项目。
- 通过 TUI 与 AI 多轮讨论大纲、章节计划和场景计划，用户批准后再续写。
- 支持批量续写、自动批准、确定性检查、LLM 自审和自动修订。
- 支持 OpenAI Responses API 与 Chat Completions API。
- 支持多个模型 profile，并可把不同任务分配给不同模型。
- 支持限流、重试、断点续接、运行日志、产物版本和 token 用量统计。
- 输出可写入运行目录，也可在 `writeback` 模式下原子写回原章节目录。
- 启动任务时可填写额外 notes；notes 会随运行设置持久化，恢复运行时自动复用。

## 安装

```bash
uv sync
uv run inklink version
```

复制示例配置：

```bash
cp config.toml.example config.toml
```

真实 API key 建议放在环境变量中，不要直接写入 `config.toml`。在配置文件里用 `api_key_env` 指向对应环境变量。

## 准备章节

输入目录应使用连续章节号命名：

```text
novel/
  1.txt
  2.txt
  3.txt
```

每个章节文件使用 UTF-8 编码，内容格式如下：

```text
title: 第一章 标题
---
正文内容
```

章节号必须从 `1.txt` 开始连续递增。不接受 `01.txt`、`0.txt`、负数、非数字文件名或章节号缺口。

## 启动 TUI

```bash
uv run inklink run ./novel --config config.toml
```

TUI 首页可以设置：

- 运行 ID：留空时创建新运行；填写已有 ID 可恢复。
- 续写章数与起始章节。
- 每章目标字数范围。
- 最大修订轮数。
- 输出模式：写入运行目录或写回原章节目录。
- 自动批准选项。
- 额外 notes：本次续写的补充约束。世界观、设定和人物关系仍会主要从正文推断。
- 日志目录。

运行中可查看 DAG 状态、节点、产物、审批消息、事件日志和用量统计，也可以提交审批意见、让 AI 修改当前产物、批准版本、重试失败节点、放弃章节或重写章节。

## 直接执行

不进入交互界面，直接执行一次续写任务：

```bash
uv run inklink run ./novel --config config.toml --execute \
  --chapter-count 1 --min-chars 800 --max-chars 1800 \
  --max-revision-rounds 3 --auto-approve \
  --notes "保留门后悬念" --notes-file notes.md
```

恢复已有运行：

```bash
uv run inklink run --execute --resume-runtime-id <runtime_id> --auto-approve
```

首次运行会把输入目录、配置文件、输出模式、字数范围、审批选项和 notes 保存到 `logs/<runtime_id>/artifacts/run_settings.json`。恢复同一 runtime 时，墨连会复用这些设置；本次命令里的 `--auto-approve` 可以作为继续通过后续审批点的临时开关。

默认输出模式为 `output`，生成章节会写入：

```text
logs/<runtime_id>/outputs/chapters/<N>.txt
```

使用 `--output-mode writeback` 时，生成章节会写回输入目录。若目标章节文件已经存在，墨连会先把结果放到 pending 目录并暂停，待用户处理冲突后再恢复运行。

## 常用命令

查看运行信息：

```bash
uv run inklink workflow info <runtime_id>
uv run inklink workflow stats <runtime_id>
uv run inklink workflow nodes <runtime_id>
uv run inklink workflow artifacts <runtime_id>
uv run inklink workflow approvals <runtime_id>
uv run inklink workflow events <runtime_id> --limit 20
```

查看产物：

```bash
uv run inklink workflow artifact <runtime_id> outline --version 1
```

提交审批消息、让 AI 修改产物并批准：

```bash
uv run inklink workflow message <runtime_id> outline "请强化主角和反派的正面冲突"
uv run inklink workflow chat-update <runtime_id> outline outline outline "请根据刚才的意见更新大纲"
uv run inklink workflow approve <runtime_id> outline outline 2
```

处理异常或不满意的章节：

```bash
uv run inklink workflow retry <runtime_id> draft-1
uv run inklink workflow abandon <runtime_id> 1
uv run inklink workflow rewrite <runtime_id> 1
```

`retry` 用于从网络、限流、超时等技术性失败恢复；`abandon` 和 `rewrite` 用于放弃或重写创作结果。

## 用量统计

每次运行结束后，CLI 会输出按总计、profile、model 和 task 汇总的调用次数与 token 使用量。基础字段包括：

- `calls`
- `input`
- `output`
- `total`

如果服务端返回了 cache 或 reasoning token，统计中也会显示对应字段。

## 配置

主要配置项包括：

- 模型 profile：模型名、API 类型、base URL、环境变量、温度等参数。
- 任务映射：把章节分析、大纲、计划、续写、自审、修订等任务分配给不同 profile。
- 限流：RPM、并发数和重试策略。
- 长篇上下文：冷启动模式、检索预算、区间摘要阈值、活跃度衰减。
- 审批：哪些阶段需要用户确认，哪些阶段可以自动批准。

完整配置示例见 [config.toml.example](config.toml.example)，详细说明见 [配置说明](docs/configuration.md)。

## 文档

- [配置说明](docs/configuration.md)
- [工作流设计](docs/workflow.md)
- [LLM 兼容性](docs/llm-compatibility.md)
- [长上下文策略](docs/long-context.md)
