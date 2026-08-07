---
name: repo-template-sync
description: none
disable-model-invocation: true
---

# repo-template-sync

把 **repo_template** 的工具链与模板资产同步到**当前消费项目**。仅用户显式调用（`/repo-template-sync`）。禁止在模板仓本身当「推送源」跑。

状态文件（本 skill 旁，须入库）：

```text
.agents/skills/repo-template-sync/sync_state.json
```

## 输入

| 用户输入 | 行为 |
|----------|------|
| 无参数 / `dry-run` | 只读：解析源、列 diff，**绝不写盘**（含不写 state） |
| `apply` | 先出与 dry-run 相同预览；**须用户再确认**才写入硬同步路径并回写 state |
| `init` | 只写/修正 `template_source`（可建 state 骨架），不同步文件 |
| `status` | 只读：源、上次 commit、模板 HEAD、是否落后、消费侧相对 SRC 的差异摘要 |

## 边界（硬）

- **只在消费项目跑**。当前仓库根与 `template_source` 解析后为同一路径 → 立即停止并说明。
- **不自动 commit**。写入后列 diff，用户明确要求再提交。
- **不碰业务与项目状态**：`src/`、`assets/`、`schemas/`、`config/`、非 `repo_template` 的 `scripts/`、`docs/tasks/{tid}_*`、`docs/archive/`、`docs/pending/{todo,parked}/`、`docs/findings/dNNN_*`、`docs/specs*`、`docs/handoff.md`、`docs/runtime/`、`docs/reviews/review_*/`、`docs/spikes/{sid}_*`、`docs_repo/`、`README.md`。
- **不静默覆盖**项目会改的文件（见「只 diff」表）。
- 同步 `.agents/skills/` 时**必须保留**本目录 `sync_state.json`（模板侧无此文件或为空模板时不得抹掉消费侧状态）。
- **`status` / `dry-run` 零写盘**：不创建/不改 `sync_state.json`，不 rsync，不删文件。缺源或缺 state → 报告并要求用户跑 `init`，不自行落盘。

## 配置：`sync_state.json`

```json
{
  "template_source": {
    "kind": "path",
    "value": "/absolute/path/to/repo_template"
  },
  "last_synced_commit": "full-or-abbrev-sha-or-null",
  "last_synced_at": "2026-08-08T12:00:00+08:00"
}
```

| 字段 | 含义 |
|------|------|
| `template_source.kind` | `path`（本地复制/本机维护模板）或 `url`（git clone 来源） |
| `template_source.value` | 绝对路径，或 git remote URL（`https://…` / `git@…`） |
| `last_synced_commit` | 上次成功 `apply` 后模板仓 HEAD（完整 SHA 优先）；**仅当写入内容与该 commit 工作树一致时**才可推进 |
| `last_synced_at` | 上次成功 `apply` 的 ISO 8601（UTC+8） |

`kind`/`value` 为 `null`、缺失，或 state 文件不存在：

- `init`：可创建骨架并写入 `template_source`。
- `status` / `dry-run` / `apply`：**停**，提示先 `/repo-template-sync init`（可附带 path/url）。不在内存外持久化推断结果。

### `init`

1. 若无 `sync_state.json` → 按上表骨架创建（`last_synced_*` 置 `null`）。
2. 推断来源（按序，能用就停）：
   - 用户本轮明确给出 path 或 url。
   - 常见本机路径若存在且为 git 仓且像 `repo_template`：`~/karson_ubuntu/repo_template`。
   - 消费仓是否曾记录 template remote（不臆造；没有就不猜）。
3. **kind 选择**：
   - 本机可读目录且含 `scripts/repo_template/task.py` → `kind=path`，`value`=绝对路径。
   - 否则若有可用 git URL → `kind=url`。
   - 都没有 → 停，问用户给 path 或 url。
4. 写回 `template_source`；**不**改 `last_synced_commit`（除非用户说「把当前模板 HEAD 记为已同步基线」且工作树干净——见 path 脏检查）。
5. `init` 到此结束。

## 硬同步路径（以模板为准）

**统一语义（目录与单文件相同）**：

| 模板侧 | 消费侧 | 动作 |
|--------|--------|------|
| 存在 | 无 / 内容不同 | 写入（复制/覆盖） |
| 存在 | 相同 | 不动 |
| **不存在** | 存在 | **删除**（仅下列路径内） |
| 不存在 | 不存在 | 不动 |

