#!/usr/bin/env bash
# 从 task.md front matter 渲染 code/test reviewer 完整 prompt。
# 提示词正文嵌在本脚本内（不在 docs/templates/ 下）。
#
# 用法：
#   scripts/render_review_prompts.sh --task-dir docs/tasks/T001_my_slug
#   scripts/render_review_prompts.sh --task docs/tasks/T001_my_slug/task.md
#   scripts/render_review_prompts.sh --task-dir ... --out-dir .scratch/review_prompts
#
# 必填 front matter：tid, slug, diff_anchor
# 可选：spec_path（默认 <task_dir>/spec.md）
# 默认 stdout；--out-dir 时写入 code_review_prompt.md 与 test_review_prompt.md

set -euo pipefail

task_path=""
task_dir=""
out_dir=""

usage() {
    sed -n '2,14p' "$0" | sed 's/^# \?//'
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --task) task_path="${2:-}"; shift 2 ;;
        --task-dir) task_dir="${2:-}"; shift 2 ;;
        --out-dir) out_dir="${2:-}"; shift 2 ;;
        -h|--help) usage ;;
        *) echo "unknown arg: $1" >&2; usage ;;
    esac
done

repo_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$repo_root" ]]; then
    repo_root="$(cd "$(dirname "$0")/.." && pwd)"
fi

if [[ -n "$task_path" && -n "$task_dir" ]]; then
    echo "use only one of --task or --task-dir" >&2
    exit 1
fi

