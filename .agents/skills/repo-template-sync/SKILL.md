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
| 无参数 / `dry-run` | 只读：解析源、列 diff + **完整裁定表**，**绝不写盘**（含不写 state） |
| `apply` | 先出与 dry-run 相同预览；**须用户再确认**才按 disposition 写入；回写 state |
| `init` | 只写/修正 `template_source`（可建 state 骨架），不同步文件 |
| `status` | 只读：源、上次 commit、模板 HEAD、是否落后、差异摘要 |

## 边界（硬）

- **只在消费项目跑**。当前仓库根与 `template_source` 解析后为同一路径 → 立即停止并说明。
- **不自动 commit**。写入后列 diff，用户明确要求再提交。
- **不碰业务与项目状态**：`src/`、`assets/`、`schemas/`、`config/`、非 `repo_template` 的 `scripts/`、`docs/tasks/{tid}_*`、`docs/archive/`、`docs/pending/{todo,parked}/`、`docs/findings/dNNN_*`、`docs/specs*`、`docs/handoff.md`、`docs/runtime/`、`docs/reviews/review_*/`、`docs/spikes/{sid}_*`、`docs_repo/`、`README.md`（业务介绍）。
- **禁止整树/整文件静默硬盖** skill、MCP、宿主配置、以及常被项目改写的共享文稿（`AGENTS.md`、`conventions.md`、`.gitignore` 等）——一律走「裁定同步」。
- **有差异就必须裁定并报告**。不存在「只展示 diff、默认可永久不处理」的桶；差异要么写入（`update` / `merge`）、要么明确 `keep_consumer`（写清为什么保留）、要么 `ask_user`。禁止用「只 diff」当借口跳过该更新的模板演进。
- 本 skill 的 `sync_state.json` 永不被模板抹掉。
- **`status` / `dry-run` 零写盘**：不创建/不改 `sync_state.json`，不 rsync，不删文件。缺源或缺 state → 报告并要求用户跑 `init`。

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
| `template_source.kind` | `path` 或 `url` |
| `template_source.value` | 绝对路径或 git remote URL |
| `last_synced_commit` | 上次成功 `apply` 后模板 HEAD（完整 SHA）；仅当写入与该 commit 工作树一致时才可推进 |
| `last_synced_at` | ISO 8601（UTC+8） |

缺源：`init` 可写骨架；其它模式停并要求 `init`。

### `init`

1. 无 state → 建骨架（`last_synced_*` = null）。
2. 推断 path/url（用户给定 → 常见本机路径 → 不臆造）。
3. 本机含 `scripts/repo_template/task.py` → `kind=path`；否则 url。
4. 只写 `template_source`。

## 两类同步

| 类型 | 范围 | 策略 |
|------|------|------|
| **硬同步** | 纯模板工具链与无项目定制的文档模板 | 树对树：以 SRC 为准写入/删除；预览确认后 apply |
| **裁定同步** | skill / MCP / 宿主配置 / 共享文稿（AGENTS、conventions、gitignore 等） | **禁止**整树 `--delete` 与未裁定整文件盖；agent **读 diff、逐项裁定、写 rationale**；该合就合、该留就留；拿不准 → `ask_user` 并**明示用户** |

写入清单权威：硬同步 = 树对树；裁定 = 逐项 disposition。commit range 仅信息摘要。

---

## 硬同步路径

| 模板侧 | 消费侧 | 动作 |
|--------|--------|------|
| 存在 | 无 / 内容不同 | 写入 |
| 存在 | 相同 | 不动 |
| 不存在 | 存在 | **删除**（仅下列路径） |

| 相对路径 | 说明 |
|----------|------|
| `scripts/repo_template/` | task 工具链 |
| `tests/repo_template/` | 工具链测试 |
| `docs/tasks/task_template/` | task 文件模板 |
| `docs/reviews/prompts/` | review prompt |
| `docs/spikes/report_template.md` | spike 报告模板 |
| `docs/blueprint/architecture_repo_template.md` | 模板执行架构 |
| `.claude/hooks/merge_guard.py` | merge hook（SRC 无 → 删消费侧） |

噪声忽略：`__pycache__/`、`*.pyc`、`.pytest_cache/`、`.DS_Store`。

---

## 裁定同步

凡可能叠项目定制的路径，**必须**走本节。有 diff → **必须**产出 classification + disposition + rationale，并在预览里给用户看清楚「将怎么改」。

### 裁定范围

