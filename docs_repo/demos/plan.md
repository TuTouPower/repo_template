# Task 看板 v2 — 链式规划面板 执行计划

## 需求确认(与用户对齐)

- 面板用途:**调度决策辅助**。看 DAG → 推荐链 → 微调 → 用户自己在终端派发给 coding agent(面板不管 agent 执行)。
- 任务是 DAG(dep/conflict 边)。切链 = 把未完成任务划分成若干条内部串行的路径。
- **交叉点 = 链间依赖**:甲链中某任务依赖乙链中某任务。面板要识别并标记;语义是"先执行到交叉节点为止,完成后重新计算切链"。
- 切链/微调/交叉点检测全部**前端计算**,刷新即重算,不扩展后端契约、不落库。
- 视图:**左侧 DAG 图 + 右侧链列表联动**;done/dropped 弱化(默认隐藏/折叠)。
- mock 数据千级 task,done/dropped 占 ~85%,活跃前沿几十到一两百个,需包含链间交叉的典型案例。

## Stage 1 — 实现(单个 coder 子代理,分支 chain-plan)

在 v1 代码基础上重构:

- 左:DAG 图(分层布局,缩放平移,category 着色,链颜色,交叉节点标记,点击联动)
- 右:链列表(自动推荐 + 微调 + 重新计算)
- 切链推荐算法 + 交叉点检测(纯前端)
- 保留:summary 条、归档区、搜索过滤、详情侧栏、mock 生成器(调整以产生交叉场景)

## Stage 2 — 构建与交付

- 合并 chain-plan → final-build → npm run build → master
- website_version_manager build_version (static)
