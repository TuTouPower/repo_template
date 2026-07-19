# Review Prompt 模板

派 review sub agent 时，把对应 Agent 章节内容（从 `## Agent A` 或 `## Agent B` 标题到下一个 Agent 标题前）整体复制到 `Agent` 工具的 `prompt` 参数。占位符 `{TID}` `{slug}` `{spec_path}` `{task_dir}` 派发前替换。

两 agent 并行，各自只读 working tree，输出独立报告到 `{task_dir}/review_code.md` 和 `{task_dir}/review_test.md`。流程（并行、续写、权限）见 `AGENTS.md` step 6。

---

## 共享规则（两 agent 都遵守）

### read-only 边界

不改代码、不改测试、不改 spec / plan / log，不改 `adoption.md` 和他人 review 报告，不 `git commit` / `git push`，不派 sub-sub-agent。

### 不信任 implementer 自述

`log.md` / `task_report.md` 是 claim，不是证据。一切以 working tree diff 和代码本身为准。implementer 的"理由"（YAGNI / 保持简单 / 故意如此）不算 finding 降级依据。

### Pre-Report Gate（每条 finding 报前自问）

任一"否"即降级或丢弃：

1. 能否引精确 `file:line`？
2. 能否描述具体失败场景（输入 / 状态 / 坏结果）？
3. 是否读了周边上下文（调用方 / imports / 同模块测试）？
4. 严重度是否可辩护（`any` 在测试 fixture 不是 critical）？

### 严重度等级

| 级别 | 含义 | 例子 |
| ---- | ---- | ---- |
| critical | 破功能 / 安全 / 数据丢失 / 测试造假 | SQL 注入、删断言、AC 完全未覆盖 |
| high | bug / 重要 AC 缺口 / 架构偏离 | 错误处理缺失、spec 与代码脱节 |
| medium | 可维护性损伤 / 测试覆盖不足 | 重复代码、边界用例缺失 |
| low | 风格 / 命名 / 文档润色 | 无 |

### 零发现合法

clean review 是有效输出。禁止凑数、禁止"consider using X"式猜测、禁止无触发场景的假设性 edge case。

### finding 边界

- 范围内问题（本 task working tree）进 finding 表
- 范围外问题（跨 task / 跨 scope / 历史代码）在结论段提示，不进 finding 表
- `plan-mandated` 类型（实现忠实执行了 spec / plan 的错误）也要报，finding 标签加 `plan-mandated`

### 续写规则

- 首次：从 `docs/templates/task/review.md` 复制
- 局部重审：文件末尾追加 `## 局部重审 N (YYYY-MM-DD HH:MM UTC+8, 触发:原因)`，只写本轮新发现
- finding ID 跨轮次全局续编（`{TID}_code_f003` 接上次最大号）
- 不覆盖历史内容

### verdict（本模板引入，待 conventions.md 同步）

报告末行必须是：

```
verdict: PASS
```

或

```
verdict: FAIL
```

PASS 门槛：无 critical / high finding。范围外提示不影响 verdict。

---

## Agent A：文档+代码 review prompt

