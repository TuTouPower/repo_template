---
name: repo-template-sync
description: none
disable-model-invocation: true
---

# repo-template-sync

把 **repo_template** 的工具链与模板资产同步到**当前消费项目**。仅用户显式请求时运行（如「同步模板」）。禁止在模板仓本身当「推送源」跑。

**机械化对齐一律由脚本 `scripts/repo_template/repo_sync.py` 执行**——能用脚本做的不手动拼命令。agent 只负责：裁定非机械化单元（共享文稿语义合并）、审查预览、执行审批门禁、管理站立指令登记。

状态文件（消费项目本地运行态；**模板仓不入库**，由消费侧首次接入时生成后入库）：

```text
.agents/skills/repo-template-sync/sync_state.json
```

## 入口

本 skill **仅用户显式请求同步时运行**（如「同步模板」「repo-template-sync」）。用户不传参数；agent 全自动按「流程」执行。`init` / `status` / `plan` / `apply` 是**流程内部阶段**（见「流程」），不是用户可敲的命令。

同步过程中的用户站立指令（如「.env 不要 ignore」）由 `repo_sync.py prompt add` 登记进 `user_prompts`（见「state 与站立指令」），后续每轮同步持续生效。

## 边界（硬）

- **只在消费项目跑**。`repo_sync.py apply` 会做同一性检查：当前仓库根与 `template_source` 解析后为同一路径 → 拒绝并停止。
- **禁止自动 commit**。apply 写盘与 state 更新完成后，必须进入「审批门禁」：展示变更摘要，**询问用户是否审批通过**；仅当用户明确表示审批通过（如「审批通过」「approve」「可以 commit」）后才可 `git add` + `git commit`。用户未表态、只说「好的/继续/知道了」、或明确拒绝 → **不 commit**。
- **不碰业务与项目状态**：`src/`、`assets/`、`schemas/`、`config/`、非 `repo_template` 的 `scripts/`、`docs/tasks/{tid}_*`、`docs/archive/`、`docs/pending/{todo,parked}/`、`docs/findings/dNNN_*`、`docs/specs*`、`docs/handoff.md`、`docs/runtime/`、`docs/reviews/review_*/`、`docs/spikes/{sid}_*`、`docs_repo/`、`README.md`（业务介绍）。硬同步只动脚本声明的清单路径，不越界。
- **不静默硬盖可定制的共享资产**：`AGENTS.md`、`conventions.md`、`.gitignore`、MCP、宿主 settings 等必须走「裁定」，逐项裁定。模板资产（工具链、模板侧存在的 skill）归「强制覆盖」。
- **合并共享文稿禁止格式化**：合并 `AGENTS.md`、`conventions.md` 等共享文稿时，禁止运行任何 Markdown 格式化 / 表格对齐工具（prettier 等），保持模板原格式（紧凑表格，单元格不做空格 pad 填充）。
- **有差异就必须处理并报告**。不存在「只展示 diff、默认可永久不处理」的桶：模板资产 → 强制覆盖；裁定单元 → 要么写入（`update` / `merge`）、要么明确 `keep_consumer`（写清为什么保留）、要么 `ask_user`。禁止用「只 diff」当借口跳过该更新的模板演进。
- 消费侧 `sync_state.json` **永不**被模板侧文件覆盖或删除（模板本就不带该文件；`repo_sync.py` 的 skill 覆盖天然排除它）。
- **每次同步全量对比，不信任上一次同步**：diff 一律基于「模板当前工作树 ↔ 消费项目当前工作树」逐路径重新对比；`last_synced_commit` / `last_synced_at` 仅作审计记录，**绝不**作为对比基线、跳过依据或增量范围。

## 脚本能力映射

`repo_sync.py` 在消费项目 `scripts/repo_template/` 下（硬同步清单路径，随模板演进同步）。子命令见 `repo_sync.py --help`。

| 能力 | 命令 | 说明 |
| --- | --- | --- |
| 初始化 template_source | `repo_sync.py init --source <path\|url>` | 首次接入；仅字段级写 state |
| 状态与差异 | `repo_sync.py status` | 源、HEAD、dirty、user_prompts、差异摘要（零写盘） |
| 差异预览 | `repo_sync.py plan` | 输出 markdown 预览表（强制覆盖/裁定/软链/state 预期），零写盘 |
| 硬同步强制覆盖 | `repo_sync.py apply` | 硬同步清单树对树（含多余删除）；噪声忽略 |
| skill 整目录覆盖 | `repo_sync.py apply` | 含 front matter；保护 `sync_state.json`；不做整树 `--delete` |
| 软链建/修 | `repo_sync.py apply` / `link-skills` | `.claude/skills/<name>` → `.agents/skills/<name>`；非本机制链接报告不碰 |
| .gitignore / MCP 机械合并 | `repo_sync.py apply` | 追加去重、按键合并、禁冲密钥；遵从 `user_prompts` 拦截 |
| 裁定单元写盘 | `repo_sync.py apply --decision U:D` | `update` / `keep`；`merge` 提示后由 agent 手动编辑 |
| state 字段级更新 | `apply` / `prompt` 内部 | 原子写盘，未知键保留（脚本保证纪律） |
| user_prompts 管理 | `repo_sync.py prompt add\|revoke\|list` | 同 tag 自动 supersede（后出优先） |

