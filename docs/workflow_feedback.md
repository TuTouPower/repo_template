# 工作流反思与改进建议

源自 2026-07 在 omni_media 项目用 my-review skill 跨 16 份报告做 adoption、批量创建 15 个 task（t041-t055）时的实际体验。本文件记录工作流设计中的结构性问题与改进方向，作为后续项目的参考。

## 工作流的核心假设

CLAUDE.md 定义的开发工作流基于五条核心假设：

1. **task 是独立可验证的最小单元**（"一个需求拆成 N 个 task，每个结果独立可验证，一个 task 一个 commit"）
2. **spec/plan 分离让"想清楚"和"做"分离**
3. **门禁（黑盒 5 轮 / 双审 2 轮）保证质量**
4. **索引（tasks_index.json / specs_index.md）保证可追溯**
5. **追加式记录（handoff.md / bugs.md）保留历史**

实际操作中，1、3、5 大体成立；2、4 暴露了结构性问题。

## 实际暴露的问题

### 1. spec 与 plan 的字段重叠（模板层设计缺陷）

CLAUDE.md 模板：
- `spec.md`：背景 / 范围 / 非范围 / 验收标准 / 依赖与约束
- `plan.md`：步骤与验证 / 风险与回退 / Finalization 时更新的 blueprint

实际写作时：
- "验收标准" ≈ "步骤与验证"（每条步骤对应一个验收）
- "依赖与约束" ≈ "风险与回退"（约束即风险）
- "范围" 已隐含 "步骤"

**根本原因**：模板没有区分两者的视角。
- spec 是**契约**（对 reviewer / 用户）——问"做完什么样"
- plan 应该是**设计**（对实施者）——问"具体怎么实现、文件级改动是什么、关键技术决策的细节是什么"

模板把 plan 的字段设计成 spec 的子集，结果 plan 退化为 spec 的副本。agent 写 plan 时要么重复 spec，要么强行往里塞内容造垃圾。

### 2. task 粒度"独立可验证 + 一个 commit"是模糊的

实际批量创建 task 时，多个 task 都明显超出"一个 commit"：
- 安全整改 task 实际包含 6 个独立修复点（DB / 限流 / HTML / 代理池 / 跳转 / 鉴权）
- 前端守卫 task 含 10+ 个独立前端问题
- 文档清理 task 含 10+ 个文档点

按"一个 task 一个 commit"原则，这些都应该继续拆。但拆到什么粒度没指引：
- 单文件单改动？（粒度太细，task 数量爆炸，spec/plan 模板成本被放大）
- 单功能主题？（"太大"问题不解决）
- 单 PR？（项目没有 PR 流程）

实际结果：要么 task 太大违反"一个 commit"，要么拆得太碎失去主题凝聚力。需要量化的可执行标准。

### 3. 双审对所有 task 一视同仁是形式主义

CLAUDE.md 双审机制（max_review_round=2，code reviewer + test reviewer 各一）对所有 task 同样要求。但实际：

- **纯文档 task**（改外部依赖表述 / 同步 spec / 清理元引用）：code reviewer 审什么？test reviewer 审什么？
- **风格统一 task**（缩进 / 命名 / ESLint）：双审重复 ESLint 已经能自动查的东西
- **LOW 项批量 task**（8 个独立小修复打包）：双审变成"清单核对"而非"质量评估"

双审对**关键路径代码**（计费、鉴权、并发、安全）有意义。对文档 / 风格 / 配置 task 是无效成本。工作流没有按 task 类型分级。

### 4. 索引文件是高成本同步点

tasks_index.json 必须由 task.py 维护（agent 禁止手改）。但：
- agent 直接 grep `docs/tasks/` 能得到同样信息
- task.py 自身存在 bug（finish/drop 三次落盘非原子，已发现）
- 索引的真正价值（快速查全局状态）被同步维护成本抵消

specs_index.md 类似——它要求"task 收尾时更新"，但实际 spec 改动比 task 收尾更频繁，索引经常滞后于 spec 内容。

### 5. 依赖关系分散无全局视图

N 个 task 各自在 spec 里写"建议在 tXXX 之后做"。实施时回答"现在能开干 tYYY 吗"需要读 N 个 spec 拼图。真正需要的是依赖图，不是分散的"建议"。

### 6. plan 的"风险与回退"对 agent 实施时几乎无用

agent 实施一个 task 时，影响决策的实际信息是：
- 当前代码状态
- 测试失败的错误信息
- spec 的验收标准

不是 plan 里预先写的"风险：代码可能错，回退：改回来"。预先写的风险要么是泛泛的废话，要么是基于不完整代码现状的猜测（实际遇到的往往是没预测到的）。

### 7. spec 的稳定性假设不成立

CLAUDE.md 自承"后置 task 的 spec/plan 随前置完成修订"——这承认 spec 会变。但流程里没有"修订 spec"的明确环节。实际开干后发现 spec 假设错了：
- 硬干 → 违背 spec 契约
- 改 spec → 没有专门的 commit 类型，混在代码 commit 里，难追溯