| 路径 / 单元 | 粒度 | 典型裁定方式 |
|-------------|------|--------------|
| `.agents/skills/<name>/` | 每个 skill（保护 `repo-template-sync/sync_state.json`） | 整 skill 更新或保留 |
| `.claude/skills/<name>` | 软链 | 随 skill 建/修；不删 consumer_only |
| `.grok/skills/<name>/` | 若存在 | 同 skill |
| MCP 清单 | 按 server 键（`.mcp.json`、`.cursor/mcp.json`、`.vscode/mcp.json` 等实际扫到的） | 按键合并 |
| 宿主 settings 中 mcp/hooks 等片段 | `.claude/settings.json` 等 | 片段合并，禁冲密钥 |
| `AGENTS.md`（及 `CLAUDE.md` 软链目标） | **按段/按表行** | 保留项目首行介绍；合并模板工作流表、目录权责、skill 路由等模板演进 |
| `docs/blueprint/conventions.md` | **按节/按条** | 保留项目追加约定；并入模板新增的命名/流程条 |
| `.gitignore` | **按行/按块** | 保留项目规则；并入模板新增忽略项（去重） |
| 其它两边都存在、且明显属「模板脚手架 + 项目改写」的共享文件 | 按内容 | 同左；不进「永久不写」桶 |

**不在**裁定也不硬同步：纯业务 `README` 项目介绍、`docs/blueprint/{architecture,domain,testing,decisions}.md` 等项目真相文档——除非用户点名。若模板与消费差异巨大且疑似模板脚手架残留，可在预览「范围外差异提示」里提一句，默认不动。

### 分类

| 类 | 条件 |
|----|------|
| `template_only` | 仅 SRC 有 |
| `consumer_only` | 仅消费有 |
| `both_identical` | 等价（忽略噪声） |
| `both_differ` | 都有且不同 → **必读 diff 再裁** |

### disposition（有 diff 必须落其一）

| disposition | 含义 | apply 行为 |
|-------------|------|------------|
| `update_from_template` | 整单元以模板为准 | 覆盖该单元（skill 目录 / 整文件仅当无项目定制） |
| `merge_into_consumer` | **智能合并**：把模板新增/修正并入消费侧，保留消费定制 | agent **编辑**消费文件（补表行、补 ignore 行、补约定条…），**禁止**整文件 rsync 盖掉 |
| `keep_consumer` | 明确保留消费侧 | 不写；rationale 必须说明「为何不用模板」（例：项目业务约定 246 行） |
| `ask_user` | 无法独断 | 不写该单元直至用户逐项答复；预览须写清冲突要点 |
| `skip_identical` | 无实质差异 | 不写 |

**偏见**：

- 不确定 → `ask_user`（优先）或 `keep_consumer`，**绝不**默认整文件 `update_from_template`。
- 模板有清晰增量、消费有清晰定制、且可机械/语义合并 → **优先 `merge_into_consumer`**，不要停在「只看不写」。
- `keep_consumer` 不是偷懒默认；用于「模板侧无值得并入的增量」或「并入会破坏项目语义」。

### 共享文稿裁定要点

**`AGENTS.md`**

- 保留消费侧**项目一句话介绍**（首段/首行）。
- 模板新增/改写的：目录权责表行、开发工作流、skill 路由表、task 状态机等 → 通常 `merge_into_consumer`（按行/段并入）。
- 预览须写明：将补哪些表行/段落；不动哪段项目介绍。

**`docs/blueprint/conventions.md`**

- 统计模板独有 vs 消费独有行/节。
- 模板独有且属通用命名/流程 → `merge_into_consumer`。
- 消费大段项目约定 → `keep_consumer` 那些节；不要整文件覆盖。
- 预览须列出「拟并入的模板条」摘要（可摘关键句）。

**`.gitignore`**

- 模板新增规则且消费没有 → `merge_into_consumer`（追加，去重）。
- 消费独有（如 `node_modules/`、构建产物）→ 保留。
- 禁止用模板 `.gitignore` 整文件覆盖。

**skill**

- 同名且消费 ≈ 旧模板 → `update_from_template`。
- 同名且两边实质改动 → `merge_into_consumer` 或 `ask_user`。
- 仅消费有 → `keep_consumer`，**禁止删**。
- 模板 skill 名（`task-*`、`repo-hygiene`、`repo-cleanup`、`repo-template-sync`）仅启发，同名仍要读 diff。

**MCP / settings**

- 按键合并；禁冲密钥、token、本机路径。
- 模板新 server → 建议并入；消费独有 server → 保留。

**`repo-template-sync`**：可更新 `SKILL.md`；禁覆盖消费 `sync_state.json`。

### 软链

