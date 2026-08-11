---
name: repo-template-sync
description: none
disable-model-invocation: true
---

# repo-template-sync

把 **repo_template** 的工具链与模板资产同步到**当前消费项目**。仅用户显式调用（`/repo-template-sync`）。禁止在模板仓本身当「推送源」跑。

状态文件（消费项目本地运行态；**模板仓不入库**，由消费侧 `init` 生成后入库）：

```text
.agents/skills/repo-template-sync/sync_state.json
```

## 输入

|用户输入|行为|
|------|------|
|无参数 / `dry-run`|只读：解析源、读 `user_prompts`、列 diff + **完整裁定表**；**不写同步文件**（见下：仅允许显式登记 prompt）|
|`apply`|先出与 dry-run 相同预览；**须用户再确认**才按 disposition 写入；回写 state（含本轮新 prompt）；**写完后必须走审批门禁**，用户明确「审批通过」后才可 commit|
|`init`|只写/修正 `template_source`（可建 state 骨架），不同步文件|
|`status`|只读：源、上次 commit、模板 HEAD、`user_prompts`、是否落后、差异摘要|
|`prompt add "…"` / 用户本轮给出站立指令|**追加**到 `user_prompts`（可在任意模式；须原样保留用户措辞）|

## 边界（硬）

- **只在消费项目跑**。当前仓库根与 `template_source` 解析后为同一路径 → 立即停止并说明。
- **禁止自动 commit**。apply 写盘与 state 更新完成后，必须进入「审批门禁」：展示变更摘要，**询问用户是否审批通过**；仅当用户明确表示审批通过（如「审批通过」「approve」「可以 commit」）后才可 `git add` + `git commit`。用户未表态、只说「好的/继续/知道了」、或明确拒绝 → **不 commit**。
- **不碰业务与项目状态**：`src/`、`assets/`、`schemas/`、`config/`、非 `repo_template` 的 `scripts/`、`docs/tasks/{tid}_*`、`docs/archive/`、`docs/pending/{todo,parked}/`、`docs/findings/dNNN_*`、`docs/specs*`、`docs/handoff.md`、`docs/runtime/`、`docs/reviews/review_*/`、`docs/spikes/{sid}_*`、`docs_repo/`、`README.md`（业务介绍）。
- **禁止整树/整文件静默硬盖** skill、MCP、宿主配置、以及常被项目改写的共享文稿（`AGENTS.md`、`conventions.md`、`.gitignore` 等）——一律走「裁定同步」。
- **合并共享文稿禁止格式化**：合并 `AGENTS.md`、`conventions.md` 等共享文稿时，禁止运行任何 Markdown 格式化 / 表格对齐工具（prettier 等），保持模板原格式（紧凑表格，单元格不做空格 pad 填充）。
- **有差异就必须裁定并报告**。不存在「只展示 diff、默认可永久不处理」的桶；差异要么写入（`update` / `merge`）、要么明确 `keep_consumer`（写清为什么保留）、要么 `ask_user`。禁止用「只 diff」当借口跳过该更新的模板演进。
- 消费侧 `sync_state.json` **永不**被模板侧文件覆盖或删除（模板本就不带该文件；更新 skill 时 rsync 须 `--exclude 'repo-template-sync/sync_state.json'`）。
- **`status` / `dry-run` 默认同步零写盘**：不 rsync、不改业务文件；缺源或缺 state → 报告并要求 `init`。例外：登记站立指令时仅允许**字段级**改 state（见「state 写入纪律」）。

## 配置：`sync_state.json`

消费项目本地文件。骨架仅作文档；**init 生成**，不要从模板仓复制。

```json
{
  "template_source": {
    "kind": "path",
    "value": "/absolute/path/to/repo_template"
  },
  "last_synced_commit": "full-or-abbrev-sha-or-null",
  "last_synced_at": "2026-08-08T12:00:00+08:00",
  "user_prompts": [
    {
      "at": "2026-08-08T15:30:00+08:00",
      "text": ".env 在消费项目里不要 ignore",
      "tags": [".gitignore", ".env"],
      "revoked": false
    }
  ]
}
```

|字段|含义|
|------|------|
|`template_source.kind`|`path` 或 `url`|
|`template_source.value`|绝对路径或 git remote URL|
|`last_synced_commit`|上次成功 `apply` 后模板 HEAD（完整 SHA）；仅当写入与该 commit 工作树一致时才可推进|
|`last_synced_at`|ISO 8601（UTC+8）|
|`user_prompts`|站立指令历史。每轮同步**必须先读完**再裁定（忽略 `revoked: true`）|

### `user_prompts[]` 条目