同步前 dry-run **必须**把将写与将删全部列出。写入动作清单**只**来自「当前 `SRC` 文件树 ↔ 消费仓文件树」对比，**禁止**用 commit range 代替。

| 相对路径 | 说明 |
|----------|------|
| `scripts/repo_template/` | task 工具链整包（含 `repo_task/`） |
| `tests/repo_template/` | 工具链测试 |
| `.agents/skills/` | 全部 skill 正文；**排除** `repo-template-sync/sync_state.json` |
| `docs/tasks/task_template/` | task 文件模板 |
| `docs/reviews/prompts/` | review prompt |
| `docs/spikes/report_template.md` | spike 报告模板（单文件） |
| `docs/blueprint/architecture_repo_template.md` | 模板执行架构 |
| `.claude/hooks/merge_guard.py` | merge 授权 hook（单文件，删除语义同上） |

### 软链（`.claude/skills/`）

本机制管理的软链：目标为 `../../.agents/skills/<name>`（或等价解析到该目录）的符号链接。

1. **创建/修正**：对每个现存 `.agents/skills/<name>/`，确保 `.claude/skills/<name>` → `../../.agents/skills/<name>`。
2. **删除悬空**：`.claude/skills/<name>` 若为本机制管理的软链，且 `.agents/skills/<name>/` 已不存在 → dry-run 列入将删；apply 确认后 `rm` 该软链。
3. **不碰**：同名普通文件/目录、或指向其它目标的软链 → 报告「非本机制管理」，不删不改。
4. 根目录 `CLAUDE.md` → `AGENTS.md`：损坏时只报告，用户确认才改。

排除噪声（对比与 rsync 一律忽略）：`__pycache__/`、`*.pyc`、`.pytest_cache/`、`.DS_Store`。

## 只 diff、默认不写入

展示与模板差异；**不**自动覆盖。用户点名「覆盖某某」才动。

| 路径 | 原因 |
|------|------|
| `AGENTS.md` | 首行项目介绍 + 全文工作流；需人工合并 |
| `docs/blueprint/conventions.md` | 可能叠项目约定 |
| `.gitignore` | 项目会追加规则 |
| `.claude/settings.json` | 宿主/本地配置 |

## 解析模板工作树

记 `SRC` = 模板可读根目录，`T_HEAD` = 该仓 `git rev-parse HEAD`。

### `kind=path`

1. `value` 须存在且为目录。
2. 校验：`$value/scripts/repo_template/task.py` 存在。
3. `SRC=$value`。在 `SRC` 跑 `git rev-parse HEAD` 得 `T_HEAD`（非 git 仓 → 停，要求 git 管理的模板路径或改 `url`）。
4. **脏工作树检查**（硬同步路径范围内）：

   ```bash
   git -C "$SRC" status --porcelain -- \
     scripts/repo_template tests/repo_template .agents/skills \
     docs/tasks/task_template docs/reviews/prompts \
     docs/spikes/report_template.md \
     docs/blueprint/architecture_repo_template.md \
     .claude/hooks/merge_guard.py
   ```

   - 有输出 → 标 `SRC_DIRTY=true`，预览必须附 dirty 清单（相对 `T_HEAD` 的未提交变更）。
   - **`apply` 在 dirty 时**：
     - 仍可按工作树内容同步（预览已含这些 diff），但 **禁止** 将 `last_synced_commit` 推进为 `T_HEAD`（工作树 ≠ 该 commit）。
     - 汇报：`state 未推进：模板工作树 dirty`。用户须先在模板仓提交/清理，再跑一次干净 apply 才能记账。
   - 用户若只要记账不要同步：拒绝在 dirty 下把 HEAD 写成 last。

### `kind=url`

1. 缓存目录：消费仓 `.scratch/repo_template_sync_src/`（已 gitignore；可复用）。
2. 若缓存不存在：`git clone --depth 1 <url> .scratch/repo_template_sync_src`。
3. 若已存在：在缓存内 `git fetch --depth 1 origin` 并 `git checkout` / `reset --hard` 到 `origin` 默认分支 tip（以远程 HEAD 为准）。
4. `SRC` = 缓存路径；`T_HEAD` = 其 HEAD；`SRC_DIRTY=false`（reset 后干净）。
5. 网络失败 → 停，报告错误；不半写消费仓。
6. **浅克隆**：缓存通常只有 tip；`last_synced_commit` 对象多半不在本地。凡依赖「对象存在」的 git 命令失败时 → 当作无基线，**不得报错退出**。

