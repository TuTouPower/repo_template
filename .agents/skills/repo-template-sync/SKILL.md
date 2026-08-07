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
| `apply` | 先出与 dry-run 相同预览；**须用户再确认**才写入；回写 state |
| `init` | 只写/修正 `template_source`（可建 state 骨架），不同步文件 |
| `status` | 只读：源、上次 commit、模板 HEAD、是否落后、差异摘要 |

## 边界（硬）

- **只在消费项目跑**。当前仓库根与 `template_source` 解析后为同一路径 → 立即停止并说明。
- **不自动 commit**。写入后列 diff，用户明确要求再提交。
- **不碰业务与项目状态**：`src/`、`assets/`、`schemas/`、`config/`、非 `repo_template` 的 `scripts/`、`docs/tasks/{tid}_*`、`docs/archive/`、`docs/pending/{todo,parked}/`、`docs/findings/dNNN_*`、`docs/specs*`、`docs/handoff.md`、`docs/runtime/`、`docs/reviews/review_*/`、`docs/spikes/{sid}_*`、`docs_repo/`、`README.md`。
- **技能 / MCP / 宿主 agent 配置禁止硬覆盖整树**（见「裁定同步」）。消费项目常有自建 skill、MCP、settings 片段；**不得**对 `.agents/skills/`、MCP 清单、宿主 settings 做 `rsync --delete` 或整文件静默覆盖。
- 本 skill 的 `sync_state.json` 永不被模板抹掉。
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

## 两类同步

| 类型 | 范围 | 策略 |
|------|------|------|
| **硬同步** | 纯模板工具链与文档模板 | 树对树：以 SRC 为准写入/删除；预览后确认再 apply |
| **裁定同步** | skill、MCP、宿主 agent 配置 | **禁止**整树 `--delete` / 整文件静默盖；agent **逐项**分类 + 写 rationale；默认偏保留消费侧；拿不准 → 待用户决定 |

写入动作清单**只**来自「当前 `SRC` ↔ 消费」对比（硬同步）或「逐项裁定结果」（裁定同步）。commit range 仅作信息摘要，**禁止**代替写入清单。

---

## 硬同步路径

**统一语义（仅下列路径）**：

| 模板侧 | 消费侧 | 动作 |
|--------|--------|------|
| 存在 | 无 / 内容不同 | 写入（复制/覆盖） |
| 存在 | 相同 | 不动 |
| **不存在** | 存在 | **删除**（仅下列路径内） |
| 不存在 | 不存在 | 不动 |

| 相对路径 | 说明 |
|----------|------|
| `scripts/repo_template/` | task 工具链整包（含 `repo_task/`） |
| `tests/repo_template/` | 工具链测试 |
| `docs/tasks/task_template/` | task 文件模板 |
| `docs/reviews/prompts/` | review prompt |
| `docs/spikes/report_template.md` | spike 报告模板（单文件） |
| `docs/blueprint/architecture_repo_template.md` | 模板执行架构 |
| `.claude/hooks/merge_guard.py` | merge 授权 hook（单文件；SRC 无且消费有 → 删） |

排除噪声（对比与 rsync 一律忽略）：`__pycache__/`、`*.pyc`、`.pytest_cache/`、`.DS_Store`。

---

## 裁定同步（skill / MCP / agent 宿主）

消费仓可有**项目自有** skill、MCP、宿主配置。本节路径**永不** `rsync --delete` 整树，**永不**在未逐项裁定时整文件覆盖。

### 裁定范围（出现则纳入）

| 路径 / 单元 | 粒度 |
|-------------|------|
| `.agents/skills/<name>/` | 每个 skill 目录（**排除** `repo-template-sync/sync_state.json` 内容被模板抹掉） |
| `.claude/skills/<name>` | 与 skill 配套的软链/副本（见下） |
| `.grok/skills/<name>/` | 若消费或模板存在 |
| MCP 清单文件（任一侧存在即对比） | 如 `.mcp.json`、`.cursor/mcp.json`、`.vscode/mcp.json`、项目文档约定的其它 MCP manifest；以实际扫到的为准，不臆造 |
| 宿主 settings 中与 MCP/hooks 相关的片段 | 如 `.claude/settings.json`、Cursor/VSCode 相关 settings——**只裁定/合并建议**，默认不整文件覆盖 |