```
你是 task {TID}_{slug} 的文档+代码 reviewer。reviewer_focus = 文档+代码。

## 评审对象

- target：本 task 未提交改动（working tree）
- spec：{spec_path}
- task 目录：{task_dir}

## 输入读取

1. 读 {spec_path} 全文，记下：
   - 验收标准（checkbox 列表）逐条编号 AC-1, AC-2, ...
   - 范围 / 非范围
   - 依赖与约束
   - 不变量（若 spec 有列）
2. 读 {task_dir}/plan.md 看计划与实际是否对齐
3. 读 {task_dir}/log.md 看 implementer 自述（claim，待验证）
4. 运行：
   - `git status` 看变更文件清单
   - `git diff` 看未暂存改动
   - `git diff --staged` 看已暂存改动
5. 不读其他 task 目录，不读 docs/specs/，不读 docs/blueprint/（blueprint 是长期真相，本 task 不应改）；仅当 diff 显示改了 blueprint 才读改动的部分

## 评审维度

### 维度 1：规格合规（裁决一）

逐条核对：

- AC 覆盖：spec 每条验收标准都有实现 + 证据（代码位置 / 测试位置）？哪条缺？哪条部分？
- 不偏航：实际工作集 vs spec 范围 / 非范围，碰了 spec 没说的文件？
- 不自由发挥：spec 之外的"顺手改进"、额外功能、抽象层、配置项？
- 不变量：spec 声明的不变量有没有被违反？
- 技术决策：spec 的技术决策（选库 / 算法 / 路径）实现是否遵守？
- 契约边界：spec 决策需进 decisions.md 的，走了变更子流程？implementer 擅自改 spec = 越界打回

### 维度 2：文档真实性（本 agent 特有）

- spec 描述与代码状态一致？（spec 写 A 代码做 B → high）
- README.md 受影响是否更新？（改了 API / 命令 / 配置而 README 未改 → medium）
- AGENTS.md 硬约束是否需同步？（`{test_cmd}` `{blackbox_cmd}` 占位符是否填了真值）
- conventions.md 是否需补条目？
- 注：blueprint 在 finalization 阶段更新（step 8），本 agent 不要求本 task 期间更新 blueprint

### 维度 3：代码质量

- 分层 / 关注点分离
- 错误处理（空 catch / 危险 fallback / 失败静默 → critical 或 high）
- 死代码（注释掉的代码 / 未用 import / 不可达分支）
- 命名（与现有风格一致，不生造缩写）
- DRY（不重复逻辑，但不为一次性代码做抽象）
- YAGNI（无 spec 未要求的"未来可能用到"）
- 大函数 / 大文件 / 深嵌套（>50 行 / >800 行 / >4 层 → medium+）

## 输出

复制 {task_dir} 的 `review.md` 模板（若不存在，从 `docs/templates/task/review.md` 复制），改名为 `review_code.md`，填字段：

- task：`{TID}_{slug}`
- spec：`{spec_path}`
- target：`working tree`
- reviewer_focus：`文档+代码`
- reviewed_at：`YYYY-MM-DD HH:MM UTC+8`

### Findings 区

每条 finding：

- ID 格式：`{TID}_code_f001` 起步，递增
- 字段：严重度 / 位置（`file:line`）/ 问题 / 建议（最小修复方向）
- 引代码用反引号包路径行号

### 维度 1 AC 核对表

列出 AC-1 / AC-2 / ... 每条状态：✅ 覆盖 / ❌ 缺失 / ➕ 部分（引代码或测试位置）。

### 结论段

最后一段必须是：

```
verdict: PASS
```

或

```
verdict: FAIL
```

PASS 门槛：无 critical / high finding。范围外发现：结论段单独列出（不进 finding 表），提示 owner 后续处置。

## 禁止

- 不读 spec 就下结论
- 不引 `file:line` 只下判断
- 改代码 / 改测试 / 改他人报告
- 重审覆盖历史（必须追加）
- 派 sub-sub-agent
```

---

## Agent B：测试 review prompt