|字段|必填|含义|
|------|------|------|
|`at`|是|登记时间，ISO 8601（UTC+8）|
|`text`|是|用户原话或忠实摘要（优先原话）|
|`tags`|否|短标签，如 `[".gitignore",".env"]`；agent 可补|
|`revoked`|否|`true` = 已作废，裁定忽略；条目保留备查|

### state 写入纪律（硬；防自我更新窗口丢字段）

**禁止**用编辑器/Write 按「内存里的 schema」整文件重建 `sync_state.json`。旧会话若 schema 不全，整文件写会抹掉未知字段（如 `user_prompts`）——这是已发生事故的根因。

**只准字段级更新**（读盘 → 改指定键 → 写回；未知键原样保留）：

```bash
# 推进 last（示例；路径按 CONSUMER）
STATE=".agents/skills/repo-template-sync/sync_state.json"
tmp="$(mktemp)"
jq --arg c "$T_HEAD" --arg t "$(date -Iseconds)" \
  '.last_synced_commit=$c | .last_synced_at=$t' "$STATE" > "$tmp" && mv "$tmp" "$STATE"

# 写 template_source（init）
jq --arg k "path" --arg v "$SRC" \
  '.template_source={kind:$k, value:$v}' "$STATE" > "$tmp" && mv "$tmp" "$STATE"

# append 一条 prompt（新条对象用 --argjson）
jq --argjson p "$PROMPT_JSON" '.user_prompts += [$p]' "$STATE" > "$tmp" && mv "$tmp" "$STATE"
```

无 `jq` 时用等价 Python：`json.load` → 只改目标键 / `list.append` → `json.dump`；**不得** `state = {只含已知键}` 再 dump。

- 文件不存在（仅 `init`）：可**一次**写出完整骨架，此后永远字段级。
- 更新本 skill 的 `SKILL.md` 与写 state **解耦**：rsync skill 正文时必须 exclude `sync_state.json`。

### `user_prompts` 读写与时效

1. **每次** `status` / `dry-run` / `apply` 加载；预览置顶列出**未 revoked** 条（空则写「无」）。
2. 裁定时未撤销的 `user_prompts` **优先于**模板默认；rationale 引用序号或 `text`。
3. 登记新站立指令：
   - append 新条；**不删**旧条正文。
   - **同 tag 覆盖**：若新条 `tags` 与某条未撤销旧条的 `tags` **有交集**（规范化小写比较），将旧条标 `"revoked": true`（字段级：改该元素的 `revoked`），再 append 新条。后出优先。无 tags 则不做自动 supersede，靠语义 + 用户显式 revoke。
   - 一次性「这次先别动」且用户说「不用记住」→ 不写入；未说明则默认记住。
4. 用户显式作废：将该条 `revoked: true`（字段级），不整表重写。
5. 裁定只用 `revoked != true` 的条目；预览可附「已 supersede/revoked」缩略计数。

缺源：`init`；其它模式停。

### `init`

1. 无 state → **仅此时**写完整骨架（`last_synced_*=null`，`user_prompts=[]`，`template_source` 待填）。
2. 推断 path/url（用户给定 → 常见本机路径 → 不臆造）。
3. 本机含 `scripts/repo_template/task.py` → `kind=path`；否则 url。
4. 有 state → **仅**字段级写 `template_source`；不动 `user_prompts` / `last_synced_*`。

## 两类同步

|类型|范围|策略|
|------|------|------|
|**硬同步**|纯模板工具链与无项目定制的文档模板|树对树：以 SRC 为准写入/删除；预览确认后 apply|
|**裁定同步**|skill / MCP / 宿主配置 / 共享文稿（AGENTS、conventions、gitignore 等）|**禁止**整树 `--delete` 与未裁定整文件盖；agent **读 diff、逐项裁定、写 rationale**；该合就合、该留就留；拿不准 → `ask_user` 并**明示用户**|

写入清单权威：硬同步 = 树对树；裁定 = 逐项 disposition。commit range 仅信息摘要。

---

## 硬同步路径

|模板侧|消费侧|动作|
|------|------|------|
|存在|无 / 内容不同|写入|
|存在|相同|不动|
|不存在|存在|**删除**（仅下列路径）|

|相对路径|说明|
|------|------|
|`scripts/repo_template/`|task 工具链|
|`tests/repo_template/`|工具链测试|
|`docs/tasks/task_template/`|task 文件模板|
|`docs/reviews/prompts/`|review prompt|
|`docs/spikes/report_template.md`|spike 报告模板|
|`docs/blueprint/architecture_repo_template.md`|模板执行架构|
|`.claude/hooks/merge_guard.py`|merge hook（SRC 无 → 删消费侧）|

噪声忽略：`__pycache__/`、`*.pyc`、`.pytest_cache/`、`.DS_Store`。

---

## 裁定同步

凡可能叠项目定制的路径，**必须**走本节。有 diff → **必须**产出 classification + disposition + rationale，并在预览里给用户看清楚「将怎么改」。