### 与消费仓同一性

两边 `realpath` 后相同 → 拒绝（模板仓或误配）。`kind=url` 时比较消费根与缓存路径。

## 步骤

### 0. 定位

```bash
CONSUMER=$(git rev-parse --show-toplevel)
STATE="$CONSUMER/.agents/skills/repo-template-sync/sync_state.json"
```

### 1. 模式

`status` / `init` / `dry-run`（默认）/ `apply`。

### 2. 读 state → 解析源

- `init`：按上文写 state 后结束（或用户要求继续同步再往下）。
- 其它模式：
  - 无 `STATE` 或 `kind`/`value` 无效 → **停**，要求 `init`。不创建文件。
  - 有效 → 解析 `SRC`、`T_HEAD`、`SRC_DIRTY`。

### 3. `status`（只读）

输出：

1. `template_source`、`last_synced_commit`、`T_HEAD`、是否相同、`SRC_DIRTY`。
2. **模板更新摘要**（可选信息，不是写入清单）：
   - 若有 `last_synced_commit` 且 `git -C SRC cat-file -e <last>^{commit}` 成功 →  
     `git -C SRC diff --stat <last>..<T_HEAD> -- <硬同步路径>`。
   - 否则（无 last、对象不存在、浅克隆）→ 标「无可用 commit 基线（浅克隆或首同步）」；**不**因 diff 失败而退出。
3. **消费相对 SRC 的差异摘要**（与步骤 4 同一套树对比，可缩略）：硬同步路径上是否已与 SRC 一致。
4. 结束。零写盘。

### 4. 计算变更（dry-run 与 apply 共用；写入清单的唯一权威）

#### 4a. 写入/删除清单（强制：树对树）

对每个硬同步路径，对比 **当前 `SRC` 工作树** 与 **`CONSUMER` 对应路径**（忽略噪声）：

- 目录：递归；模板无、消费有 → **将删**；模板有、消费无或内容不同 → **将写**；相同 → 跳过。
- 单文件（`report_template.md`、`architecture_repo_template.md`、`merge_guard.py`）：
  - `SRC` 存在且与消费不同/消费无 → **将写**。
  - `SRC` 不存在且消费存在 → **将删**。
  - 两边都不存在 → 跳过。
- `.agents/skills/`：对比时与 rsync 一样排除 `repo-template-sync/sync_state.json`（消费侧该文件永不因模板缺失被删）。
- `.claude/skills/`：按「软链」节列出将建/将修/将删悬空链。

实现提示：`diff -rq`、对目录 `rsync -anc --delete …`（dry-run）、或等价；**apply 实际执行的动作集合必须 ⊆ 本清单**，不得对清单外路径 `--delete` 出预览未列的删除。

#### 4b. 模板更新摘要（信息性，可缺）

与 status 第 2 点相同：有 last 且对象在 SRC 中才做 `git diff last..T_HEAD`；否则写「无 commit 基线」。

**禁止**用 4b 代替 4a。场景：`last == T_HEAD` 但消费改过 `scripts/repo_template/` → 4b 空、4a 仍列出将覆盖项；预览必须显示将覆盖，不得显示「无变化」。

输出预览：

```markdown
## repo-template-sync 预览

模式：dry-run | apply（待确认）
消费仓：…
模板源：kind=… value=…
模板 HEAD：…  dirty：yes/no
上次同步：… @ …

### 模板更新摘要（信息）
… 或「无可用 commit 基线」…

### 将写入 / 覆盖（SRC ↔ 消费 树对比）
| 路径 | 动作 |
|------|------|
| scripts/repo_template/… | update |

### 将删除（硬同步路径内；含单文件与悬空软链）
| 路径 |
|------|
| … |

### 只 diff（未写入）
| 路径 | 摘要 |
|------|------|
| AGENTS.md | N lines differ |

### 软链
将建/修：…  将删悬空：…  非本机制跳过：…

### state 推进预期
- SRC 干净且验证将通过 → 可推进 last_synced_commit → T_HEAD
- SRC dirty → **禁止推进**（即使 apply 写入工作树内容）

下一步：确认后 `/repo-template-sync apply`（若本次已是 apply，等用户明确「确认写入」）。
```