1. apply 后仍存在的 `.agents/skills/<name>/` → 建/修 `.claude/skills/<name>` → `../../.agents/skills/<name>`。
2. 不因模板没有而删 consumer_only skill 或其软链。
3. 仅用户确认删除的 skill 才清对应悬空链。
4. 非本机制管理链接：报告，不碰。
5. `CLAUDE.md` → `AGENTS.md` 损坏：报告，确认后修。

---

## 解析模板工作树

`SRC` = 模板根，`T_HEAD` = `git rev-parse HEAD`。

### `kind=path`

1. 目录存在且含 `scripts/repo_template/task.py`。
2. dirty 检查硬同步 + 裁定相关路径；有输出 → `SRC_DIRTY=true`。
3. dirty 时 apply 可写已确认项，**禁止**推进 `last_synced_commit`。

### `kind=url`

缓存 `.scratch/repo_template_sync_src/`；clone/fetch + reset tip；浅克隆缺旧 commit → 无基线，不失败退出。

### 同一性

`realpath` 相同 → 拒绝。

## 步骤

### 0–2. 定位 / 模式 / 读 state

同前：`CONSUMER`、`STATE`；缺源要求 `init`。

### 3. `status`

源、last、T_HEAD、dirty；模板更新摘要（对象缺失则无基线）；硬同步/裁定 diverged 计数。零写盘。

### 4. 计算变更

#### 4a. 硬同步清单

树对树：写/删列表。

#### 4b. 裁定同步清单（**禁止省略有 diff 的单元**）

1. 枚举裁定范围全部单元（含 `AGENTS.md`、`conventions.md`、`.gitignore`、skills、MCP…）。
2. 对 `both_differ` / `template_only`：**读具体 diff**（至少知道模板独有/消费独有方向），给 disposition + rationale。
3. 对 `merge_into_consumer`：预览中写清**拟改操作**（补哪几行/哪一节/哪几个 ignore 模式），不得只写「有 252 行差异」。
4. 汇总建议写入 / 合并 / 保留 / 待决。

#### 4c. 模板 commit 摘要（信息）

可缺；禁止代替 4a/4b。

预览模板：

```markdown
## repo-template-sync 预览

模式：dry-run | apply（待确认）
…

### 硬同步 — 将写入 / 删除
…

### 裁定同步 — 逐项（有 diff 必出现）
| 单元 | 分类 | disposition | rationale / 拟操作 |
|------|------|--------------|---------------------|
| AGENTS.md | both_differ | merge_into_consumer | 保留首行项目介绍；并入 skill 路由 2 行（repo-template-sync、task-from-pending 职责） |
| docs/blueprint/conventions.md | both_differ | merge_into_consumer | 并入模板新增 N 条：…；保留消费项目约定章节 |
| .gitignore | both_differ | merge_into_consumer | 追加模板独有：…；保留消费 node_modules 等 |
| .agents/skills/my-proj | consumer_only | keep_consumer | 项目自有 skill |
| .mcp.json#foo | both_differ | keep_consumer | 含项目密钥占位 |

### 软链
…

### state 推进预期
…

下一步：确认后 apply。`ask_user` 须逐项答复。
```

**禁止**再输出名为「只 diff（未写入）」且暗示「这些永远不动」的分区。

### 5. `apply`

1. 用户确认本轮预览。`ask_user` 未答复的单元不写。
2. **硬同步**：目录 `rsync -a --delete` + 噪声 exclude；单文件 `sync_file`（有则拷、无则删）。
3. **裁定同步**：
   - `update_from_template`：按单元 rsync/拷贝（skills 不对整树 `--delete`；本 skill 排除 `sync_state.json`）。
   - `merge_into_consumer`：**编辑**消费文件完成合并（补行/补节/去重），禁止整文件盖掉消费定制。
   - `keep_consumer` / 未确认 `ask_user`：跳过。
4. 软链建/修。
5. `pytest tests/repo_template/ -q`；失败不推进 state。
6. 干净 SRC + 验证通过（或用户跳过测试）→ 更新 `last_synced_*`。汇报列出未处理裁定项。
7. 不自动 commit。

## 汇报

```markdown
## repo-template-sync 结果

硬同步：…
裁定同步：
- 已合并/更新：…
- 明确保留（keep_consumer）：… — 各附 rationale
- 待决未写：…
软链：…
验证 / state：…
默认未 commit。
```

## 完成条件

- dry-run：对**每一个**有 diff 的裁定单元给出 disposition + 可执行 rationale；无「只 diff 未处理」悬空桶。
- apply：硬同步完成；裁定按确认的 disposition 写入（含 `merge_into_consumer` 的实质编辑）；consumer_only 与 keep_consumer 未被覆盖/删除；`sync_state.json` 未丢；last 推进规则满足。