「只 diff」表中的 `AGENTS.md`、`conventions.md`、`.gitignore` 仍只 diff，不进自动写入（除非用户点名）。

### 分类（每个单元必标一类）

| 类 | 条件 | 默认 disposition |
|----|------|------------------|
| `template_only` | 仅 SRC 有 | **建议新增**（从模板拷入）；仍列预览，确认后写 |
| `consumer_only` | 仅消费有 | **保留**；**禁止**因模板没有而删除 |
| `both_identical` | 两边内容等价（忽略噪声） | 跳过 |
| `both_differ` | 两边都有且内容不同 | **agent 裁定**（见下），禁止静默盖 |

### Agent 裁定规则（`both_differ` 与可疑项）

对每个单元**读两边关键内容**（skill 的 `SKILL.md` 头与职责段；MCP 的 server 名与命令；settings 的 mcp/hooks 键），写一行 rationale，给出 disposition：

| disposition | 含义 | 何时用 |
|-------------|------|--------|
| `update_from_template` | 用模板覆盖该单元 | 消费侧几乎等于旧模板拷贝，无项目定制痕迹；或用户本轮点名升级该 skill |
| `keep_consumer` | 保留消费侧 | 含项目业务、自有 MCP server、项目路径、非模板 skill 名、明显本地改写 |
| `merge_manual` | 需人工合并 | 两边都有实质改动（模板演进 + 消费定制） |
| `ask_user` | 待用户决定 | 判断不确定 |

**偏见**：不确定 → `ask_user` 或 `keep_consumer`，**绝不**默认 `update_from_template`。

**模板 skill 名提示**（仅启发，非白名单硬编码唯一真相）：`task-*`、`repo-hygiene`、`repo-cleanup`、`repo-template-sync` 等与模板工作流同名的 skill 更可能该升级；但消费侧若改过正文 → 仍按 `both_differ` 读 diff 再裁，不得因「同名」就覆盖。

**MCP**：

- 按 **server 名 / 键** 对比，不要整文件当原子块硬盖。
- 模板新增 server → 建议并入（`template_only` 键）。
- 消费独有 server → `consumer_only`，保留。
- 同名 server 配置不同 → `both_differ`：保留消费 endpoint/密钥占位；仅当消费明显是旧模板片段且无项目字段时才建议更新命令/args。
- **禁止**把消费侧密钥、token、绝对本机路径冲掉。

**`repo-template-sync` 自身**：可更新 `SKILL.md`（若模板更新了本 skill）；**禁止**用模板的空 `sync_state.json` 覆盖消费侧 state。

### 软链（`.claude/skills/`，属裁定配套）

本机制管理的软链：目标为 `../../.agents/skills/<name>`（或等价解析到该目录）。

1. 对 **apply 后仍存在** 的 `.agents/skills/<name>/`：确保对应软链正确（缺失或指错则建/修）。
2. **禁止**因「模板没有该 skill」删除消费侧 skill 目录或其软链（`consumer_only`）。
3. 仅当某 skill 经裁定为「从消费删除」（用户确认的模板退役 + 消费无定制）且目录已删时，才删对应本机制悬空软链。
4. 非本机制管理的文件/目录/外指软链：报告，不删不改。
5. `CLAUDE.md` → `AGENTS.md`：损坏只报告，用户确认才改。

---

## 解析模板工作树

记 `SRC` = 模板可读根目录，`T_HEAD` = 该仓 `git rev-parse HEAD`。

### `kind=path`