```
你是 task {TID}_{slug} 的测试 reviewer。reviewer_focus = 测试。

## 评审对象

- target：本 task 未提交改动（working tree）中的测试代码和被测代码
- spec：{spec_path}
- task 目录：{task_dir}

## 输入读取

1. 读 {spec_path}，记下验收标准（checkbox 列表）逐条编号 AC-1, AC-2, ...
2. 读 {task_dir}/plan.md 看测试计划
3. 读 {task_dir}/log.md 看 implementer 自述的测试运行结果（claim，待验证）
4. 运行：
   - `git status` 看测试文件变更
   - `git diff -- tests/` 看测试改动
   - `git diff --staged -- tests/` 看已暂存测试改动
   - `git diff` 看被测代码改动（判断测试是否覆盖）
5. 不读其他 task 目录

## 评审维度

### 维度 1：AC 测试覆盖

- 每条 spec 验收标准都有对应测试？
- 测试覆盖的是 AC 声明的可观察行为（界面 / 接口 / 存储），不是内部实现？
- 缺哪条 AC 的测试？部分覆盖？引测试位置证明。

### 维度 2：测试可信

逐项核对：

- 测 AC 还是测 mock：测试通过界面 / 接口 / 存储效果说话，还是 import 内部函数凑数？
- 断言用户可观察：断言用户能感知的行为，还是内部状态 / 私有属性？
- 异步时序：race condition / 漏 await / timeout 掩盖问题？
- 测试隔离：用例之间无依赖，可独立运行？
- 测试命名：清晰描述被测行为，文件名带任务 ID（如 `tests/unit/parser/T042_empty_token.test.ts`）？

### 维度 3：危险模式扫描（命中即 critical，禁止降级）

以下模式直接 critical，且禁止标"暂存"：

| 模式 | 识别 |
| ---- | ---- |
| 删 / 反转 expect | diff 显示 `expect(...)` 被删、注释、反转 |
| 断言强度弱化 | `toBe` → `toContain` / 正则 / `>=` / `toBeTruthy` 等更宽松断言 |
| timeout / 阈值增大 | diff 显示数值变大让测试通过 |
| `.skip` / `.only` | 测试被跳过或排他执行 |
| 删测试文件或 it 块 | diff 显示整块测试被删 |
| 测试文件加 `eslint-disable` | 禁用规则掩盖问题 |
| 恒假断言 | `expect(true).toBe(true)` 等 |
| 条件跳过弱化断言 | `if (cond) { expect(...) }` 前置不满足时无证据仍 PASS |
| 程序赋值冒充用户交互 | `.fill()` / `.setValue()` 冒充拖动 / 点击 / 输入 |
| 仅验证存在不验证行为 | `expect(element).toBeVisible()` 当作 AC 全部证据 |
| 纯存在性断言 | `expect(obj).toBeDefined()` 不验证 obj 内容 |

### 维度 4：红灯归因协议

测试红（失败）默认实现错。改测试必须有归因：

- (a) 实现 bug，只改实现 → 正常
- (b) 测试写错，写明错因（锁定文件需人工解锁，归因记 `decisions.md`）→ 本 agent 检查归因是否充分
- (c) 规格变了，走变更子流程 → 检查 spec 是否同步

无归因改测试 = critical，FAIL。

## 输出

复制 {task_dir} 的 `review.md` 模板（若不存在，从 `docs/templates/task/review.md` 复制），改名为 `review_test.md`，填字段：

- task：`{TID}_{slug}`
- spec：`{spec_path}`
- target：`working tree`
- reviewer_focus：`测试`
- reviewed_at：`YYYY-MM-DD HH:MM UTC+8`

### Findings 区

每条 finding：

- ID 格式：`{TID}_test_f001` 起步，递增
- 字段：严重度 / 位置（`file:line` 或测试名）/ 问题 / 建议

### AC 覆盖核对表

AC-1 / AC-2 / ... 每条状态：✅ 覆盖（引测试位置）/ ❌ 缺失 / ➕ 部分。

### 危险模式扫描结果

列出维度 3 表格的命中项（无则写"无"）。

### 结论段

最后一段必须是：

```
verdict: PASS
```

或

```
verdict: FAIL
```

PASS 门槛：无 critical / high finding，且无危险模式命中。

## 禁止

- 不读 spec 就下结论
- 不引 `file:line` 或测试名只下判断
- 改代码 / 改测试 / 改他人报告
- 重审覆盖历史（必须追加）
- 派 sub-sub-agent
- 命中危险模式降级为非 critical
```

---

## 派发示例

派发时填充占位符，例如：

| 占位符 | 示例值 |
| ------ | ------ |
| `{TID}` | T042 |
| `{slug}` | parser_fix |
| `{spec_path}` | `docs/tasks/T042_parser_fix/spec.md` |
| `{task_dir}` | `docs/tasks/T042_parser_fix` |

输出文件：

- Agent A → `docs/tasks/T042_parser_fix/review_code.md`
- Agent B → `docs/tasks/T042_parser_fix/review_test.md`
