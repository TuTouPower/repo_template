# plan：模板仓接入 md_kx

来源：用户要求模板仓与消费仓非归档 Markdown 统一用 md_kx 格式化。md_kx 三种表风格（`compact` = `|a|b|`、`spaced` = `| a | b |`、`pad` = 按列对齐）由 md_kx 仓交付，本仓只消费。

> **状态（2026-08-13）**：未落地。本文是方案推导，尚未立项、未改生产树。**执行本计划（立项或改生产树）之前，必须先过「前置门禁」。门禁不过，停。**

## 前置门禁：md_kx 必须先交付

本仓不改 md_kx。模板接入依赖 md_kx 仓 t008 / p008 已完成并装到本机 `PATH` 上的 `md_kx`。旧二进制（t003：`none`/`pad`/`compact`，且 compact 实际输出 `| a | b |`）不能用来执行本计划。

### 工具必须做到

对同一张输入表（单元格有效文本 `a`、`b`），`table_mode` / `--table-mode` 只能是下面三种，默认 `spaced`，`none` 非法：

|风格|单元格空白|分隔行|示例|
|---|---|---|---|
|`compact`|有效文本与两侧 `\|` 之间零空格|两侧也零空格|`\|a\|b\|` / `\|---\|---\|`|
|`spaced`|两侧各恰好一个空格，不按列对齐|`\| --- \|`|`\| a \| b \|`|
|`pad`|两侧至少一空格，再按列补齐，外层 `\|` 对齐|分隔段随列宽|`\|    a   \| b \|`|

还须同时成立：

- 对齐冒号语义保留：compact 为 `:---|` / `|:---:|` / `|---:`；spaced 冒号外侧各一空格；pad 保留冒号并按列拉长。
- 单元格内 `\|` 不拆列。
- 空单元格：compact `||`，spaced `| |`，pad 按列宽补空格。
- 同一风格二次格式化无 diff。
- 不含表的文档，三风格输出一致。
- CLI 与 `.md_kx.toml` 都能选风格，CLI 优先。
- `none` 或其它名字：报错，不改文件。

权威需求在 md_kx 仓：`docs/archive/pending/p008_table_three_output_styles.md`、`docs/tasks/t008_table_three_output_styles/spec.md`。

### 开干前怎么验

在**本机将要用来跑模板仓的那个 `md_kx`** 上测（`which md_kx` / `md_kx --version` 先记下来）。不要用未重装的旧 `uv tool`。

1. `md_kx --help` 的 `--table-mode` 只列 `compact` / `spaced` / `pad`，没有 `none`。
2. 对夹具表分别指定三风格，输出符合上表。至少用这一份输入：

```markdown
|路径|用途|
|------|------|
|`a`|写|
```

预期：

- `--table-mode compact` → `|路径|用途|` / `|---|---|` / `|`a`|写|`（有效文本贴竖线）
- `--table-mode spaced` → `| 路径 | 用途 |` / `| --- | --- |`
- `--table-mode pad` → 同列外层 `|` 对齐，有效文本两侧至少各一空格
- 再跑同一风格一次，文件无 diff

3. `--table-mode none` 非 0 退出，文件未改。
4. 无 CLI、无 toml 时，默认是 spaced。
5. 模板将采用的配置（`.md_kx.toml` 里 `table_mode = "compact"`）对上面那张表必须得到 `|路径|用途|`，不能是 `| 路径 | 用途 |`。

任一条失败 = md_kx 没搞完。本计划的 task 1/2/3 都不开。包装脚本、挂钩、存量 format 全部停。

### 和后文的关系

A–E、建议切分都默认门禁已过。文中「新 compact」= 本节的 `|a|b|`，不是 t003 的 `| a | b |`。

## 问题

模板仓已有 `.md_kx.toml`（`compact` / 4 空格 / `number`），但：

1. 配置不在 `HARD_SYNC_FILES`，消费仓同步不到。
2. 没有仓库内入口。`md_kx .` 递归扫全部 `*.md`，不读 gitignore，会进 `node_modules/`；`exclude` 只在 Python 3.13+ 有效，模板 CI 是 3.12。
3. `repo-template-sync` 写着「合并共享文稿禁止任何 Markdown 格式化」，是防 prettier 把表 pad 成宽列。md_kx compact 不是那种，但这条禁令不改，agent 不敢跑。
4. agent 写 md 的路径（task-work 收尾、pending/findings/spikes `new`、task-create 落盘）没有强制 format，风格会漂。

归档「内部文件只准新增」。用户已拍板：**跳过 `docs/archive/`**。这轮只改模板仓，消费仓靠之后 sync。

## 设计总览