### 裁定范围

|路径 / 单元|粒度|典型裁定方式|
|------|------|------|
|`.agents/skills/<name>/`|每个 skill（保护 `repo-template-sync/sync_state.json`）|整 skill 更新或保留|
|`.claude/skills/<name>`|软链|随 skill 建/修；不删 consumer_only|
|`.grok/skills/<name>/`|若存在|同 skill|
|MCP 清单|按 server 键（`.mcp.json`、`.cursor/mcp.json`、`.vscode/mcp.json` 等实际扫到的）|按键合并|
|宿主 settings 中 mcp/hooks 等片段|`.claude/settings.json` 等|片段合并，禁冲密钥|
|`AGENTS.md`（及 `CLAUDE.md` 软链目标）|**按段/按表行**|保留项目首行介绍；合并模板工作流表、目录权责、skill 路由等模板演进|
|`docs/blueprint/conventions.md`|**按节/按条**|保留项目追加约定；并入模板新增的命名/流程条|
|`.gitignore`|**按行/按块**|保留项目规则；并入模板新增忽略项（去重）|
|其它两边都存在、且明显属「模板脚手架 + 项目改写」的共享文件|按内容|同左；不进「永久不写」桶|

**不在**裁定也不硬同步：纯业务 `README` 项目介绍、`docs/blueprint/{architecture,domain,testing,decisions}.md` 等项目真相文档——除非用户点名。若模板与消费差异巨大且疑似模板脚手架残留，可在预览「范围外差异提示」里提一句，默认不动。

### 分类

|类|条件|
|------|------|
|`template_only`|仅 SRC 有|
|`consumer_only`|仅消费有|
|`both_identical`|等价（忽略噪声）|
|`both_differ`|都有且不同 → **必读 diff 再裁**|

### disposition（有 diff 必须落其一）

|disposition|含义|apply 行为|
|------|------|------|
|`update_from_template`|整单元以模板为准|覆盖该单元（skill 目录 / 整文件仅当无项目定制）|
|`merge_into_consumer`|**智能合并**：把模板新增/修正并入消费侧，保留消费定制|agent **编辑**消费文件（补表行、补 ignore 行、补约定条…），**禁止**整文件 rsync 盖掉|
|`keep_consumer`|明确保留消费侧|不写；rationale 必须说明「为何不用模板」（例：项目业务约定 246 行）|
|`ask_user`|无法独断|不写该单元直至用户逐项答复；预览须写清冲突要点|
|`skip_identical`|无实质差异|不写|

**偏见**（优先级从高到低）：

1. **`user_prompts` 站立指令**（见 state）— 与指令冲突的模板增量一律不并入；rationale 写「遵循 user_prompts#N」。
2. 不确定 → `ask_user`（优先）或 `keep_consumer`，**绝不**默认整文件 `update_from_template`。
3. 模板有清晰增量、消费有清晰定制、且可机械/语义合并 → **优先 `merge_into_consumer`**。
4. `keep_consumer` 用于「模板侧无值得并入的增量」或「并入会破坏项目语义 / 违反 prompt」。

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
- **先套 `user_prompts`**：若指令禁止 ignore 某路径（如 `.env`），则模板中对应规则**不得**并入；消费侧若误有该规则且指令要求跟踪 → 在预览中提议删除该 ignore 行（`merge_into_consumer` 或明确操作），不得无视 prompt。

**skill**

- 同名且消费 ≈ 旧模板 → `update_from_template`。
- 同名且两边实质改动 → `merge_into_consumer` 或 `ask_user`。
- 仅消费有 → `keep_consumer`，**禁止删**。
- 模板 skill 名（`task-*`、`repo-hygiene`、`repo-cleanup`、`repo-template-sync`）仅启发，同名仍要读 diff。

**MCP / settings**

- 按键合并；禁冲密钥、token、本机路径。
- 模板新 server → 建议并入；消费独有 server → 保留。

**`repo-template-sync`**：可更新 `SKILL.md`；**永不** rsync/覆盖/删除消费侧 `sync_state.json`（模板侧也不应存在该文件）。

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

源、last、T_HEAD、dirty；**完整 `user_prompts` 列表**；模板更新摘要（对象缺失则无基线）；硬同步/裁定 diverged 计数。默认同步零写盘。

### 4. 计算变更

**先加载 `user_prompts`，再做 4a/4b。** 预览置顶「生效的站立指令」。

#### 4a. 硬同步清单

树对树：写/删列表。硬同步一般不直接改 `.gitignore`；若硬同步间接冲突极少见——仍以 prompt 为准在裁定段处理共享文件。

#### 4b. 裁定同步清单（**禁止省略有 diff 的单元**）

