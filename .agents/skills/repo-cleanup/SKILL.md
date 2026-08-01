---
name: repo-cleanup
description: none
disable-model-invocation: true
---

# repo-cleanup

删仓库内**明确无用**的文件系统垃圾（缓存、OS/编辑器垃圾、点名的运行产物）。默认只列清单；确认后再删。文档归档用 `repo-hygiene`，不用本 skill。

## 输入

| 用户输入 | 行为 |
|----------|------|
| 无参数 / `dry-run` | 只扫只列，不删 |
| `apply` | 删**默认类别**命中项 |
| `apply` + 类别名（可多个） | 只删这些类别 |

## 类别

**默认**（`apply` 未点名时）：`pycache` / `pytest` / `logs` / `os` / `editor`。

| 类别 | 匹配 |
|------|------|
| `pycache` | `__pycache__/`、`*.pyc` / `*.pyo` / `*.pyd` |
| `pytest` | `.pytest_cache/` |
| `logs` | 仓库内 `*.log`（保护路径除外） |
| `os` | `.DS_Store`、`Thumbs.db`、`desktop.ini` |
| `editor` | `*~`、`*.swp`、`*.swo`、`.*.swp` |

**点名才清**（不在默认集合）：

| 类别 | 匹配 | 处理 |
|------|------|------|
| `node` | `node_modules/` | 直接删；汇报提示重装依赖 |
| `scratch` | `.scratch/` **内**草稿 | 清内容、保留目录；跳过活跃 task 引用路径 |
| `artifacts` | `artifacts/` 内容 | 清内容、保留目录 |
| `data` | `data/` 内容 | 清内容、保留目录 |

## 保护（永不删）

- `.git/`
- **业务与契约正文**：`src/`、`tests/`、`schemas/`、`config/` 下的源码、测试、契约与配置（**不是**类别表里的垃圾名）。  
  类别表命中的垃圾**可清**，即使落在这些目录下（如 `src/**/__pycache__/`、`tests/**/.pytest_cache/`）。
- `docs/` 下除 OS/编辑器垃圾文件名以外的一切（含 task 文档、specs、handoff/pending/findings）
- `scripts/` 入库脚本；`.agents/`、`.claude/` skill 与软链
- `AGENTS.md`、`README.md`、`CLAUDE.md`、`.gitignore`
- `docs/archive/tasks_audit.log`
- task worktree（`../{repo}_tNNN`）在仓库外，本 skill 不扫不删；正常完成后由 `task.py cleanup-worktree` 从主仓清理，active/blocked worktree 保留
- 不确定是否垃圾 → **不删**，列入「需用户决定」

## 步骤

1. **解析模式**：无 `apply` → dry-run；有 `apply` → 删除。类别：无点名 = 默认五类；`node` / `scratch` / `artifacts` / `data` 须点名。

2. **扫描**：在仓库根（`git rev-parse --show-toplevel`）按类别找命中项；对照保护名单过滤。示例：

   ```bash
   find . -type d -name '__pycache__' -not -path './.git/*'
   find . -type d -name '.pytest_cache' -not -path './.git/*'
   find . -type f \( -name '.DS_Store' -o -name 'Thumbs.db' -o -name '*.log' -o -name '*~' -o -name '*.swp' \) -not -path './.git/*'
   ```

3. **dry-run 输出**（到此结束，不删）：

   ```markdown
   ## repo-cleanup 预览（未删除）

   模式：dry-run
   类别：…

   | 路径 | 类别 | 说明 |
   |------|------|------|
   | ./tests/unit/__pycache__/ | pycache | 目录 |

   合计：N 项
   下一步：确认后 `/repo-cleanup apply`（或带类别）。
   ```

4. **apply 删除**（仅本次调用含 `apply`）：
   1. 再扫一遍，与 dry-run 同规则。
   2. 文件 `rm`；目录 `rm -rf`（**仅列表内路径**）。禁止 `rm -rf` 仓库根或保护路径。
   3. **`scratch`**（仅点名 `apply scratch`）：
      - 以登记 worktree、未合并 task 分支链尾 ref、main 的优先级确定 backlog/active/blocked task；读各有效来源中的 `spec.md` 上下文区与 `task.md` 实施笔记，收集提及的 `.scratch/` 相对路径 → **跳过不删**。main 中被 worktree或链尾覆盖的旧状态不重复计。
      - 其余 `.scratch/` 内容删掉，保留空目录。
      - 无法解析引用 → **不删** `.scratch/`，列入「需用户决定」。
   4. `artifacts` / `data`：清内容、保留目录。

5. **汇报**：

   ```markdown
   ## repo-cleanup 结果

   模式：apply / dry-run
   已删除：
   - …

   跳过（保护/被引用/不存在）：
   - …

   需用户决定：
   - …

   默认不 commit（见边界）。
   ```

## 边界

- 不替代 `repo-hygiene`；不改业务逻辑；不手改 task front matter / audit log。
- 派生 index JSON **不列入清理类别**（误删只需重跑 `task.py list --rebuild`）。
- 不把「好久没动的源码/文档」当垃圾。
- **commit**：默认不 commit。纯 gitignore 产物清理无跟踪 diff → 不 commit。仅当产生可跟踪 diff（如误提交的 `__pycache__`）且用户同意 → 单独维护期 commit；**不**擅自 commit。
- 与 `repo-hygiene` 分工：本 skill = 文件系统垃圾；hygiene = handoff/pending/过时文档迁 archive。

## 完成

输出预览或删除结果：已删 / 跳过 / 需用户决定。
