# Worktree 状态栏追踪

多会话并发跑 task（多会话各执行 `task-run`）时，每个 Claude Code 会话的工作目录都是主仓根目录。状态栏的目录字段若不区分会话，无法看出该会话当前落在哪个 task worktree。本机制在状态栏显示各会话实际关联的 task worktree 名。

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

路径：`scripts/repo_template/track_worktree.py`。双角色：

- **hook writer 模式**（`--write --agent <name>`）：由 PostToolUse hook 调用。stdin 读 hook payload（`session_id`、`cwd`、`tool_input.command`），缺 session_id 或 cwd 直接返回；从命令文本提取 tid，解析 worktree，命中则 append 一条记录。
- **agent 标记模式**（无 `--write`，带 `--tid`）：校验 tid 并打印确认，实际写入仍由 hook 完成。

### tid 提取

从触发命令文本正则提取，三种形式（`track_worktree.py:100` `extract_tid`）：

- `--tid tNNN`
- `--tid=tNNN`
- `task.py start tNNN`（位置参数）

提取失败返回空，本次不写。

### ledger 解析与终态抵消

`resolve_worktree()`（`track_worktree.py:38`）顺序读 `docs/runtime/dispatch_ledger.jsonl`，只取该 tid 的记录，按事件推进状态：

|事件|窗口|说明|
|------|------|------|
|`start`|开|记录 worktree 相对路径并置 active（`integration.py` 写入，字段 `branch`/`worktree`）|
|`attempt_reserved` 且 `state=running`|保持|attempt 生命周期内窗口维持|
|`report` / `integrated` / `drop` / `attempt_terminal` / `attempt_report`|关|终态事件，worktree 不再有效（已合并或删除）|

最终仅当 active 且存在 worktree 时返回 `os.path.normpath(project + worktree)`；否则返回空串，本次不写。注意终态抵消只是"不再写新记录"，jsonl 中已存在的历史记录不会被清除——integrate 后最后一条记录仍指向已删除的 worktree，清理由消费侧负责（见下节路径校验）。

### 去重

append 前与 jsonl 末行比较（`track_worktree.py:77` `append_record`），完全相同则跳过，避免同一 Bash 命令反复触发刷屏。

## 消费者：statusline.py

路径：`$MY_FILE_CONFIG/claude_code/statusline.py`（claude-code 全局状态栏脚本）；`kimi_code_statusline.py`（kimi-code 变体）逻辑相同。

`resolve_workdir()`（`statusline.py:206`）读同一 jsonl，逆序扫描与 `(agent, session_id)` 匹配的记录：

- `session_id` 匹配隔离多会话，各会话互不污染。
- `session_id` 为空时降级为该 agent 最近一条记录（老会话或 payload 缺 session 的场景）。
- **路径校验**：命中记录后先 `os.path.isdir(worktree)`，目录已删（task 已 integrate/drop）则跳过继续向前扫；全死返回空。旧记录无需清理，消费侧自愈。

命中后 `project` 取 worktree basename，替代 `workspace.current_dir` 的 basename 渲染到 C_CWD 槽位；未命中则显示当前目录名。git 分支仍由 `git branch --show-current` 实时取。

## 配置落点

|位置|作用|
|------|------|
|项目 `.claude/settings.json` `hooks.PostToolUse`（matcher=Bash）|每次 Bash 调用后执行 `track_worktree.py --write --agent claude-code`，timeout 2s|
|全局 `~/.claude/settings.json` `statusLine.command`|指向 `statusline.py`，状态栏刷新时执行|

## 边界与失效场景

- ledger 文件缺失或不可读 → `resolve_worktree` 返回空，不写。
- tid 提取失败 / 非 task 相关命令 → 不写。
- 无 `session_id` 的 hook payload → writer 直接返回。
- task integrate/drop 后 → 生产端不再写新记录，jsonl 历史记录指向已删 worktree；消费端 `isdir` 校验跳过，状态栏回落到当前目录名。
- 同 jsonl 随 `.scratch/` 被 gitignore，仅本地会话态，不随仓库分发。