`dry-run` 到此结束，不写盘。

### 5. `apply` 写入

1. 必须先完成步骤 4 预览。若本轮对话里用户尚未对**本次预览**说确认/写入/apply 同意 → **停，只展示预览**。
2. 仅执行预览清单中的写入与删除。推荐实现（噪声 exclude 与正文一致；**禁止**对仓库根裸 `rsync --delete`）：

   ```bash
   RSYNC_EXCL=(--exclude '__pycache__/' --exclude '*.pyc' \
     --exclude '.pytest_cache/' --exclude '.DS_Store')

   # 目录类（示例）
   rsync -a --delete "${RSYNC_EXCL[@]}" \
     "$SRC/scripts/repo_template/" "$CONSUMER/scripts/repo_template/"
   rsync -a --delete "${RSYNC_EXCL[@]}" \
     "$SRC/tests/repo_template/" "$CONSUMER/tests/repo_template/"
   rsync -a --delete "${RSYNC_EXCL[@]}" \
     "$SRC/docs/tasks/task_template/" "$CONSUMER/docs/tasks/task_template/"
   rsync -a --delete "${RSYNC_EXCL[@]}" \
     "$SRC/docs/reviews/prompts/" "$CONSUMER/docs/reviews/prompts/"

   # .agents/skills：删模板已去掉的 skill；保留消费侧 sync_state.json
   rsync -a --delete "${RSYNC_EXCL[@]}" \
     --exclude 'repo-template-sync/sync_state.json' \
     "$SRC/.agents/skills/" "$CONSUMER/.agents/skills/"

   # 单文件：存在则复制；不存在则删消费侧（与硬同步删除语义一致）
   sync_file() {
     local rel="$1"
     mkdir -p "$CONSUMER/$(dirname "$rel")"
     if [[ -e "$SRC/$rel" ]]; then
       rsync -a "$SRC/$rel" "$CONSUMER/$rel"
     elif [[ -e "$CONSUMER/$rel" || -L "$CONSUMER/$rel" ]]; then
       rm -f "$CONSUMER/$rel"
     fi
   }
   sync_file docs/spikes/report_template.md
   sync_file docs/blueprint/architecture_repo_template.md
   sync_file .claude/hooks/merge_guard.py
   ```

3. **软链**：为每个 `.agents/skills/<name>/` 建/修 `.claude/skills/<name>`；删除预览中列出的本机制悬空软链。
4. **验证**（失败不回滚已写文件；**不**推进 `last_synced_commit` 除非用户强行要求记账）：

   ```bash
   python3 -m pytest tests/repo_template/ -q
   ```

   无 pytest / 失败 → 「验证失败」；state 的 commit **不推进**。

5. **更新 state** 仅当同时满足：
   - 验证通过（或用户明确跳过测试），且
   - `SRC_DIRTY=false`（path 脏则**不推进**，即使已写入），

   然后写：

   ```json
   {
     "template_source": { "kind": "…", "value": "…" },
     "last_synced_commit": "<T_HEAD 完整 SHA>",
     "last_synced_at": "<ISO8601 +08:00>"
   }
   ```

   pretty-print（2 空格）、末尾换行。`template_source` 保持 init 已有值。

6. **不** `git add`/`commit`，除非用户本轮要求提交。

## 汇报

```markdown
## repo-template-sync 结果

模式：…
模板：… @ <T_HEAD>  dirty：…
上次 → 本次 last_synced：<old> → <new | 未推进（原因）>

已同步路径：…
已删除（含单文件/悬空软链）：…
软链：建/修/删…
只 diff 未动：…
验证：pass / fail / skipped
state：已更新 / 未更新（dirty | 验证失败 | 用户未确认 | …）

工作区 git status 摘要（若有）：…
默认未 commit。
```

## 完成条件

- `status` / `dry-run`：只读完成；无任何写盘；缺源时明确要求 `init`。
- `init`：仅 `sync_state.json` 的 `template_source`（及骨架）落盘。
- `apply`：用户确认后，预览清单内写入/删除完成；本机制软链无悬空；`last_synced_commit` 仅在「干净 SRC + 验证通过/跳过测试」时等于本次 `T_HEAD`；边界路径与「只 diff」路径未被擅自改动。
