# Inklink

墨连（Inklink）是一个面向中文长篇小说续写的 AI 驱动 TUI 工具。

当前仓库处于早期实现阶段，已包含项目脚手架、配置加载、严格章节读取、原子写入辅助和项目目录运行锁。

## 开发

本项目使用 `uv` 管理：

```bash
uv run inklink version
uv run inklink run ./novel --config config.toml
```

常用质量检查：

```bash
uv run ruff format
uv run ruff check
uv run mypy
uv run pytest -q
```

## 配置

复制 `config.toml.example` 为 `config.toml` 后再修改。配置支持多个模型 profile，并可按任务映射到不同模型。OpenAI SDK 调用层会同时支持 Responses API 和 Chat Completions API；温度、top-p、推理强度、最大输出 token 等参数可以留空。

`config.toml` 包含 API key 环境变量名，不应提交到仓库。

## 输入章节

输入目录中的章节文件必须使用连续 ASCII 正整数命名：

```text
1.txt
2.txt
3.txt
```

不接受 `01.txt`、非 ASCII 数字或章节号缺口。每个章节文件使用 UTF-8 编码，允许 UTF-8 BOM，换行会在读取时规范化为 LF。章节内容必须严格符合：

```text
title: 章节标题
---
正文
```

第一行必须以 `title:` 开头且标题不能为空；第二行必须是独立的 `---`；正文从第三行开始。

## 写入

输出辅助会先写入同目录唯一临时文件，刷新并 `fsync` 后再通过 `os.replace` 原子替换目标文件。writeback 模式后续会基于同样原则避免半写入损坏章节文件。

同一输入目录一次只能有一个活跃 run。项目锁使用 `.inklink.lock` 记录当前 `run_id`，释放时只会删除属于当前 run 的锁。