1. 枚举裁定范围全部单元（含 `AGENTS.md`、`conventions.md`、`.gitignore`、skills、MCP…）。
2. 对 `both_differ` / `template_only`：**读具体 diff**，给 disposition + rationale；**每条相关 `user_prompts` 必须体现在 rationale 或拟操作里**（引用 `user_prompts[i].text` 或序号）。
3. 对 `merge_into_consumer`：预览中写清**拟改操作**（补哪几行/哪一节/哪几个 ignore 模式），不得只写「有 252 行差异」；若某模板行被 prompt 禁止并入，单列「因 prompt 跳过：…」。
4. 汇总建议写入 / 合并 / 保留 / 待决 / **本轮拟新登记的 prompt**。

#### 4c. 模板 commit 摘要（信息）

可缺；禁止代替 4a/4b。

预览模板：

```markdown
## repo-template-sync 预览

模式：dry-run | apply（待确认）
…

### 站立指令 user_prompts（裁定优先）
|#|at|text|
|------|------|------|
|0|2026-08-08T15:30:00+08:00|.env 在消费项目里不要 ignore|

（无则写「无」；本轮将新登记：…）

### 硬同步 — 将写入 / 删除
…

### 裁定同步 — 逐项（有 diff 必出现）
|单元|分类|disposition|rationale / 拟操作|
|------|------|------|------|
|AGENTS.md|both_differ|merge_into_consumer|保留首行；并入 skill 路由 2 行|
|.gitignore|both_differ|merge_into_consumer|追加模板独有（**跳过 .env 相关，遵循 prompt#0**）；保留消费 node_modules|
|.agents/skills/my-proj|consumer_only|keep_consumer|项目自有 skill|

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
6. 干净 SRC + 验证通过（或用户跳过测试）→ **字段级**更新 `last_synced_commit` / `last_synced_at`；本轮新 prompt **字段级** supersede（同 tag 旧条 `revoked`）+ append。**禁止**整文件重建 state。
7. **进入审批门禁（强制，不可跳过）** — 见下节。工作区可有未提交变更；**尚未 commit**。

### 6. 审批门禁（apply 之后）

apply 写盘结束后**必须**停下来问用户，不得默认提交。

1. 输出「结果汇报」+ 变更摘要：

   ```bash
   git -C "$CONSUMER" status --short
   git -C "$CONSUMER" diff --stat
   ```

2. 列出**拟提交路径清单**（仅本轮同步触碰的路径，含变更的 `sync_state.json` 若有）。

3. **明确询问**：

   > 同步已写入工作区（尚未 commit）。请审批：回复「审批通过」后才会提交；拒绝或其它表述则保持未提交。

4. **等待用户本轮明确答复**：
   - **通过**：「审批通过」/「approve」/「可以 commit」/「同意提交」等无歧义同意 → 步骤 5。
   - **不通过**：拒绝、再改、沉默、仅「ok/好/收到」→ **不 commit**。
5. 审批通过后——**只点名 add 清单内路径**（**禁止** `git add -A` / `git add .`）：

   ```bash
   git -C "$CONSUMER" add -- \
     scripts/repo_template \
     tests/repo_template \
     .agents/skills/task-run \
     .agents/skills/repo-template-sync/SKILL.md \
     .agents/skills/repo-template-sync/sync_state.json \
     # …预览/status 中本轮实际改动的每一路径
   git -C "$CONSUMER" commit -m "chore: sync repo_template @ <T_HEAD 短 SHA>"
   ```

   - 路径以本轮真实 diff 为准，上表仅为形状示例。
   - **不** `git push`，除非用户另说。
   - 无关脏文件不得进 stage。

6. 无任何可提交 diff → 说明无需 commit。

## 汇报

```markdown
## repo-template-sync 结果

硬同步：…
裁定同步：
- 已合并/更新：…（注明遵循了哪些 prompt）
- 因 prompt 跳过的模板增量：…
- 明确保留（keep_consumer）：…
- 待决未写：…
user_prompts：历史 N 条；本轮新增：…
软链：…
验证 / state：…

### 待审批（commit 门禁）
拟提交文件：
- …
diff stat：…
请回复「审批通过」以 commit；否则保持工作区未提交。
```

审批通过并 commit 后补一行：`commit: <sha> <subject>`。未批准：`commit: 未执行（等待审批 / 用户拒绝）`。

## 完成条件

- dry-run：加载并展示未撤销 `user_prompts`；每个有 diff 的裁定单元有 disposition + rationale；不违反 prompt。
- apply：硬同步完成；裁定遵守 prompt；state **仅字段级**更新；未知字段仍在；本轮 prompt 已 append 且同 tag 旧条已 revoked；已展示待审批清单并询问。
- commit：仅「审批通过」后、且**点名 add** 本轮路径；否则不提交。

