---
name: task-work
description: none
---

# task-work

在给定 worktree 内以 exact identity 执行单个 task，止于一个执行 commit。本 skill 只覆盖 worktree 内实施：主仓 attempt 控制面命令（reserve/terminal/report/cleanup-worktree/integrate）与分支合并由 `task-run` 队列循环承担。状态机、目录权责见 `AGENTS.md`；门禁数字见本 skill。

## 执行已获批准

触发本 skill 表示批准把该 task 执行至执行 commit 完成。禁止进入 plan mode（`EnterPlanMode` / `ExitPlanMode`），禁止开跑前重述计划征求同意，禁止把 spec 已写明内容再问一遍。直接从 Step 1 开始。

## 实施边界

本 skill 全程只在已创建并登记的 task worktree 内进行。`start`、`attempt reserve/terminal/report`、`cleanup-worktree`、`integrate`、`integrate-chain` 都属于 `task-run` 队列循环，本 skill 不调用。

禁止：写主仓 attempt 控制面、合并任何分支、`task.py list --rebuild`、`git push`、删除分支、清理自己的 worktree、修改其他 task 的文件、询问是否合并主干。本 skill 入库的机器执行证据是 task 分支中的 `handoff.json`。

## 输入

三个值均必填：`tid=tNNN`、非 bool 的正整数 `attempt`、非空字符串 `execution_id`。task 须为 `backlog` / `active`；worktree 必须已由 `task-run` 创建，identity 必须来自 `task.py attempt reserve` 并与本次执行完全一致。`blocked` 须用户先放行或改判。

## 单 task 流程

门禁默认：`max_verify_round = 5`（黑盒）；`max_review_round = 5`（审阅）。`{doctor_cmd}` / `{test_cmd}` / `{blackbox_verify}` 见 `docs/blueprint/testing.md`。

```mermaid
flowchart TD
    S1["1 开干+前置"] --> S2["2 红"]
    S2 --> S3["3 绿"]
    S3 --> S4["4 黑盒"]
    S4 --> B1{"黑盒通过?"}
    B1 -->|否且未满轮| S3
    B1 -->|否且满轮| BLK["blocked"]
    B1 -->|是| S5["5 审阅"]
    S5 --> D{"6 overall?"}
    D -->|PASS| S7["7 收尾+执行 commit"]
    D -->|FAIL 未满轮| W["写处置表"]
    W --> C{"改了代码/测试?"}
    C -->|是| S3
    C -->|否| DOC["改必要文档"] --> S5
    D -->|FAIL 满轮| W2["写处置表"] --> BLK
    S7 --> DONE["交出 branch @ sha"]
```

开始或继续时，先确认当前目录就是 tid 登记的 worktree，且调用参数中的 `attempt` / `execution_id` 是本次 handoff 目标；再读 `scripts/repo_template/task.py show <tid>`、task 目录下 `spec.md` / `task.md` / `review_*.md`、分支、`git status`、`diff_anchor`、测试与实施笔记判断入口：

|状态 / 证据|从哪继续|
|---|---|
|无 task worktree，或当前目录/分支不归属 tid|停止，回到 task-run 修复 start/ownership；本 skill 不自行 start|
|`active`，无 preflight/红灯证据|Step 1|
|红已有、实现未完|Step 3|
|绿过、黑盒未过|Step 4|
|黑盒过、无审阅|Step 5|
|有 FAIL、未满轮|Step 6 处置后按表回流|
|`blocked`|停止，呈 blocked 选项|
|`done` / `dropped`|不改动，交出 branch 与 HEAD；task-run 仍以 exact handoff/attempt gate 判断|

### Step 1：开干与前置