1. `value` 须存在且为目录。
2. 校验：`$value/scripts/repo_template/task.py` 存在。
3. `SRC=$value`。`git rev-parse HEAD` → `T_HEAD`（非 git 仓 → 停）。
4. **脏工作树检查**（硬同步路径 + 裁定范围内模板侧文件）：

   ```bash
   git -C "$SRC" status --porcelain -- \
     scripts/repo_template tests/repo_template .agents/skills \
     docs/tasks/task_template docs/reviews/prompts \
     docs/spikes/report_template.md \
     docs/blueprint/architecture_repo_template.md \
     .claude/hooks/merge_guard.py
   ```

   - 有输出 → `SRC_DIRTY=true`，预览附 dirty 清单。
   - **`apply` 在 dirty 时**：可按工作树写入已确认项，但 **禁止** 推进 `last_synced_commit`。

### `kind=url`

1. 缓存：消费仓 `.scratch/repo_template_sync_src/`（gitignore；可复用）。
2. 无则 `git clone --depth 1 <url> …`；有则 fetch + reset 到远程默认分支 tip。
3. `SRC` = 缓存；`T_HEAD` = HEAD；`SRC_DIRTY=false`。
4. 网络失败 → 停，不半写。
5. 浅克隆下 `last_synced_commit` 对象常不存在 → 当无基线，**不**因此失败退出。

### 与消费仓同一性

两边 `realpath` 相同 → 拒绝。

## 步骤

### 0. 定位

```bash
CONSUMER=$(git rev-parse --show-toplevel)
STATE="$CONSUMER/.agents/skills/repo-template-sync/sync_state.json"
```

### 1. 模式

`status` / `init` / `dry-run`（默认）/ `apply`。

### 2. 读 state → 解析源

- `init`：写 state 后结束（或用户要求继续）。
- 其它：无 STATE 或 kind/value 无效 → **停**，要求 `init`。不创建文件。

### 3. `status`（只读）

1. `template_source`、`last_synced_commit`、`T_HEAD`、是否相同、`SRC_DIRTY`。
2. **模板更新摘要**（信息）：有 last 且对象在 SRC →  
   `git -C SRC diff --stat <last>..<T_HEAD> -- <硬同步路径> .agents/skills`；  
   否则「无可用 commit 基线」。
3. 硬同步是否与 SRC 一致（缩略）；裁定范围 skill/MCP 是否有 diverged（计数即可）。
4. 零写盘结束。

### 4. 计算变更（dry-run 与 apply 共用）

#### 4a. 硬同步清单（树对树）

对每个**硬同步路径**：模板无消费有 → 将删；模板有且不同 → 将写。  
单文件用同一语义（`sync_file`：有则拷，无则删消费侧）。

#### 4b. 裁定同步清单（逐项）

1. 枚举裁定范围内的单元（skill 名、MCP 文件、settings 若需）。
2. 每项标 classification + disposition + **一句话 rationale**（`both_differ` / 可疑项必写）。
3. 汇总：
   - **建议写入/更新**：`template_only` 建议新增 + disposition=`update_from_template`
   - **保留不动**：`consumer_only`、`keep_consumer`、`both_identical`
   - **待用户决定**：`ask_user`、`merge_manual`
4. 软链：仅对将存在的 skill 列建/修；**不对** `consumer_only` 列删除。

#### 4c. 模板更新摘要（信息）

同 status；对象缺失则「无 commit 基线」。禁止用 4c 代替 4a/4b。

输出预览：

```markdown
## repo-template-sync 预览

模式：dry-run | apply（待确认）
消费仓：…
模板源：kind=… value=…
模板 HEAD：…  dirty：yes/no
上次同步：… @ …

### 模板更新摘要（信息）
…

### 硬同步 — 将写入 / 覆盖
| 路径 | 动作 |
|------|------|
| scripts/repo_template/… | update |

### 硬同步 — 将删除
| 路径 |
|------|
| … |

### 裁定同步 — 逐项
| 单元 | 分类 | disposition | rationale |
|------|------|--------------|-----------|
| .agents/skills/task-run | both_differ | update_from_template | 消费侧与旧模板一致，模板有修复 |
| .agents/skills/my-proj | consumer_only | keep_consumer | 项目自有 skill |
| .mcp.json#context7 | both_differ | keep_consumer | 消费侧含项目 token 配置 |
| .agents/skills/task-bug | both_differ | ask_user | 两边均有实质改动 |

### 只 diff（未写入）
| 路径 | 摘要 |
|------|------|
| AGENTS.md | … |

### 软链
将建/修：…  不删 consumer_only：…

### state 推进预期
- SRC 干净且验证将通过 → 可推进 last → T_HEAD
- SRC dirty → 禁止推进

下一步：确认后 `/repo-template-sync apply`。对 `ask_user` / `merge_manual` 须逐项答复后再写入对应项。
```

