---
name: issue-handler
description: 模板仓（工厂仓）处理 GitHub 上报的模板问题。优先读取远程 GitHub Issues，必要时回退扫描消费仓本地交接报告；复现并定位根因，向用户报告问题分析与修复计划，批准后改 repo/.repo_template/ 与 repo 根模板文件（AGENTS.md / CLAUDE.md）执行修复并跑测试。当有模板仓 GitHub issue、消费仓上报或用户让「处理/修复模板仓 issue」时调用。仅在模板仓本体运行，不进消费仓产物。
---

# issue-handler

模板仓（工厂仓）的**实现方** skill：消费仓 agent 通过 `template-issue-report` 写交接报告（只描述现象与期望），本 skill 接手——复现、定位根因、定方案、执行修复。

**本 skill 只在模板仓本体（工厂仓，cwd = `repo_template` 工厂根）运行**。它是工厂仓私有工具，不进 `repo/` 产物，不随模板分发到消费仓。

## 分工对照

| 角色 | skill | 职责 |
| --- | --- | --- |
| 需求方 | 消费仓 `template-issue-report` | 观察现象、写报告，不定位根因、不给方案 |
| 实现方 | 本 skill（模板仓） | 复现、读实现、定位根因、报告计划、修复 |

消费仓报告只到「期望行为」为止；根因与方案由本 skill 补全。GitHub Issue 是主交接渠道，本地 `.scratch/repo_template_issues/` 仅作离线回退和复现补充。

## 硬边界

- **只改工厂仓真相源**：`repo/.repo_template/`，以及 `repo/` 根模板文件 `AGENTS.md` / `CLAUDE.md`。消费仓是副本，通过 `repo-template-sync` 拉取修复；绝不直接改消费仓里的模板产物。
- **报告后必须等用户批准再改代码**。模板仓改动影响所有消费仓，方案可能有多种取舍；分析+计划先呈用户，批准前不动上述真相源。
- **复现与定位可读实现**：本 skill 是模板仓 agent，可读 `repo/.repo_template/scripts/*.py`、`hooks/*.py`、`skills/*` 等实现源码——这是它与需求方 skill 的区别。
- **复现不改生产树**：在工厂仓 `repo/` 复现用只读命令或 `.scratch/` 隔离；进消费仓复现同样只读 + `.scratch/` 隔离，不碰消费仓生产文件。

## 流程

### 1. 扫描远程 GitHub Issues（主入口）

先确认模板仓远程地址和 `gh` 登录状态：

```bash
git remote get-url origin
gh auth status
gh issue list --repo <owner>/<repo> --state open --limit 50 --json number,title,createdAt,updatedAt,url
```

- GitHub `open` Issue 是主待处理队列；用 `gh issue view <number> --repo <owner>/<repo>` 读取正文和评论。
- 已关闭 Issue 默认跳过，除非用户明确要求复查。
- 多个 open Issue 时列出编号、标题、更新时间，先让用户确认处理哪个；不要把同一 Issue 的重复本地文件当成多个问题。
- 不要默认创建、修改或关闭 Issue；创建由消费仓 `template-issue-report` 完成，关闭/评论需在修复流程中按回执规则执行。

### 1.1 GitHub 不可用时回退本地报告

仅当 `gh` 不可用、未登录，或用户明确要求核对本地交接时，才扫描消费仓本地报告：

```bash
find ~/karson_ubuntu -path '*/.scratch/repo_template_issues/*.md' -type f -printf '%T@ %p\n' 2>/dev/null | sort -rn
```

读取不带 `.issue.md` 的完整版；列清单（路径 + 修改时间），已含「## 处理结果」段的报告默认跳过。若 GitHub 和本地同时存在，以 GitHub Issue 为准，本地文件只用于补充复现上下文。

### 2. 读 Issue / 报告

理解：分类（`bug` / `需求`）、问题概述、现象（触发操作 + 报错原文）、涉及模板仓组成部分、期望行为。GitHub Issue 正文优先；必要时读取对应消费仓本地完整版补充真实路径和状态。

### 3. 复现

- 优先在工厂仓 `repo/` 复现——`repo/` 就是模板真相源，消费仓是它的副本。
- 现象依赖消费仓特定状态（特定文件、git 状态、配置）时，进入对应消费仓复现：只读 + 写入 `.scratch/` 隔离。
- 复现不了或报告信息不足 → 回消费仓补现象，或向用户问清，不猜测。

### 4. 定位根因

读 `repo/.repo_template/` 下相关实现，定位根因机制。这是与需求方 skill 的分界——本步读实现、下结论。

### 5. 报告分析与计划（等批准）

向用户呈现：

- **问题分析**：根因机制、影响面（哪些消费仓 / 场景受影响）。
- **修复计划**：改哪些文件、怎么改、补什么测试、验证方式。
- 方案有取舍时列出备选与推荐，不默认独断。

**批准前不写代码。** 用户明确同意后进第 6 步。

### 6. 执行修复

改工厂仓真相源：`repo/.repo_template/`（脚本 / skill / hook / 模板文件 / 约定文档）与 `repo/` 根模板文件 `AGENTS.md` / `CLAUDE.md`，按需补或改测试。

### 7. 验证

```bash
pytest repo/.repo_template/tests -q
```

失败 → 修到绿，不提交。

### 8. 回执

GitHub 可用时，在对应 Issue 追加评论，写明：修复结论、涉及的工厂仓改动文件、测试结果、修复 commit（若已提交）以及消费仓同步提示；修复已验证且用户确认关闭时再关闭 Issue。不要把 GitHub 回执写回消费仓生产文件。

GitHub 不可用时，才在对应消费仓本地报告末尾追加「## 处理结果」段，内容同上，供人工交接。

### 9. 汇报与提交

汇总改动，向用户报告；commit 走正常门禁——列改动文件，用户同意后提交，不 `push` 除非用户另说。消费仓通过 `repo-template-sync` 拉取修复。

## 完成

问题已定位、修复已落地工厂仓真相源并通过测试，报告已回执，消费仓待同步。未批准不写代码、未验证不提交。