agent 不手动用 rsync / jq / sed 重写脚本已覆盖的机械化路径；脚本不覆盖的部分（AGENTS.md 语义合并、settings 片段合并、悬空链清理）由 agent 处理。

## 流程

### 0. 初始化：脚本 + 模板源 + state

`CONSUMER` = 当前消费项目根。

1. **脚本存在性**：`scripts/repo_template/repo_sync.py` 缺失时，先从模板仓复制该脚本到消费项目（首次接入引导；完整工具链随后由 apply 硬同步补齐）。
2. **首次接入（无 state）**：`repo_sync.py init --source <path|url>`。推断模板源：用户给定 → 常见本机路径 → 不臆造。本机含 `scripts/repo_template/task.py` → `kind=path`；否则 `url`。
3. 已有 state → 不重跑 init；仅当 `template_source` 缺失时用 `init` 字段级补。
4. 缺源或缺 state 且无法初始化 → **停止并报告**。

### 1. 现状（status）

`repo_sync.py status`：源、T_HEAD、dirty；`last_synced_commit` 仅作审计展示，**不参与**落后判断；**完整 `user_prompts` 列表**；全量差异摘要（硬同步/裁定 diverged 计数基于当前两棵工作树对比）。默认零写盘。

### 2. 计算变更（plan，零写盘）

`repo_sync.py plan` 输出完整预览，覆盖：

- **站立指令 user_prompts**（裁定优先）：未 revoked 条全列（空则「无」）。
- **强制覆盖**：硬同步清单（write/delete/same）、模板侧 skill 强制覆盖、消费侧独有 skill 保留。
- **裁定同步**：`AGENTS.md`、`conventions.md`、`.gitignore` 的分类（`template_only` / `consumer_only` / `both_identical` / `both_differ`）与建议 disposition。**有 diff 的单元必出现**。
- **软链**状态与需修项。
- **state 推进预期**（SRC dirty → 不推进 `last_synced_commit`）。

预览展示后，agent 把 plan 输出整理为最终预览呈现给用户（见「汇报」）。

### 3. 裁定（非机械化，agent 判断）

对 `AGENTS.md` / `conventions.md` 等语义合并单元，agent 依据 plan 的分类**读 diff 后裁定**：

| disposition | 含义 | apply 行为 |
| --- | --- | --- |
| `update_from_template` | 整单元以模板为准 | `--decision U:update`，脚本整文件覆盖 |
| `merge_into_consumer` | **智能合并**：模板增量并入消费侧，保留消费定制 | agent **手动编辑**消费文件（补表行、补约定条…）；apply 时传 `--decision U:merge` 提示脚本不整文件覆盖 |
| `keep_consumer` | 明确保留消费侧 | `--decision U:keep`；rationale 说明「为何不用模板」 |
| `ask_user` | 无法独断 | 不写该单元；预览写清冲突要点，等用户逐项答复 |

**偏见**（优先级从高到低）：

1. **`user_prompts` 站立指令** — 与指令冲突的模板增量一律不并入；rationale 写「遵循 user_prompts#N」。
2. 不确定 → `ask_user`（优先）或 `keep_consumer`，**绝不**默认整文件 `update_from_template`。
3. 模板有清晰增量、消费有清晰定制、且可机械/语义合并 → 优先 `merge_into_consumer`。
4. `keep_consumer` 用于「模板侧无值得并入的增量」或「并入会破坏项目语义 / 违反 prompt」。

**各资产裁定要点**