`dry-run` 到此结束。

### 5. `apply` 写入

1. 须有本轮预览且用户确认。对预览中 `ask_user` / `merge_manual`：**未获该单元明确答复前不得写入该单元**（其它已确认项可写）。
2. **硬同步**（禁止对仓库根裸 `rsync --delete`）：

   ```bash
   RSYNC_EXCL=(--exclude '__pycache__/' --exclude '*.pyc' \
     --exclude '.pytest_cache/' --exclude '.DS_Store')

   rsync -a --delete "${RSYNC_EXCL[@]}" \
     "$SRC/scripts/repo_template/" "$CONSUMER/scripts/repo_template/"
   rsync -a --delete "${RSYNC_EXCL[@]}" \
     "$SRC/tests/repo_template/" "$CONSUMER/tests/repo_template/"
   rsync -a --delete "${RSYNC_EXCL[@]}" \
     "$SRC/docs/tasks/task_template/" "$CONSUMER/docs/tasks/task_template/"
   rsync -a --delete "${RSYNC_EXCL[@]}" \
     "$SRC/docs/reviews/prompts/" "$CONSUMER/docs/reviews/prompts/"

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

3. **裁定同步**（按预览 disposition，**禁止**对 `.agents/skills/` 整树 `--delete`）：

   ```bash
   # 仅对 disposition=update_from_template 或 template_only 且已确认的 skill：
   # rsync -a "${RSYNC_EXCL[@]}" "$SRC/.agents/skills/<name>/" "$CONSUMER/.agents/skills/<name>/"
   # 更新本 skill 的 SKILL.md 时排除 sync_state.json：
   # rsync -a --exclude 'sync_state.json' ...

   # MCP/settings：按裁定合并写入；禁止整文件覆盖含密钥的消费配置
   # consumer_only / keep_consumer：跳过
   ```

4. **软链**：为仍存在的 `.agents/skills/<name>/` 建/修链接；只删「用户确认删除的 skill」留下的本机制悬空链。
5. **验证**：

   ```bash
   python3 -m pytest tests/repo_template/ -q
   ```

   失败 → state 不推进。

6. **更新 state** 仅当：验证通过或用户跳过测试，且 `SRC_DIRTY=false`。  
   注意：存在未处理的 `ask_user` 项时仍可推进 last（表示「已处理本轮确认范围」），但汇报须列出**未同步的裁定项**；若用户要求「全部裁定完成才记账」则从其要求。

7. 不自动 `git commit`。

## 汇报

```markdown
## repo-template-sync 结果

模式：…
模板：… @ <T_HEAD>  dirty：…
上次 → 本次 last_synced：…

硬同步：写入… 删除…
裁定同步：更新… 保留… 跳过/待决…
软链：…
只 diff 未动：…
验证：pass / fail / skipped
state：已更新 / 未更新（原因）

默认未 commit。
```

## 完成条件

- `status` / `dry-run`：只读；无写盘；缺源要求 `init`。
- `init`：仅 state 源字段（及骨架）。
- `apply`：硬同步按清单完成；裁定项仅写入用户确认且 disposition 允许的单元；**无任何 consumer_only skill/MCP 被删或被静默覆盖**；`sync_state.json` 未被模板空文件覆盖；软链不误删项目 skill；last 仅在干净 SRC + 验证策略满足时推进。
