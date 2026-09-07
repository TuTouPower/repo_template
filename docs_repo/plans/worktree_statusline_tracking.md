# Worktree 状态栏追踪

多会话并发跑 task（各会话执行 `task-run`）且从消费仓主仓根启动时，状态栏的目录字段若不区分会话，无法看出该会话当前落在哪个 task worktree。本机制在状态栏显示各会话实际关联的 task worktree 名。

> **状态（2026-08-12 核对；2026-09-07 勘误）**：现行宿主附属机制。生产者是 `.repo_template/scripts/track_worktree.py` + 项目 `.claude/settings.json` 的 PostToolUse hook。L36 否决删除本 hook。路径与终态事件以脚本为准。

## 数据流

```
Bash 工具调用
  │  PostToolUse hook（项目 .claude/settings.json）
  ▼
track_worktree.py --write --agent claude-code
  │  从命令提取 tid → 查 dispatch_ledger.jsonl 解析 worktree
  ▼
<项目>/.scratch/statusline_workdir.jsonl   ← append 一行 {agent, session_id, tid, worktree, ts}
  │  状态栏刷新（全局 ~/.claude/settings.json statusLine.command）
  ▼
statusline.py resolve_workdir()   ← 按 (agent, session_id) 逆序取记录，isdir 校验跳过死路径
  ▼
状态栏第 3 字段（C_CWD 槽位）用 worktree basename 替代 cwd basename
```

生产-消费解耦：hook 只管写 jsonl，状态栏只管读。两侧无直接依赖，消费侧降级不影响生产侧。

## 生产者：track_worktree.py

路径：`.repo_template/scripts/track_worktree.py`。双角色：

- **hook writer 模式**（`--write --agent <name>`）：由 PostToolUse hook 调用。stdin 读 hook payload（`session_id`、`cwd`、`tool_input.command`），缺 session_id 或 cwd 直接返回；从命令文本提取 tid，解析 worktree，命中则 append 一条记录。
- **agent 标记模式**（无 `--write`，带 `--tid`）：尝试解析该 tid 的 worktree 并打印结果，不写文件；只要求 `--tid` 非空，不校验编号格式。实际写入由 hook 完成，exit 0 不代表已找到或写入 worktree。

### tid 提取

从触发命令文本正则提取，三种形式（`extract_tid`）：

- `--tid tNNN`
- `--tid=tNNN`
- 包含 `start tNNN` 的命令文本（实现匹配 `start` + tid，并未限定必须是 task.py）

提取失败返回空，本次不写。

### ledger 解析与终态抵消

`resolve_worktree()` 顺序读 `docs/runtime/dispatch_ledger.jsonl`，只取该 tid 的记录，按事件推进状态：

| 事件 | 窗口 | 说明 |
| --- | --- | --- |
| `start` | 开 | 记录 worktree 相对路径并置 active |
| `attempt_reserved` 且 `state=running` | 开 | 重新打开窗口，沿用此前 start 记录的路径 |
| `report` / `integrated` / `attempt_terminal` | 关 | 关闭本次解析窗口，不证明 worktree 已删除；阻塞时目录仍可能保留 |

最终仅当 active 且已有非空 worktree 路径字段时返回 `os.path.normpath(os.path.join(project, worktree))`；否则返回空串，本次不写；生产端不检查目录是否存在。注意终态抵消只是"不再写新记录"，jsonl 中已存在的历史记录不会被清除——integrate 后最后一条记录仍指向已删除的 worktree，清理由消费侧负责（见下节路径校验）。

### 去重

append 前与 jsonl 末行比较（`append_record`），序列化文本完全相同才跳过。hook 每次生成新的 `ts`，因此同一 Bash 命令再次触发通常仍会追加；这不是按 tid 或命令去重。

## 消费者：statusline.py

本机已配置的入口为 `$MY_CODING_AGENT_DIR/scripts/statusline.py`；同目录 `kimi_code_statusline.py` 的 `resolve_workdir()` 使用相同读取逻辑。二者属于仓外个人工具，不随模板分发；其它机器需核对自身 `statusLine.command` 与对应实现，不能仅复制项目 hook 就认为显示端已启用。

`resolve_workdir()` 读同一 jsonl，逆序扫描与 `(agent, session_id)` 匹配的记录：

- `session_id` 匹配隔离多会话，各会话互不污染。
- `session_id` 为空时降级为该 agent 最近一条记录（老会话或 payload 缺 session 的场景）。
- **路径校验**：命中记录后先 `os.path.isdir(worktree)`，目录已删（task 已 integrate/drop）则跳过继续向前扫；全死返回空。旧记录无需清理，消费侧自愈。

命中后 `project` 取 worktree basename，替代 `workspace.current_dir` 的 basename 渲染到 C_CWD 槽位；未命中则显示当前目录名。git 分支仍由 `git branch --show-current` 实时取。

## 配置落点

| 位置 | 作用 |
| --- | --- |
| 项目 `.claude/settings.json` `hooks.PostToolUse`（matcher=Bash） | 每次 Bash 调用后执行 `track_worktree.py --write --agent claude-code`，timeout 2s |
| 全局 `~/.claude/settings.json` `statusLine.command` | 指向 `statusline.py`，状态栏刷新时执行 |

## 边界与失效场景

- ledger 文件缺失或不可读 → `resolve_worktree` 返回空，不写。
- tid 提取失败 → 不写；命令中恰好出现 `start tNNN` 也可能命中，不能据显示结果证明执行过 task 命令。
- 无 `session_id` 的 hook payload → writer 直接返回。
- terminal/report 后 → 生产端关闭解析窗口，但消费者不读 ledger；若历史记录的目录仍存在（例如 blocked 留场），状态栏仍可能显示它。只有目录删除后才因 `isdir` 失败跳过；更早的有效记录仍可能命中。
- `attempt_reserved` 必须携带 `state=running` 才重新打开窗口；未携带此字段的事件不会重开。应以真实 ledger 字段核对，不能把此显示机制当作执行状态权威。
- 同 jsonl 随 `.scratch/` 被 gitignore，仅本地会话态，不随仓库分发。

## 验证与恢复

先静态核对项目 hook、仓外消费者与全局 `statusLine.command` 三个入口。可在临时目录构造 start/terminal 账本与目录存活/删除场景，分别调用生产端解析函数和消费者 `resolve_workdir()` 检查上述边界；宿主端到端显示须另在实际会话验证。没有 writer 记录时先核对 payload 的 cwd、session_id、命令和账本，不手改 task 状态。显示错误不改变 task 执行事实，以 `task.py effective-status` / `recovery` 核对工作区。