1. worktree 必须已由 task-run 创建并登记（`task-run` 先 `start <tid> [--base <前一分支>]`）。本 skill 不进入主仓、不执行 start，也不 reserve attempt。
2. 记录本次必填 identity `(tid, attempt, execution_id)`；后续生成的 `handoff.json` 必须逐字段使用这组值，不得用旧 handoff 或“当前最新 attempt”替代。
3. 有 `{doctor_cmd}` 则跑；无则实施笔记写「无」。失败：停止，先解决环境或走 spike。
4. 执行 `scripts/repo_template/task.py preflight <tid>`：状态、spec 完整、工作区一致性与未知契约分类。
    - `UNVERIFIED-BLOCKING` 或裸 `UNVERIFIED` → FAIL，必须停止。
    - `UNVERIFIED-SPIKE` → WARN；当前只可继续 Step 1 实验，不得进入 Step 2。
5. spec 契约区行为 AC 非空再继续（preflight 已查）。
6. spec 上下文区有 `UNVERIFIED-SPIKE`：先做实验，文档查询不能替代兼容实验。需外部环境的 SPIKE 先查环境齐备性（key、代理、夹具）并做最小实测；未实测不得以「难验证」上报阻断，实测失败须附输出证据；优先走 spec 预留的保守回退方案，而非中断。用 `scripts/repo_template/spikes.py new --slug <主题>` 建 `docs/spikes/{sid}_{slug}/`（锁内取号并从模板生成 `report.md`）；有实验代码自行加 `code/`。结论用 `scripts/repo_template/findings.py new` 建条目写入，报告留在 spike 目录。
7. 将全部 `UNVERIFIED-SPIKE` 改写为验证结论与验证方式，再运行 `scripts/repo_template/task.py preflight <tid> --require-verified`。严格门禁 PASS 后才可进入 Step 2。

### Step 2：红

可测部分先写失败测试；`{test_cmd}` 确认失败。测试须触达生产逻辑。

### Step 3：绿

实现至测试通过；`{test_cmd}` 确认。量大可派 subagent。

变更命中 `docs/blueprint/testing.md`「Schema / codegen 验证」触发路径时，运行其中生成命令与验证命令；未声明或写「无」则跳过。涉及 migration 独占窗口时，不与其他 migration 并行。生产 migration、部署和数据操作不由本步骤执行。

改既有测试纪律见 `AGENTS.md`「开发原则」TDD 条款。

### Step 4：黑盒

按 `docs/blueprint/testing.md` 中 `{blackbox_verify}` 执行。通过→Step 5。未通过且 `< max_verify_round` → 回 Step 3 再黑盒。未通过且 `≥ max_verify_round` → `block --reason blackbox`，停止。

进入最后一轮（已用轮次 = `max_verify_round - 1`）仍未过时，回 Step 3 前先在实施笔记写「聚焦修复计划」：失败项、根因假设、最小修复动作；范围仅限失败项，禁止顺手改动。把盲目重试升级为带假设的最后一击。

### Step 5：审阅

- 用 `git ls-files --others --exclude-standard` 列出本 task 新文件，剔除无关/临时/`.scratch/` 后，对明确路径执行 `git add -N -- <path...>`，让 untracked 产出进入 `git diff {diff_anchor}`；无新文件则跳过。不得用无路径 `git add -N`。
- 渲染 prompt：
    ```bash
    python3 scripts/repo_template/render_review_prompts.py \
      --task-dir docs/tasks/{tid}_{slug} \
      --out-dir .scratch/review_prompts
    ```
    prompt 内 `{task_dir}` 为绝对路径（落点锚定，与 reviewer cwd 无关）、`{repo_root}` 为 worktree 绝对根。reviewer 首步以**不带 -C** 的 `git rev-parse --show-toplevel` 探测会话 cwd，输出去掉首尾空白后须精确等于 `{repo_root}`，不符即停并报告「落点仓库校验失败」；此后所有 git 命令一律 `git -C '{repo_root}' ...`，不得依赖 cd（bash cd 不改变后续工具工作根）。派发后收到「落点仓库校验失败」报告说明派发会话 cwd 落错仓库：立即核对并重派，不得代写报告。
- 派 subagent 时只传文件路径，不把 prompt 正文内联进派发消息。
- `review_level=full`：code + test 两路并行；`single`：一路 general。
- 报告写入 task 目录：`full` 写 `review_code.md` / `review_test.md`；`single` 写 `review_general.md`。多轮追加，不覆盖历史。

