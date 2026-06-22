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

同一输入目录一次只能有一个活跃 run。当前项目锁实现依赖 POSIX `fcntl`，使用 `.inklink.lock` 记录当前 `run_id`，并通过输入目录的 OS 级文件锁保持互斥；释放时解锁并关闭持有的目录文件描述符，不按路径删除锁文件。

## 确定性检查

当前确定性检查函数覆盖：

- 章节合同和草稿的章节号是否一致。
- 中文字数是否落在章节合同区间。
- 章节合同要求的人物名和关键词是否出现在正文中。
- 已解决或废弃的伏笔是否被重复标记为回收。

中文字数按当前 Python `unicodedata` 支持版本统计已分配的 Unicode CJK 统一表意文字及其扩展区字符；标点、空格、换行、阿拉伯数字、拉丁字母、英文单词和未分配码位不计入。

## 结构化索引

结构化故事索引以提及事实为底层数据。人物提及事实使用 `(entity_id, chapter_number, generation)` 作为身份键，重复合并同一事实会替换旧值，不会重复追加。人物的首次/最后出现章节、相关章节和活跃分数从当前有效事实重算，因此章节分析乱序完成或重复恢复不会改变结果。

放弃某一章节 generation 时，索引会撤回该 generation 的事实贡献，再重算人物条目。若某人物没有剩余有效事实，对应索引条目会被移除。