- **`AGENTS.md`**：保留消费侧项目一句话介绍（首段/首行）；模板新增的目录权责表行、开发工作流、skill 路由表 → 通常 `merge`。预览写明补哪些表行/段落、不动哪段项目介绍。
- **`docs/blueprint/conventions.md`**：统计模板独有 vs 消费独有行/节；模板独有且属通用命名/流程 → `merge`；消费大段项目约定 → `keep` 那些节，不要整文件覆盖。
- **`.gitignore`**：脚本自动 `merge`（追加模板独有、去重、保留消费独有）；**先套 `user_prompts`**：指令禁止 ignore 某路径 → 模板对应规则不并入、消费侧误有该规则会被脚本删除。agent 核对 plan 中的 added/removed 是否合规。
- **MCP / settings**：`.mcp.json` 等由脚本按键合并（禁冲密钥）。宿主 settings 片段（`.claude/settings.json` 等）脚本不自动合并 → agent 按键合并，禁冲密钥、token、本机路径。

### 4. `apply`（写盘）

1. 用户确认本轮预览。`ask_user` 未答复的单元不写。

2. 组装裁定决策，调用：

    ```bash
    python3 scripts/repo_template/repo_sync.py apply \
      --decision AGENTS.md:update \
      --decision conventions.md:merge \
      # ... 仅已确认的单元；未确认不传
    ```

    apply 执行：硬同步树对树 → skill 整目录覆盖 → 软链建/修 → .gitignore/MCP 机械合并 → 裁定单元按决策写盘（`merge` 由 agent 先前手动编辑，脚本跳过）→ `pytest tests/repo_template/ -q`（`--skip-tests` 可跳过）→ 字段级更新 state（干净 SRC + 测试通过才推进 `last_synced_commit`）→ 输出**改动路径清单**。

3. 测试失败 → 不推进 state；agent 汇报并停，不 commit。

4. apply 后工作区有未提交变更；**尚未 commit**。

### 5. 审批门禁（apply 之后，强制）

apply 写盘结束后**必须**停下来问用户，不得默认提交。

1. 输出「结果汇报」+ 变更摘要：

    ```bash
    git status --short
    git diff --stat
    ```

2. 用 apply 输出的**改动路径清单**列出**拟提交路径**（仅本轮同步触碰的路径，含变更的 `sync_state.json`）。

3. **明确询问**：

    > 同步已写入工作区（尚未 commit）。请审批：回复「审批通过」后才会提交；拒绝或其它表述则保持未提交。

4. **等待用户本轮明确答复**：

    - **通过**：「审批通过」/「approve」/「可以 commit」/「同意提交」等无歧义同意 → 步骤 5。
    - **不通过**：拒绝、再改、沉默、仅「ok/好/收到」→ **不 commit**。

5. 审批通过后——**只点名 add 清单内路径**（**禁止** `git add -A` / `git add .`）：

    ```bash
    git add -- scripts/repo_template \
      tests/repo_template \
      .agents/skills/task-run \
      .agents/skills/repo-template-sync/SKILL.md \
      .agents/skills/repo-template-sync/sync_state.json \
      # …apply 输出的每一改动路径
    git commit -m "chore: sync repo_template @ <T_HEAD 短 SHA>"
    ```

    - 路径以 apply 实际改动清单为准，上表仅为形状示例。
    - **不** `git push`，除非用户另说。
    - 无关脏文件不得进 stage。

6. 无任何可提交 diff → 说明无需 commit。

## state 与站立指令

- state 骨架与字段见 `sync_state.json`；字段级更新纪律由 `repo_sync.py` 内部保证（读盘 → 改指定键 → 写回，未知键保留），**agent 禁止手工编辑 state 文件**。
- 登记站立指令：`repo_sync.py prompt add --text "..." --tags a,b`；同 tag 旧条自动 `revoked`（后出优先）。作废：`prompt revoke --id N`。查看：`prompt list`。
- 一次性「这次先别动」且用户说「不用记住」→ 不写入；未说明则默认记住。
- 每轮同步**先读 `prompt list`** 未 revoked 条再裁定；未撤销的 `user_prompts` **优先于**模板默认；rationale 引用序号或 `text`。
- `link-skills`：软链校验/修复。悬空链清理仅在用户确认删除对应 skill 后手动处理；非本机制管理链接由脚本报告、agent 不碰。

## 汇报

```markdown
## repo-template-sync 结果

强制覆盖：
- 硬同步：…
- skill：…
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

- **计算变更**：`prompt list` 未 revoked 条已加载并展示；plan 强制覆盖全量列出并标注处置；每个有 diff 的裁定单元有 disposition + rationale；不违反 prompt；diff 全量基于当前两棵工作树，不依赖 `last_synced_*` 作基线。
- **写盘**：机械化部分全部由 `repo_sync.py apply` 完成（不手动拼 rsync/jq）；裁定遵守 prompt；state 仅字段级更新（脚本保证）；已展示待审批清单并询问。
- **commit**：仅「审批通过」后、且**点名 add** apply 输出的改动路径；否则不提交。