### Step 6：处置

- 处置表唯一落点：`task.md` → `## Review 处置`。`status` 仅：`已修` / `遗留` / `撤回`。
- `status=遗留` 的内容不写 task.md：用 `scripts/repo_template/pending.py new --slug <主题>` 建条目并填写；`fix_ref` 填该 `pNNN` 或已有 follow-up tid。
- 运行：
    ```bash
    python3 scripts/repo_template/check_review_status.py \
      --task-dir docs/tasks/{tid}_{slug} \
      --max-review-round <N>
    ```
- `prompt_hint` 非空 → 下一轮派发附上轮撤回 finding_id 与理由。
- reviewer 标注 spec 过时：改 spec 上下文区，不计 FAIL，不因此回 Step 3。
- `overall=PASS` → Step 7。
- `FAIL` 且 `round < max`：填处置表；改代码/测试则 Step 3→4→5→6，只改必要文档也须回 Step 5 完整重审。进入最后一轮（`round = max - 1`）的处置前，除处置表外须先在实施笔记写「聚焦修复计划」：失败 finding、根因假设、最小修复动作；范围仅限失败项。
- `FAIL` 且 `round ≥ max`：处置表填完 → `block --reason review`，停止。
- 禁止同一 round 内「复核翻 PASS」。

### Step 7：收尾与执行 commit

进入本步前重新运行 `check_review_status.py`，确认最新 `overall=PASS`。

清洁度检查（必做）：对本 task 执行产生的全部新增行（committed + staged + unstaged + 未跟踪，单 ref diff 工作树语义）做机械 grep，判据按项目 stack 调整（Python 仓示例）：

```bash
mkdir -p .scratch
python3 scripts/repo_template/repo_state.py added-lines {diff_anchor} \
  --exclude 'docs/*' --exclude '.scratch/*' > .scratch/added_lines.txt
grep -cE 'print\(|pprint\(' .scratch/added_lines.txt || true   # debug 输出残留
grep -cE '\b(TODO|FIXME|XXX)\b' .scratch/added_lines.txt || true # 会话 TODO 残留
```

`grep -c` 零匹配时 exit 1，属预期绿态，须带 `|| true`；判据只看计数数字，不看退出码。非零计数 = 收尾前必须修掉的残留。spec 声明 `Cleanliness override:` 的项报告计数但不判失败（override 须写明放行理由与具体项，保持窄口径）。该检查专门防「未提交的 debug 残留」——只看 `..HEAD` 的 diff 对它不可见。

**7a 收尾文档**：