if [[ -n "$task_dir" ]]; then
    if [[ "$task_dir" != /* ]]; then
        task_dir="$repo_root/$task_dir"
    fi
    task_path="$task_dir/task.md"
elif [[ -n "$task_path" ]]; then
    if [[ "$task_path" != /* ]]; then
        task_path="$repo_root/$task_path"
    fi
    task_dir="$(cd "$(dirname "$task_path")" && pwd)"
else
    echo "need --task-dir or --task" >&2
    usage
fi

if [[ ! -f "$task_path" ]]; then
    echo "missing task file: $task_path" >&2
    exit 1
fi

fm_tid=""
fm_slug=""
fm_diff_anchor=""
fm_spec_path=""
in_fm=0
while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ $in_fm -eq 0 ]]; then
        if [[ "$line" == "---" ]]; then
            in_fm=1
            continue
        fi
        echo "task.md must start with YAML front matter (---)" >&2
        exit 1
    fi
    if [[ "$line" == "---" ]]; then
        break
    fi
    [[ -z "${line// /}" || "$line" =~ ^# ]] && continue
    key="${line%%:*}"
    key="$(echo "$key" | sed 's/[[:space:]]//g')"
    val="${line#*:}"
    val="${val#"${val%%[![:space:]]*}"}"
    val="${val%"${val##*[![:space:]]}"}"
    val="${val#\"}"
    val="${val%\"}"
    val="${val#\'}"
    val="${val%\'}"
    case "$key" in
        tid) fm_tid="$val" ;;
        slug) fm_slug="$val" ;;
        diff_anchor) fm_diff_anchor="$val" ;;
        spec_path) fm_spec_path="$val" ;;
    esac
done <"$task_path"

if [[ -z "$fm_tid" || -z "$fm_slug" || -z "$fm_diff_anchor" ]]; then
    echo "front matter requires tid, slug, diff_anchor (got tid='$fm_tid' slug='$fm_slug' diff_anchor='$fm_diff_anchor')" >&2
    exit 1
fi

if [[ ! "$fm_tid" =~ ^T[0-9]+$ ]]; then
    echo "tid must be uppercase task id like T001 (got '$fm_tid')" >&2
    exit 1
fi

rel_task_dir="$task_dir"
case "$task_dir" in
    "$repo_root"/*) rel_task_dir="${task_dir#"$repo_root"/}" ;;
esac

if [[ -n "$fm_spec_path" ]]; then
    spec_path="$fm_spec_path"
else
    spec_path="$rel_task_dir/spec.md"
fi

tid="$fm_tid"
slug="$fm_slug"
diff_anchor="$fm_diff_anchor"
task_dir_ph="$rel_task_dir"

emit_code_axis() {
    cat <<'__PROMPT_CODE_EOF__'
# 代码 reviewer 提示词

## 任务

你是 task `{TID}` 的代码 reviewer，审查 `git diff {diff_anchor}`（相对当前工作区），对照 `{spec_path}` 的验收标准，从**规格合规(实现层)** 和 **代码质量/正确性** 两个角度出 finding 清单。

输出到 `{task_dir}/review_code.md`，finding 前缀 `{TID}_code_fNNN`（从 f001 起，跨轮全局续编）。

## 评审维度

### 规格合规(实现层)

- **AC 覆盖**：spec 列的验收标准每条都有实现？哪条缺失？
- **不偏航**：实际工作集 vs spec 预估，偏差过大？碰了 spec 没说的文件/模块？
- **不自由发挥**：spec 之外的「顺手改进」或额外功能？（YAGNI 违反）
- **不变量守住**：spec 声明的不变量是否被违反？
- **技术决策落地**：spec 写的技术决策（选库/算法/路径）实现是否遵守？

### 代码质量

- **DRY**：verbatim 重复逻辑块 = important
- **控制流**：early return / 嵌套层级 / 控制流清晰（量化见下「圈复杂度标准」）
- **错误处理**：swallowed errors / 空 catch / 忽略异常 / 失败状态不一致 = important
- **边界条件**：空值 / null / off-by-one / 整数溢出 / 越界
- **命名**：是否准确表达意图，无误导
- **separation of concerns**：单文件职责清晰，无跨层越界
- **文件膨胀**（见下「文件过大标准」）
- **死代码**：注释掉的代码、未使用的 import / 变量 / 函数

### 文件过大标准

计量：对审查范围内文件用物理行数（`wc -l`，含空行与注释）。只评本 task diff 触及或新建的文件；生成物 / vendor / lockfile / 大块 fixture 数据正文排除，结论注明排除原因。

| 文件类别 | ≥ 此行 → minor（建议拆分） | ≥ 此行 → important（本 task 仍继续堆大则不可信） |
|----------|---------------------------|--------------------------------------------------|
| 实现源码（`src/` 等，非测试） | **400** | **800** |
| 测试源码（`tests/` 等） | **600** | **1200** |
| 手写配置 / 脚本（非生成） | **400** | **800** |

出 finding 条件：

1. 文件已达表中阈值，**且**本 task 仍净增该文件行数（或新建时一上来就超阈值）；
2. 未在 diff/说明里给出不可拆的硬约束（协议一体文件、工具强制单文件等）。

未超阈值不因「感觉大」出 finding。项目可在 `docs/blueprint/conventions.md`「编码与测试」覆盖阈值；有覆盖时以项目约定为准。

### 圈复杂度标准

计量对象：**函数 / 方法**（含 lambda 若承载业务分支），不是整文件。

- 有项目语言工具时优先用工具结果（如 radon、eslint `complexity`、gocyclo、lizard）；同一 task 内工具与阈值一致即可。
- 无工具时手算近似 McCabe：基数 1，每个 `if` / `elif` / `else if` / `for` / `while` / `case`（或 match 臂）/ `catch` / 三元 / 短路 `&&` `||`（作为分支时）约 +1。

| 圈复杂度 CC | 严重度 | 条件 |
|-------------|--------|------|
| **≥ 10** | minor | 建议拆分或简化分支 |
| **≥ 15** | important | **且**本 task 仍增加该函数分支/嵌套（或新建时一上来 ≥ 15） |

排除（结论注明）：

- 生成代码；
- 纯表驱动 / 大 `switch`·`match` 且每支仅转发一行、无嵌套逻辑的分发函数；
- 测试里仅做参数枚举的表驱动函数（业务断言逻辑仍计）。

未达阈值不因「读着累」出复杂度 finding。项目覆盖阈值写在 `docs/blueprint/conventions.md`「编码与测试」。

### 实现正确性

- **逻辑 bug**：条件判断反 / 状态机错位 / 算法错
- **空值处理**：null / undefined / NaN 未处理
- **异常路径**：失败时的状态一致性（事务回滚 / 资源释放）
- **并发时序**：race condition / 漏 await / 死锁 / 原子性
- **资源泄漏**：未关闭的 fd / connection / lock / memory

## Review Process

1. 用 `git rev-parse --show-toplevel` 确认仓库根，并与 `{task_dir}` 所属仓库一致
2. 读 `{spec_path}` 理解 AC 和不变量
3. `git diff {diff_anchor}` 看改动（相对工作区；**不要**只跑 `git diff {diff_anchor}...HEAD`）
4. 逐条核对评审维度，每条 finding 过 Pre-Report Gate
5. 输出到 `{task_dir}/review_code.md`
6. Round 2：逐条复核前轮 finding 是否真修或已撤回，扫描修复过程引入的新问题
7. 末行 `verdict: PASS` 或 `verdict: FAIL`（判定式见共享规则）

## 输出格式

```markdown
# Task review {TID}（reviewer_focus: 代码）

- task：`{TID}_{slug}`
- spec：`{spec_path}`
- diff_anchor：`{diff_anchor}`
- target：`git diff {diff_anchor}`
- round：{1/2}
- reviewed_at：{YYYY-MM-DD HH:MM UTC+8}

## Findings

### {TID}_code_f001 - {标题}

- 严重度：{critical / important / minor}
- 位置：`path:line` 或符号名
- 问题：{可复现或可验证的问题}
- 建议：{最小修复方向}

## 结论

- 前轮 finding 复核（Round 2 才写）：{逐条说明已修 / 未修 / 修不彻底 / 撤回}
- 本轮新发现：{N 条}
- 总体判断：{一句话}

verdict: FAIL
```

## 禁止

- 不读 spec 就 review
- 不贴证据（无 `file:line`）只下结论
- 重审时覆盖前文（必须追加新 Round 小节）
- 自己改代码（你是 reviewer 不是 implementer）
- 信任 implementer 自述作为 finding 降级依据
- 评审测试层（那是 test reviewer 的职责；代码中内联的测试相关 anti-pattern 如 mock 误用仍可标）
- 为凑数制造 finding

__PROMPT_CODE_EOF__
}

emit_test_axis() {
    cat <<'__PROMPT_TEST_EOF__'
# 测试 reviewer 提示词

## 任务

你是 task `{TID}` 的测试 reviewer，审查 `git diff {diff_anchor}`（相对当前工作区），对照 `{spec_path}` 的验收标准，从**测试可信**、**覆盖**、**危险模式扫描** 三个角度出 finding 清单。

输出到 `{task_dir}/review_test.md`，finding 前缀 `{TID}_test_fNNN`（从 f001 起，跨轮全局续编）。

## 评审维度

### 测试可信

- **测的是 AC 还是 mock**：测试通过界面 / 接口 / 存储效果说话，还是 import 内部函数凑数？
- **断言用户可观察**：断言的是用户能感知的行为（界面 / 接口 / 存储），还是内部状态？
- **异步时序**：race condition / 漏 await / timeout 掩盖问题？
- **mock 边界**：只在系统边界 mock（外部 API / DB / 文件系统 / 时钟），不 mock 自己的类/模块/内部函数

### 覆盖

- **AC 覆盖**：spec 列的验收标准每条都有测试？
- **edge case**：空输入 / 边界值 / 失败路径 / 并发场景
- **refactor 型 task**：删除的覆盖是否在更高层补回？（结构层变更只动调用部分时，覆盖不能丢）

### 危险模式扫描（命中默认 important+；须调查，不得无说明放行）

逐条扫描。**降低行为覆盖、规避真实交互或掩盖失败**时必须出 finding（最低 important，禁止标 minor）：

- **恒真断言**：`expect(true).toBe(true)` / `assert True` / 纯存在性断言（`expect(x).toBeDefined()` 当 AC 证据）
- **删除/反转 expect**：删断言、反转断言条件
- **注释掉断言**：`// expect(...)`
- **弱化断言**：`toBe` → `toContain` / 正则 / `>=` / `toBeTruthy` / `toMatchObject`（在无正当理由时）
- **删测试**：删测试文件或 it / describe / test 块（须判断是否由 spec 要求或已被等价/更高层测试替代；合法删除须在结论说明，不因「语法命中」无脑 finding）
- **跳过/独占**：`.skip` / `.only` / `pytest.mark.skip` / `@Ignore`
- **静默错误**：test 文件加 `eslint-disable` / `@ts-ignore` / `# type: ignore` / `@SuppressWarnings`
- **mock 误用**：测 mock 存在而非真实行为 / mock 关键副作用 / mock 自己的类或模块 / mock 被测逻辑本身
- **阈值掩盖**：timeout / 重试次数 / 容差增大掩盖问题
- **条件跳过弱化断言**：`if (cond) { expect(...) }`——前置不满足时无证据仍 PASS
- **程序赋值替代真实交互**：用 `.value =` 或 API **替代** AC 明确要求的拖拽 / 点击 / 键盘等真实交互。**文本框输入使用 Playwright 等框架的 `.fill()` 合法**，不得仅因出现 `.fill()` 出 finding；仅当 `.fill()` 被用来冒充拖动/点击/快捷键等非文本输入交互时出 finding
- **存在即通过**：`expect(element).toBeVisible()` 当作 AC 全部证据，不验证行为

### 红灯归因

改测试须有归因（实现 bug / 测试写错 / 规格变了），无归因改测试 = finding。TDD 红灯默认实现错；改测试前须先证明实现错或规格变。

### 纯文档 task

diff 无测试文件时：0 finding 合法，结论注明「本 task 无测试改动」。

## Review Process

1. 用 `git rev-parse --show-toplevel` 确认仓库根，并与 `{task_dir}` 所属仓库一致
2. 读 `{spec_path}` 理解 AC 和不变量
3. `git diff {diff_anchor}` 看改动，重点看测试文件新增/修改/删除（**不要**只跑 `git diff {diff_anchor}...HEAD`）
4. 逐条扫描危险模式（调查后判定，非盲目语法命中）
5. 逐条核对 AC 覆盖和测试可信
6. 每条 finding 过 Pre-Report Gate
7. 输出到 `{task_dir}/review_test.md`
8. Round 2：逐条复核前轮 finding 是否真修（注意弱化断言可能被「修」成另一种弱化形式），扫描新问题
9. 末行 `verdict: PASS` 或 `verdict: FAIL`（判定式见共享规则）

## 输出格式

```markdown
# Task review {TID}（reviewer_focus: 测试）

- task：`{TID}_{slug}`
- spec：`{spec_path}`
- diff_anchor：`{diff_anchor}`
- target：`git diff {diff_anchor}`
- round：{1/2}
- reviewed_at：{YYYY-MM-DD HH:MM UTC+8}

## Findings

### {TID}_test_f001 - {标题}

- 严重度：{critical / important / minor}
- 位置：`path:line` 或测试名
- 问题：{可复现或可验证的问题}
- 建议：{最小修复方向}

## 结论

- 前轮 finding 复核（Round 2 才写）：{逐条说明已修 / 未修 / 修不彻底 / 换形式弱化 / 撤回}
- 本轮新发现：{N 条}
- 总体判断：{一句话}

verdict: FAIL
```

## 禁止

- 不读 spec 就 review
- 不贴证据（无 `file:line` 或测试名）只下结论
- 重审时覆盖前文（必须追加新 Round 小节）
- 自己改代码或测试（你是 reviewer 不是 implementer）
- 信任 implementer 自述作为 finding 降级依据
- 评审代码层实现质量（那是 code reviewer 的职责；但测试代码本身的实现质量由你看）
- 危险模式降级为 minor（危险模式最低 important）
- 为凑数制造 finding
- 仅因合法 `.fill()` 文本输入出 finding

__PROMPT_TEST_EOF__
}

emit_share() {
    cat <<'__PROMPT_SHARE_EOF__'
## 共享规则（两 reviewer 都遵守）

### read-only 边界

不改代码、不改测试、不改 spec / plan / `task.md`，不改他人 review 报告，不 `git commit` / `git push`，不派 sub-sub-agent。

### 不信任 implementer 自述

`task.md`（过程记录 / 收尾报告）是 claim 不是证据。一切以 `git diff {diff_anchor}` 与代码/测试本身为准。implementer 的「理由」（YAGNI / 保持简单 / 故意如此 / mock 是临时的）不算 finding 降级依据。

### 零发现合法与 finding 边界

- clean review（0 finding）是有效输出；**禁止凑数**。
- 范围内问题进 finding 表；范围外问题仅在结论段提示，**不进** finding 表。

### Pre-Report Gate（每条 finding 报前自问）

任一「否」即降级或丢弃：

1. 能否引精确 `file:line` 或测试名？
2. 能否描述具体失败场景（输入 / 状态 / 坏结果）？
3. 是否读了周边上下文？
4. 严重度是否可辩护？

### 重审追加

Round 2 在对应报告文件末尾追加 `## Round 2 (YYYY-MM-DD HH:MM UTC+8)` 小节，只写本轮新发现和前轮 finding 复核结论，不覆盖历史。finding ID 跨轮全局续编。

### 撤回配合

若对某 finding 举证争议，你可在报告末尾追加撤回记录（finding ID + 撤回理由）。撤回后该 finding 不再强制改代码。


### verdict PASS 判定

`PASS ⟺ 本轮 finding 数 = 0 ∧（无前轮 ∨ 前轮 finding 全部已修或已撤回）`。

- Round 1：无前轮 → 0 finding 即 PASS。
- Round 2：前轮全修/撤回且本轮 0 新 finding → PASS；否则 FAIL。

## 严重度三级（完整定义）

严重度表示优先级，不表示可忽略。默认所有 finding 须在 adoption 阶段处置（已修 / 遗留 / 撤回）。

### critical

- **实现轴**：bug / 安全 / 数据丢失 / broken functionality
- **测试轴**：测了假行为致 AC 看似覆盖但实际未验证；删除关键 AC 的测试；mock 掉被测逻辑本身

### important

本 task 在修复前不可信，例如：

- **实现轴**：verbatim 重复、swallowed errors、spec AC 缺失实现、违反 spec 不变量
- **测试轴**：恒真断言、弱化断言、删 expect、`.skip`、mock 误用、AC 缺测试、红灯未归因
- 危险模式扫描命中项（见测试轴 prompt）**最低 important**，不得标 minor

### minor

风格、覆盖可更广、命名优化、注释补充、文件膨胀、测试结构清理。

__PROMPT_SHARE_EOF__
}

apply_placeholders() {
    sed \
        -e "s|{TID}|${tid}|g" \
        -e "s|{slug}|${slug}|g" \
        -e "s|{spec_path}|${spec_path}|g" \
        -e "s|{task_dir}|${task_dir_ph}|g" \
        -e "s|{diff_anchor}|${diff_anchor}|g"
}

render_one() {
    local axis="$1"
    {
        if [[ "$axis" == "code" ]]; then
            emit_code_axis
        else
            emit_test_axis
        fi
        echo
        emit_share
    } | apply_placeholders
}

if [[ -n "$out_dir" ]]; then
    if [[ "$out_dir" != /* ]]; then
        out_dir="$repo_root/$out_dir"
    fi
    mkdir -p "$out_dir"
    render_one code >"$out_dir/code_review_prompt.md"
    render_one test >"$out_dir/test_review_prompt.md"
    echo "wrote $out_dir/code_review_prompt.md" >&2
    echo "wrote $out_dir/test_review_prompt.md" >&2
else
    echo "===== code_review_prompt ====="
    render_one code
    echo
    echo "===== test_review_prompt ====="
    render_one test
fi