## 做得好的地方（保留）

工作流不是全错，以下设计应保留：

1. **specs driven 防止实施时范围漂移**：spec 在前确实让"做什么"先于"怎么做"清晰
2. **门禁上限防止无限循环**：5 轮黑盒 / 2 轮双审是合理的硬上限
3. **blocked 机制明确"何时问用户"**：避免 agent 卡死或自作主张推进
4. **spike / task 二分**：技术选型（spike）和需求实现（task）解耦，干净
5. **AGENTS.md 文档规范**（禁止元引用 / 单一权威定义 / 禁止元 ticket 编号嵌入正文）：文档质量确实更高
6. **追加式 handoff**：保留时间线，新 agent 能续接，历史决策可追溯
7. **task.py 自动化**：降低状态管理心智负担（虽然有 bug 但方向对）

## 改进建议（按收益排序）

### 高收益

#### A. plan 模板按 task 类型分级

把单一的 `plan.md` 模板拆三套：

**`plan_code.md`**（适用于代码 task）：
- 文件级改动清单（动哪个文件、新增还是修改、大致行数）
- 关键技术决策细节（不是 A/B/C 的方案选型——那属于 spec 待决定项——而是 A 路线下具体的实现路径，如"用 try/catch 还是 Result 类型"、"JSON5 选哪个 npm 包"）
- 实施顺序（多文件改动时的先后，含中间状态设计）
- 测试策略（mock 哪一层、用什么 fixture、断言什么）

**`plan_doc.md`**（适用于文档 task）：
- 受影响文档清单
- grep 自洽检查项（如"`grep TikHub` 应返回 0 处"）
- 不重复 spec 已有的"范围"

**`plan_style.md`**（适用于风格 / 配置 / lint task）：
- 实施顺序（避免文件重命名引发 import 失败的连锁）
- 自动化验证命令
- 兼容性影响面（如重命名是否触及外部 API）

强制区分能让 plan 不再是 spec 副本，agent 实施时真正有参考价值。

#### B. task 粒度量化

把"一个 task 一个 commit"改为"**一个 task 一个主题，N 个 commit**"：

- **task** = 一组逻辑相关的改动（如"安全整改"是一个 task，包含 DB / 限流 / HTML 等多个子项）
- **commit** = task 内的原子提交（DB migration 一个、限流一个、HTML 解析一个）
- **spec 验收** = task 级（所有子项做完才算 done）
- **review** = commit 级（每个 commit 独立 review）或 task 级（最后一次总 review，二选一）

明确写入 CLAUDE.md，让"拆 task 还是拆 commit"有判断依据。

#### C. 双审按 task 风险等级

| Task 类型 | 流程 |
|---|---|
| CRITICAL / HIGH 资金 / 安全 / 并发 | 双审 + e2e |
| MEDIUM 代码 | 单审（code 或 test 二选一）+ 单测 |
| LOW / 文档 / 风格 / 配置 | lint 通过 + 自验收（无 reviewer） |

减少无效成本，把审阅精力集中在风险高的地方。task spec 加 `risk_level` 字段，task.py 根据 risk_level 提示对应流程。

### 中收益

#### D. 依赖图集中化

`tasks_index.json` 加 `depends_on: [tids]` 字段：
- task.py 启动时校验前置 task 状态（未 done 则提示）
- `scripts/task.py deps` 输出依赖图（命令行 dot / mermaid）
- spec 里的"建议在前置之后做"全部迁移到该字段

比"分散在 spec 里写建议"有用得多。

#### E. 简化索引

- `tasks_index.json` 只在 archive 时写（已完成 task 的历史）
- 当前 task 状态从 git 分支名读（`t041_*` 分支存在 = active）
- 减少同步成本

或保留 `tasks_index.json` 但去掉"必须 task.py 维护"硬约束，允许 agent 直接编辑（task.py 改为 helper 而非唯一权威）。

#### F. spec 修订 commit 类型

加 `chore(spec): revise <tid> <reason>` 作为合法 commit 类型。开干后发现 spec 假设错了必须先改 spec，不能直接改代码。让 spec 的动态修订有可追溯的轨迹。

### 低收益（可选）

#### G. plan 的"风险与回退"改为"已知坑位"

不是泛泛风险（"代码可能错"），而是基于代码现状的具体坑：
- "prisma findFirst 在 cursor 非法时抛 P2009，需 try/catch"
- "next 16.x 的 Route Handler formData() 在 chunked 请求下缓冲行为未确认，需实测"

这要求 plan 作者实际读过相关代码，不是凭空写。

#### H. blocked 决策模板

给用户提供结构化决策模板：
- 业务影响（不修会怎样）
- 回退成本（修一半回滚要做什么）
- 加轮收益（多花一轮预期能解决什么）

