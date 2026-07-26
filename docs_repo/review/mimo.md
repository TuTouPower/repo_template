# docs_repo 综合审阅报告

**审阅日期**：2026-07-27
**审阅范围**：`docs_repo/` 全部文件（6 份主文档 + 1 份归档 + 1 份既有审阅）
**审阅者**：mimo-v2-5-free

---

## 文件概览

| 文件 | 来源 | 核心贡献 |
|---|---|---|
| `11111.md` | 随笔 | 并发 task 索引冲突的初步观察 |
| `workflow_feedback.md` | omni_media 理论分析 | spec/plan 边界、双审分级、索引简化 |
| `workflow_record.md` | t071 事故复盘 | branch ≠ worktree 的血泪教训 |
| `workflow_retrospective_0.md` | t001-t007 复盘 | 7 条痛点 + 横向缺口根因 |
| `workflow_retrospective.md` | t041-t061 复盘 | merge 冲突灾难、plan 弃用、手写脚本破坏 |
| `workflow_session_analysis_2026-07.md` | omni_usage 实证 | 双审信噪比、context 溢出、TDD 违规 |
| `archive/workflow_skill_split_proposal.md` | 方案设计 | AGENTS.md + skill 拆分的完整提案 |
| `review/gemini.md` | Gemini 审阅 | 总结 + 补充建议 |

---

## 审阅意见

### 一、文档质量问题

#### 1. 交叉引用混乱，缺乏统一入口

6 份文档各自独立，无 README 或索引。读者要拼凑完整画面需要：
- `workflow_retrospective_0.md` 覆盖 t001-t007
- `workflow_feedback.md` 覆盖 t041-t055（理论）
- `workflow_retrospective.md` 覆盖 t041-t061（实战）
- `workflow_session_analysis_2026-07.md` 覆盖 t041-t121（实证）

四个文件对同一批 task（t041-t061）从不同角度写了三遍，但没有一篇说清"我覆盖哪些 task、其他文档覆盖哪些"。`workflow_session_analysis_2026-07.md` §末尾有一张对照表，但只覆盖新发现 vs 已知，没建立完整的时序索引。

**建议**：在 `docs_repo/` 顶部创建 `README.md`，以时间线为轴建立文档索引，标注每份文档覆盖的 task 范围、分析角度和结论状态（已采纳/待决策/已废弃）。

#### 2. 11111.md 属于临时草稿，应清理

5 行随笔，无结构、无时间、无来源。内容被后续文档覆盖（并发索引冲突 → `workflow_feedback.md` §4、`workflow_retrospective.md` §1）。保留无价值。

**建议**：将有效观察吸收进对应文档后删除，或归入 `archive/`。

#### 3. 改进建议重复度高

"双审分级"至少出现在 4 个文件中，每次措辞略有不同：
- `workflow_feedback.md`：C 项，按 risk_level 分级
- `workflow_retrospective_0.md`：建议 #3，复杂逻辑双审/基础设施单审
- `workflow_retrospective.md`：P1 项，full/single/none
- `workflow_session_analysis_2026-07.md`：§1 建议，reviewer 加 AC 硬阈值

同一个建议在四份文档里写了四遍，读者无法判断哪份是权威定义、哪份是早期草稿。

**建议**：所有已反复出现的改进建议只保留一份权威定义（放在 `workflow_feedback.md` 或新建 `workflow_decisions.md`），其他文档用链接引用。

---

### 二、分析质量评估

#### 1. 根因分析深度不均

**高质量**：
- `workflow_session_analysis_2026-07.md` §1 对"双审信噪比"的四层根因拆解（角色框架→信息不对称→finding 无界→同模型盲区）是整套文档中分析质量最高的段落。结论"换模型不解决问题，根因在 prompt + 上下文 + 阈值设计"有说服力。
- `workflow_retrospective_0.md` §根因 对"横向缺口不开 task 根治"的归因（scope 守过头 / goal 推进压力 / 未识别系统性）诚实且可操作。

**中等**：
- `workflow_record.md` 的事故复盘事实清晰，但止于"以后用 worktree"，未追问"为什么 agent 没在 Step 1 检查 worktree 需求"——这指向 skill 层的流程缺陷，不只是个人失误。

**薄弱**：
- `workflow_retrospective.md` §5（批量缩进破坏测试）归因为"手写 Python 脚本不能处理对齐空格"，但没追问：为什么 agent 选了手写脚本而非 eslint/prettier？是工具链缺位还是判断失误？根因停在技术层，没触及决策层。

#### 2. 数据使用不一致

- `workflow_retrospective_0.md` 给了精确数字（39 finding、28% 真 bug）。
- `workflow_session_analysis_2026-07.md` 给了精确数字（29% 首轮 PASS、490 已修/203 撤回/761 遗留）。
- `workflow_retrospective.md` 只给了"31 commit"和"343→370 passed"，缺乏 finding 级数据。

三个文件来自不同项目阶段，数据粒度不统一。如果要跨项目比较改进效果，需要统一指标定义（什么是"真 bug"、什么是"噪音 finding"、"遗留"的标准是什么）。

#### 3. 待决策项缺乏收敛机制

`workflow_feedback.md` §末尾列了 6 个待决策项，`workflow_retrospective.md` §末尾列了 P0-P3 优先级，`workflow_session_analysis_2026-07.md` §末尾列了 P0-P2 优先级——三套决策清单互不关联。项目需要一个统一的决策追踪机制，否则"待决策"永远是"待决策"。