|#|机制|落点|为什么这样|
|---|---|---|---|
|A|`.md_kx.toml` 硬同步|`HARD_SYNC_FILES`|风格是模板约定，不是消费侧定制。不进 `SHARED_FILES`，避免每轮裁定|
|B|包装脚本当唯一入口|`scripts/repo_template/md_format.py`|随 `HARD_SYNC_DIRS` 自动进消费仓；选文件、黑名单、缺二进制在仓库内解决，不依赖 md_kx 的 3.13 exclude|
|C|写 md 的出口挂钩|pending / findings / spikes `new`；task-work Step 7a；task-create 落盘自检|机械化写入由脚本收；agent 写的正文由 skill 在 commit / 落盘前收|
|D|约定与禁令改写|`conventions.md`、repo-template-sync skill、`testing.md` doctor、`README.md`|禁止 prettier/pad 保留；语义合并结束后允许包装脚本|
|E|本仓一次性 format|非归档已跟踪 md，单独 chore|存量对齐。必须等本机 md_kx 已是新 compact，否则表会变成 spaced|

## 不做什么

- 不改 md_kx、不发版、不塞源码进模板。
- 不格式化 `docs/archive/**`。
- 本轮不改消费仓工作树，不改消费仓 CI。
- 不上 pre-commit：模板没有这套工具链，强加等于给所有消费仓多一个运行时。
- 不在 toml 写 `exclude`（3.13 门槛）。
- 不把本议题写入 `decision_log.md`：那是「两份以上复盘诊断过的议题」总账，本条是单次设计。

## A. 契约文件

`.md_kx.toml` 已存在，内容保持：

```toml
number = true
table_mode = "compact"
indent_width = 4
```

硬同步后消费侧风格跟模板走。要改风格用 `user_prompts` 显式 `keep`，不默认 merge。

## B. 包装脚本

```text
python3 scripts/repo_template/md_format.py PATH...
python3 scripts/repo_template/md_format.py --changed
python3 scripts/repo_template/md_format.py --all
python3 scripts/repo_template/md_format.py --check
```

- 找 `PATH` 上的 `md_kx`。CLI 找不到则非 0，打印安装提示。不每次 `uvx`。
- `--all`：`git ls-files '*.md'` 再扣黑名单。不扫工作树。
- `--changed`：相对 `HEAD` 的已跟踪改动 + staged + 未跟踪 `.md`（扣黑名单和 `.scratch/`）。
- 黑名单：`docs/archive/`。`docs_repo/` 本仓要 format（本来就不 sync）。
- 点名路径落在黑名单：报错，不默默跳过。
- 风格只读 `.md_kx.toml`，包装脚本不重复传 `--table-mode`。

库函数给 `pending.py` / `findings.py` / `spikes.py` 用：找不到 `md_kx` 时跳过并警告，避免 CI 无二进制时现有测试红。CLI 必须硬失败。

## C. 挂钩

|落点|行为|
|---|---|
|pending / findings / spikes `new`|刚写的文件走库函数|
|task-work Step 7a|执行 commit 前 `--changed`；失败不得 commit|
|task-create 落盘自检|本批新建 spec/task 跑包装脚本后再 preflight|
|`task.py add`|只复制模板，不 format；填完正文由 create / work 收|

## D. 约定改写

- `conventions.md`：改完 md 必须跑包装脚本；表用 compact（`|a|b|`）。
- repo-template-sync：合并过程仍禁 prettier / pad；语义合并结束后用包装脚本。模板侧这些文件须已是 md_kx 输出，否则消费侧一 format，下次 sync 又是整表噪声。
- `testing.md` doctor：增加「md_kx 在 PATH」；原 pytest collect-only 保留。
- `README.md` 初始化补安装 md_kx 一步。
- 本轮不改 `.github/workflows/repo-template-ci.yml`。

## E. 存量 format

`md_format.py --all`，不碰 archive。覆盖 `AGENTS.md`、skills、blueprint、guides、`docs_repo/`、其余已跟踪 md。单独 chore，不和 A–D 混 commit。

开干前再跑一遍「前置门禁」第 5 条（toml compact 必须得到 `|路径|用途|`）。旧二进制会写成 spaced，禁止继续。

消费仓（本文不执行）：sync → 安装 md_kx → 各仓 `--all` → 单独 chore。

## 建议切分

实施时走标准 task 流程。`review_level=single`。现有主仓脏改动不混入，只在 task worktree 做。**立项前先过前置门禁**；门禁不过不 `task.py add`。

|顺序|slug|交付|依赖|
|---|---|---|---|
|1|`md_format_wrapper`|脚本 + toml 硬同步 + 测试（含 `test_repo_sync` fixture 补 `.md_kx.toml`）|前置门禁|
|2|`md_format_hooks`|C + D|1|
|3|`md_format_existing`|E|1 + 门禁第 5 条复测|

## 验证

- `pytest tests/repo_template/test_md_format.py tests/repo_template/test_repo_sync.py tests/repo_template/test_pending.py -q`
- 新 md_kx 下：夹具 `--check`，compact 为 `|a|b|`，二次 format 无 diff
- task 3 后：`--all --check` 对本仓非归档 md 为绿；`docs/archive/` 无 diff