- 更新 `docs/specs/<slug>.md` 与 `docs/specs_index.md`。
- 更新受影响的 `docs/blueprint/`、`docs/guides/`、`README.md`、`AGENTS.md`、API 文档等。
- 写全 `task.md` 收尾报告；收尾报告不设遗留节，遗留在 `docs/pending/todo/`。
- pending 闭环：`scripts/repo_template/pending.py archive <pNNN...> --fix-ref {tid} --write`。
- 顺手发现登记（必做）：实施/测试/黑盒中观察到、但不属本 task 范围且未修的疑似存量问题，不得只写 `task.md` 笔记——`task.md` 随 task 归档后这些发现无人跟踪。逐条盘点处置：已复现确认的 bug 链式走 `task-bug`（根因 + 补测 + 登记）；疑点或技术债用 `pending.py new` 登记。已随本 task 修复或确认不成立的不登记。
- 测试假绿专项（必做）：测试过程中发现疑似假绿（断言过弱、mock 掉被测逻辑、只测假路径、缺集成层导致测试通过但逻辑有误）的存量测试，同样必须登记——能定位根因的走 `task-bug` 补测分析，暂不能定位的用 `pending.py new` 登记并注明疑似假绿。不得在收尾报告里一笔带过。
- 核对所有 `status=遗留` 行的 `fix_ref` 已指向 `pNNN` 或 follow-up tid。
- 用 `scripts/repo_template/findings.py new` 抽取可跨 task 复用的已验证事实。
- 在执行 commit 前读取当前完整 `HEAD`，确认它仍等于 task front matter 的 `diff_anchor`，并记为 `base_sha`；该值就是稍后 branch tip 执行 commit 的 first parent。若两者不等，说明已产生额外 commit，停止而不是写一个“当前 HEAD”掩盖一 task 一 commit 违约。
- 写交接单 `docs/tasks/{tid}_{slug}/handoff.json`（机器可读契约，随执行 commit 入库）：
    ```json
    {
      "tid": "t001",
      "attempt": 1,
      "execution_id": "0123456789abcdef0123456789abcdef",
      "status": "done",
      "branch": "t001_example",
      "base_sha": "0123456789abcdef0123456789abcdef01234567",
      "tests": "pytest -q：120 passed",
      "blackbox": "黑盒命令通过",
      "review": "第 2 轮 PASS",
      "ac_evidence": {
        "AC-001": ["tests/test_x.py::test_y 通过"],
        "AC-002": ["黑盒输出：返回 200"]
      },
      "pending": ["p047"],
      "findings": ["d012"]
    }
    ```
    `tid`、非 bool 的正整数 `attempt`、非空字符串 `execution_id` 必须逐字等于本 skill 输入；`status` 与 task 终态一致；`branch` 是当前 task 分支；`base_sha` 是执行 commit 前 HEAD 的完整 SHA，且必须等于 task `diff_anchor`。`tests`、`blackbox`、`review` 都必须是非空字符串；`ac_evidence` 是 JSON 对象，键必须精确等于 spec 验收标准节的全部 `AC-NNN` 编号（缺或多都会导致 integrate 门禁失败），值为非空字符串数组，每项是一条证据引用（测试用例/断言摘要、黑盒输出摘要、review 报告锚点）；`pending`、`findings` 必须是字符串数组，没有条目时写 `[]`。所有字段逐项必填。
- 本 skill 不写 attempt 控制面，也不记录 terminal/report/integrated；task-run 读取已入库 handoff 后，以同一 exact identity 完成后续生命周期。
- 其他 task 若受本 task 影响需改 spec，不在此直接改——手动并发下其他会话看不到本分支改动，且合并时制造冲突。列进交出汇报，由 task-run 处置。

**7b finish**：

```bash
scripts/repo_template/task.py finish <tid>
```

`finish` 将 task 归档并清空归档 front matter 的 worktree 字段；物理 worktree 此时仍存在。

**7c 执行 commit**：

- 把本 task 执行期全部改动（含 7a 文档、7b 归档移动）一次性 commit；subject 含 `{tid}`。
- 一 task 一执行 commit，不提交派生 index。
- commit 后确认 worktree clean，当前 branch tip 就是该执行 commit，且 `git rev-parse HEAD^` 精确等于 handoff 的完整 `base_sha`。
- 再核对 branch tip 中已归档 task 的 `handoff.json`：`tid` / `attempt` / `execution_id` / `status` / `branch` 与输入和 refs 一致，三项结果为非空字符串，`ac_evidence` 精确覆盖 spec 验收标准 AC，两项条目为字符串数组。
- blocked 未放行前不 finish、不 commit 终态。

## 停止条件

遇任一即停并汇报，不自行扩充停止条件：

- `preflight` FAIL 且无法在本 task 内修复。
- task `blocked`（呈加轮 / dropped 选项）。
- 需用户提供密钥、环境、产品决策等不可替代输入。
- 环境/权限/外部依赖阻断；基础设施连续失败 → `block --reason infra`。
- 工作区有与本 task 冲突的无关脏改动且无法安全隔离。

## 完成

交出一行，不询问合并：

```text
{tid} attempt={N} execution_id={ID}: {branch} @ {sha}
```

交接本体是已入库的 `handoff.json`（7a），这行只是给 task-run 的 exact identity 与 ref 线索。另附：测试与黑盒结果、review 轮次与结论、登记的 `pNNN` / `dNNN`、worktree 路径（待 task-run cleanup）。本 skill 到此结束，不写 terminal/report、不 cleanup、不 integrate；停止时改为汇报当前阻塞与恢复入口。