而不是"加轮 or dropped"二选一。降低用户判断负担。

## spec/plan 边界的再评估（2026-07 补充）

> 基于 omni_usage 会话实证（`workflow_session_analysis_2026-07.md`）对 §1 与改进建议 A 的发展，非否定。

§1 已诊断"模板按 WHAT/HOW 切导致字段重叠"，改进建议 A 的方向是 plan 模板分级、承载真正的设计细节。omni_usage 会话分析暴露了另一条线索：**双审 reviewer 只读 spec 不读 plan**，但 reviewer 最需要的"决策上下文"（哪些分支有意不测）和"测试策略"（哪些 AC 不可单测）当前写在 plan 里——造成 reviewer 信息不对称，撤回率与遗留堆积（详见 session 分析报告 §1 根因 b/c）。

这指向比 §1 更根本的划分原则：**按读者切，不按字段语义切**。

### 两个正交维度

1. **读者**：spec 给 reviewer / 后置 task / finalization（他人）；plan 给 agent 自己
2. **稳定性**：spec 是契约（AC 定了不该动）；plan 是工作笔记（随实现调整）

### 按读者重新归位

| 信息 | 读者 | 稳定性 | 归属 |
|---|---|---|---|
| 范围 / AC | reviewer | 契约 | spec |
| 可测试性声明 | reviewer | 契约 | spec（新增） |
| 决策上下文（有意不测的分支） | reviewer | 契约 | spec（从 plan 收编） |
| 未知契约清单 | reviewer / 用户 | 契约 | spec（新增） |
| 测试策略 | reviewer | 半稳定 | spec（从 plan 收编） |
| 风险与回退 | agent / 处置 | 易变 | plan（简单 task 可省） |
| 文件级实现步骤 | agent | 易变 | plan（可省，agent 现场定） |
| 技术决策细节 | agent | 易变 | plan（改进建议 A 的核心） |

净效果：**spec 吸收所有"对他人稳定可见"的信息，plan 退化为"给 agent 自己的易变笔记"**。简单 task 可不要 plan，agent 拿强 spec 现场定 HOW——对应会话分析中"agent 执行时自行决定"的方向。

### 与改进建议 A 的关系

- 改进建议 A（plan 分三套）解决"plan 当 spec 副本"——让 plan 承载真正的设计细节
- 本节解决"reviewer 读不到决策依据"——把 reviewer 要看的部分前移到 spec

两者部分冲突：**测试策略归 spec 还是归 plan_code**？判断归 spec——reviewer 判 finding 时需核对"这个不测是有意还是漏测"，测试策略是其锚点；agent 实施时按 spec 测试策略写测试，不自立一套。plan_code 只保留 agent 私有的实施顺序与技术决策细节。

### 合并的风险与对策

把决策上下文 / 测试策略并入 spec 的唯一真实风险：AC（契约区）被实现细节（上下文区）污染，reviewer 难定位。

对策：spec 内部分区——
- **契约区**：范围 / 非范围 / 验收标准（含可测试性声明）
- **上下文区**：决策依据 / 测试策略 / 未知契约清单

reviewer 提示词明确"判 AC 时只看契约区；判测试覆盖时核对上下文区"。

## 一个根本性的观察

当前工作流设计**偏向"防止 agent 犯错"**（门禁 / 双审 / blocked / 索引同步），但**对"agent 的实施效率"关注不够**（plan 退化为副本 / 双审无差别 / 依赖分散）。

对一个不信任 agent 的随机调用者，这套流程合理。对长期使用的私人 agent，可以更信任一些——把流程成本转移到真正需要把关的地方（CRITICAL 安全 / 资金 / 并发），其他地方放手。

具体到 omni_media 的 15 个新 task：
- t041 / t043 / t044 / t045 / t046 / t047 真正需要双审 + e2e（安全 / 资金 / 关键前端 / e2e 基础设施）
- t048-t055 走简化流程（lint + spot check）就够

## 待决策项

实施上述改进前需明确：

1. **plan 模板分级**：是否接受三套模板？还是保留单模板但重写字段定义？
2. **task 粒度**：从"一个 task 一个 commit"改为"一个 task 一个主题，N 个 commit"，是否接受？
3. **双审分级**：是否接受按 risk_level 分级？risk_level 由谁定（agent 推断 / 用户指定）？
4. **索引简化**：tasks_index.json 是保留 task.py 唯一权威，还是改为 archive-only？
5. **依赖图**：是否引入 depends_on 字段？
6. **spec/plan 边界**：接受"按读者重划"——spec 收编决策上下文 + 测试策略 + 未知契约清单，plan 只留 agent 私有实施细节（plan_code 收窄）？还是保持改进建议 A（plan 承载测试策略）？

建议先在一两个新 task 上试点 A/B/C 三项（plan 分级 / task 粒度 / 双审分级），观察效果再决定是否全量推行。