---

### 三、内容评估

#### 1. 有价值的洞察

以下发现值得固化进项目规则：

| 发现 | 来源 | 价值 |
|---|---|---|
| spec 不写技术选型（版本号/库/目录），只写行为 AC | retrospective_0 §1 | 消除 spec 过时循环 |
| 横向系统性缺口立即开 task 根治，不在业务 task 打补丁 | retrospective_0 §根因 | 消除 ~60% 噪音 turn |
| branch ≠ worktree，多分支并行需 worktree 隔离 | record 全文 | 防止代码丢失 |
| tasks_index.json 是 merge 冲突灾难源 | retrospective §1 | 驱动 derived data 方案 |
| 双审 finding 必须锚 AC，禁止无界审查 | session_analysis §1 | 首轮 PASS 率从 30% 提升 |
| `/goal` hook 串行多 task 导致 context 溢出 | session_analysis §2 | 单会话 task 数硬上限 |
| TDD 顺序违规：改测试适配实现 | session_analysis §3 | 测试纪律硬约束 |

#### 2. 存疑或过时的观点

| 观点 | 来源 | 存疑原因 |
|---|---|---|
| "plan 模板拆三套（plan_code/doc/style）" | feedback §A | 引入三套模板增加维护成本，不如按 complexity 可选 |
| "一个 task 一个主题，N 个 commit" | feedback §B、retrospective §7 | 模糊了 task 的原子性定义，可能退化为"一个 task 什么都做" |
| "不切分支，所有 task 直接在 main 上做" | retrospective §P0 | 对长期多人项目不可行，短期单人项目可接受 |
| "AC 唯一源 spec.md，task.md 引用不复制" | retrospective_0 §7 | 方向正确，但 task.md 收尾报告需要部分 AC 原文做勾选标记，纯引用降低可读性 |

#### 3. 遗漏的问题

以下问题在文档中未被充分讨论：

**a. skill 拆分方案的实施风险**

`archive/workflow_skill_split_proposal.md` 设计完整，但文档内未讨论实施后可能的新问题：skill 加载时机依赖 agent 判断，如果 agent 误判场景不加载 skill，行为会退化。需要一个 fallback 机制或验证手段。

**b. review prompt 模板的版本管理**

`workflow_session_analysis_2026-07.md` 指出 review prompt 是根因之一，但 `docs/reviews/prompts/` 的管理方式未被讨论。prompt 改了会不会导致不同 task 用不同标准审查？需要版本化或至少有变更记录。

**c. agent 能力边界与工作流适配**

所有文档假设 agent 能力恒定，但实际 agent 在不同模型、不同 context 大小下表现差异显著。`session_analysis` 提到 19MB 会话 context 溢出，但没讨论：如果 context 窗口缩小（换模型或压缩），工作流哪些环节最先崩溃？

---

### 四、对 Gemini 审阅的补充与分歧

#### 补充

- Gemini 提出"复盘系列建立索引"，方向正确但建议过于轻描。这不只是索引问题——6 份文档的结论已经互相矛盾（如"一个 task 一个 commit"在 `workflow_feedback.md` 和 `workflow_retrospective.md` 中被不同方式否定），需要一次收敛性审阅，产出一份统一的"工作流 v2 规则清单"。

- Gemini 认可 Derived Data 方案（task 状态写在 task.md front-matter），这比方案 A（main 上统一执行 finish）更彻底，但忽略了 front-matter 格式本身也需要脚本解析——本质上只是把 JSON 冲突变成了 front-matter 冲突。真正无冲突的方案是 git branch 命名本身作为状态源（已完成的分支 archive 到特定命名模式），但实现复杂度高。

#### 分歧

- Gemini 总结说"极其优秀的工作流演进档案"。我的评价更审慎：文档质量参差不齐（`11111.md` vs `session_analysis`），重复内容多，缺乏收敛。作为"演进记录"是合格的，但作为"可执行的改进方案"还需要大幅收敛。

---

### 五、优先行动建议

| 优先级 | 行动 | 原因 |
|---|---|---|
| **立即** | 创建 `docs_repo/README.md`，建立时间线索引 + 文档状态标记 | 解决当前最大的可读性障碍 |
| **立即** | 删除或归档 `11111.md` | 临时草稿无保留价值 |
| **短期** | 合并改进建议，产出一份统一的"工作流 v2 改进清单" | 结束四份文档各自列待决策项的混乱状态 |
| **短期** | 将"双审分级"和"spec 不写技术选型"这两个最成熟的建议落盘到 AGENTS.md | 已被三个以上独立来源验证 |
| **中期** | 实施 `archive/workflow_skill_split_proposal.md` 的 skill 拆分 | 解决 AGENTS.md 职责过重问题 |
| **中期** | 建立 review prompt 版本管理 | 防止 prompt 变更导致审查标准漂移 |

---

## 总结

`docs_repo/` 积累了大量高质量的实战洞察，但文档之间缺乏组织、重复度高、结论未收敛。当前最大的问题不是"发现不够"，而是"发现太多但没有统一执行入口"。建议先做收敛（统一清单 + 优先级），再做落盘（修改 AGENTS.md 和 skill），避免继续生产新的复盘文档而不执行已有结论。
