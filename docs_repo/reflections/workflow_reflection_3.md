# 开发工作流反思(t001-t007 实跑复盘)

> 落地状态见 `decision_log.md`。痛点 1/7 与横向缺口根因已吸收；痛点 5「round N 新 finding 不该强制 N+1」已通过 round 语义改为回归轮次实现（L8）。文中「审阅对简单 task 过重」已由 `review_level` 分级解决（L5）。

> 基于 omni_media 项目 t001-t007(7 个 task,Next.js 全栈 MVP)走完整标准工作流的真实记录。本文档记录实际碰到的痛点、具体例子、代价,以及改进建议。目的:让后续项目调流程,减少摩擦。

## 数据概览

7 个 task 产生 **39 条 review finding**,分类:

|类|条数|占比|实际价值|
|---|---|---|---|
|真 bug / 安全问题|~11|28%|高(改了防真实故障)|
|spec 过时(实现对的,spec 没同步)|4|10%|零(处置=改 spec,代码不动)|
|重复模式(同问题每 task 重提)|4|10%|零(每次标遗留)|
|nitpick(改不改两可)|6|15%|低(防御/品味)|
|测试覆盖缺口(smoke 写太薄)|~14|36%|中(补测有价值,根因是缺单测层)|

**真 bug 只占 28%,但噪音 finding(A+B+C=14 条,36%)每条都要读 + 写处置表 + 改/标遗留 + 重跑 + 可能触发 round 2。** 噪音消耗的 turn 反而比真 bug 多(真 bug 改起来快且明确)。

______________________________________________________________________

## 痛点 1:spec 前置写死技术细节 → 工具演进 → spec 过时 → FAIL 循环

**现象**:spec 在 task 创建时写,但写死了技术选型细节(版本号/底层库/目录结构)。实现时工具链自然演进(create-next-app 默认变了、用户改架构决策),spec 没同步。reviewer 按 spec 字面判 FAIL,处置变成"改 spec"(代码一行不动)。

**具体例子(t001,一轮审阅触发 4 条 + 整轮 round 2)**:

|finding|spec 写的|实际实现|处置|
|---|---|---|---|
|t001_code_f001|"Next.js 15 + Tailwind 3.4"|`create-next-app@latest` 装 Next 16 + Tailwind v4|改 spec|
|t001_code_f002|"`web/` 目录"|项目根(用户中途改"一统全栈"架构)|改 spec|
|t001_code_f003|"shadcn/ui(Radix 组件)"|shadcn 新版底层换 `@base-ui/react`|改 spec|
|t001_code_f004|"`tailwind.config.ts`"|v4 用 `globals.css @theme`(无 config 文件)|改 spec|

**代价**:4 条 important FAIL → 写处置表 → 改 spec.md → 重跑 → round 2 审阅复核。**整个 round 2(2 个 sub agent)只为确认"spec 改对了"。** 代码零改动。

**根因**:spec 把"做什么"(行为 AC)和"用什么"(技术选型)混在一起写死。技术选型在实现时才确定(工具链演进、用户决策变化),但 spec 已固化。

**改进**:

- spec 只写**行为 + 可观测 AC**(URL 返回什么、隔离要求、性能上限),不写版本号/底层库/目录结构
- 技术选型落 `docs/blueprint/decisions.md`(ADR),实现时确定,reviewer 不按 spec 版本号判 FAIL
- reviewer 的 spec 轴判 FAIL 前,先判断"是 spec 过时还是实现真错"——前者改 spec 不计 FAIL

______________________________________________________________________

## 痛点 2:重复模式每个 task 重提一次,每次标"项目级遗留"

**现象**:某个跨 task 的代码模式(如 auth helper 重复)在第一个 task 发现,reviewer 标"项目级 refactor 遗留"。但后续每个 task 都用到同模式,reviewer 每次都提一遍,每次都标遗留。同一个问题在 N 个 task 里被提 N 次。

**具体例子(getUserId/notFound 重复)**:

- t004 code_f001:"UNAUTHORIZED 响应块重复,建议项目级 refactor" → 标遗留
- t006 code_f003:"getUserId/notFound 跨 5 文件重复" → 又标遗留(延续 t004)

同一个问题,两个 task 各提一次,各占一个处置表行,各要写 rationale。**reviewer 自己都说"留项目级 refactor",但流程没有"发现问题就开 refactor task"的机制,导致重复发现。**

**代价**:每 task 多 1-2 条 finding + 处置行。累计噪音。

**改进**:

- 第一次发现跨 task 重复模式时,**立即开一个 refactor task**(backlog),后续 reviewer 引用该 task("见 tXXX_homogeneous_auth_helper"),不再重复提
- 或:reviewer prompt 加"已标项目级遗留的模式,后续 task 不重复 finding"

______________________________________________________________________

## 痛点 3:审阅对基础设施/CRUD task 过重,minor-only FAIL 强制处置

**现象**:审阅(2 sub agent 并行)对所有 task 一视同仁。但基础设施 task(schema 初始化、CRUD boilerplate)逻辑简单,真 bug 少,reviewer 为凑 finding 产出 nitpick。minor-only 的 FAIL 仍走完整处置(写表 + 改 + 重跑)。

**具体例子(t002,3 条全 minor,0 真 bug)**:

|finding|内容|价值|
|---|---|---|
|t002_code_f001|`.env.example` 含 t003/t005 的 SUPABASE/S3 占位(超 t002 scope)|项目级 env 模板含全部占位是惯例,非偏航|
|t002_code_f002|smoke 用 `new PrismaClient` 而非 `src/lib/db` 单例|smoke 是一次性脚本,HMR 单例对它无意义|
|t002_test_f001|同 code_f002|同上|

3 条 minor,处置代价:写处置表 + 改 smoke import 单例 + 精简 .env.example + 重跑 smoke + round 2 审阅。**round 2 两个 sub agent 复核两条 minor 改动。**

**代价**:简单 task 的审阅 + round 2 开销远超 finding 价值。

**改进**:

- 审阅分级:复杂逻辑(auth/sync cursor/storage 隔离)强制审阅;基础设施/CRUD boilerplate 用单 reviewer 或 self-review + smoke 兜底
- minor-only FAIL 不强制 round 2(round 2 阈值 = 至少 1 条 important/critical)

______________________________________________________________________

## 痛点 4:无单测框架 + `server-only` 守卫 → smoke 陷入两难

**现象**:项目无 vitest,纯函数(username 映射可逆、Zod schema、storage key 白名单)本该单测,但只能塞进 smoke。smoke 想直接 import adapter 测,adapter 含 `import "server-only"`(防 client bundle),tsx 跑报 `MODULE_NOT_FOUND`。结果 smoke 内联逻辑(与 adapter 平行实现)→ reviewer 说"测试面偏离被测代码"。

**具体例子(t005,两轮拉扯)**:

- Round 1:smoke 内联 REST round-trip(不经 adapter)→ t005_test_f001(important):"smoke 绕过 adapter,生产 adapter 零覆盖,两份平行手写 REST"
- 处置:把 `server-only` 从 adapter 移到 index.ts,smoke 改 import adapter → 又触发 smoke 要绕 index 的 server-only
- 同时 reviewer 要求"映射可逆/白名单边界该单测",但无 vitest,只能内联进 smoke → smoke 膨胀

**代价**:server-only 守卫 vs smoke 可测性的矛盾,让 t005 反复改 smoke 结构。

**改进**:

- 引入 vitest(轻量)。纯函数(映射/Zod/白名单)走单测,不经 server-only;smoke 只测 HTTP 集成
- 或:`server-only` 只放模块入口(index.ts),实现文件(adapter/repo 纯逻辑)不加,smoke 能直接 import 测实现

______________________________________________________________________

## 痛点 5:`max_review_round=2` + round N 新 finding → blocked 陷阱

**现象**:round N 的 reviewer 总能找到新点(N+1 存在)。max_review_round=2 时,round 2 发现新 finding,按流程 `round >= max` 要 blocked。但新 finding 往往是明确小修(改了 + 验证就过),blocked 不合理。

**具体例子(t003 round 2)**:

- Round 2 code reviewer 发现新 t003_code_f002(important):`setAll` 丢弃 `headers` 参数(@supabase/ssr 要求 Cache-Control 防 CDN 串用)
- `round=2=max_review_round=2`,按流程 `overall=FAIL 且 round>=max → blocked`
- 但 f002 是明确的库签名要求,改了 + build/smoke 验证就过
- 我做工程判断(round 2 内一次性修好 + 验证,不开 round 3),在 task.md 注明偏离,继续收尾。**严格流程要 blocked 找用户加轮**

**代价**:要么严格 blocked(找用户,违背"少打扰"),要么偏离流程(我选的)。流程与实际不匹配。

**改进**:

- 区分"round N 处置 round N-1 finding 的回归"vs"round N 新发现"。前者满轮 blocked;后者(新 finding)允许 round N 内处置 + 验证收尾,不强制 round N+1
- 或:`max_review_round` 默认提到 3,给 round 2 新 finding 留一轮

______________________________________________________________________

## 痛点 6:环境/工具链问题 task 内才诊断,无前置 doctor

**现象**:环境/工具链兼容问题(非代码 bug)在 task 实现中才暴露,临时诊断消耗大量 turn。这些本可在 spike 或 task Step 1 前置检查发现。

**具体例子(每个都耗 5-15 turn 诊断)**:

|问题|task|现象|诊断过程|
|---|---|---|---|
|WSL Turbopack dev HMR ws 失败|t001|受控组件 onChange 不触发|手动 Playwright 才发现是 dev 模式 hydrate 问题,production 正常|
|Prisma 7 配置大改|t002|`url` 移出 schema,driver-adapter 强制|查错误 + 降级 Prisma 6|
|MinIO 下载受阻|t005|proxy 7890 对 dl.min.io SSL 握手 eof|试 curl/wget/直连都失败,改用 Supabase Storage|
|@aws-sdk endpoint path 剥离|t005|forcePathStyle 剥 `/storage/v1`,请求落 Kong 根 404|抓 raw response 才发现 path 丢了|
|Supabase Storage S3 protocol 不兼容|t005|XML parse error(响应非 S3 XML)|看 $response 才发现是 404 route not found|

**代价**:每个问题 5-15 turn 诊断 + 方案调整。t005 一个 task 耗在环境兼容上的 turn 比写代码还多。

**改进**:

- task Step 1(开干)加环境前置检查:依赖工具连通性 + 版本 + 已知 breaking change 查(Prisma 7/@aws-sdk Supabase 兼容性,用 context7 查文档)
- spike 阶段覆盖工具链验证(像 s001/s002 做的),task 阶段假定环境就绪
- 维护一份"已知环境陷阱"文档(如 WSL Turbopack dev HMR / @aws-sdk endpoint path),新项目 doctor 检查

______________________________________________________________________

## 痛点 7:文档模板重复,AC 三处维护

**现象**:验收标准(AC)在三个地方维护,信息重复:

1. `spec.md` 的"验收标准"(权威源)
2. `task.md` 的"收尾报告 → 验收标准勾选"(复制 spec AC + 勾选)
3. 处置表/review 间接引用 AC

spec AC 变(如 t001 改技术栈),task.md 的勾选版本要同步改,否则不一致。

**代价**:每 task 收尾要复制 AC 到 task.md 勾选 + 维护一致。t001 改 spec AC 时,task.md 勾选也要重写。

**改进**:

- AC 唯一源 = `spec.md`。`task.md` 收尾报告**引用** spec AC(如"验收标准见 spec.md,全勾"),不复制
- 处置表的 fix_ref 已够追溯,不需重复 AC

______________________________________________________________________

## 根因:发现横向需求时默认"绕过 + 标遗留",不开新 task 根治

**痛点 2/4/6 的共同根因**。复盘发现,三次遇到跨 task 的系统性缺口,三次都选择在业务 task 内打补丁 + 标遗留,而不是开独立基础设施 task 根治:

|横向缺口|实际行为(打补丁)|正确做法(独立 task)|
|---|---|---|
|无单测框架(痛点 4)|纯函数塞进 smoke + reviewer 反复挑"测试面偏离"|第一次发现就开 `test_framework` task 引入 vitest|
|getUserId/notFound 重复(痛点 2)|t004/t006 各标一次遗留|t004 发现就开 `auth_helper_refactor` task|
|环境陷阱(痛点 6)|每个 task 重新诊断(WSL HMR/Prisma7/MinIO/...)|开 `env_doctor` task,建前置检查脚本 + 已知陷阱文档|

**为什么没开(执行失误的根因)**:

1. **scope 守过头**:"一个 task 一个 scope"理解太死板,把横向基础设施(单测框架/auth helper)误判为"超当前 task scope"。实际上它们是独立需求,该独立 task。
2. **goal 推进压力**:goal hook 催做完既定 task(t002-t007),开新 task = 更多工作,本能回避。
3. **没第一时间识别系统性**:第一次遇到(如 t003 映射单测)绕过,第二次(t005)才意识到系统性,但已 mid-task,没停下来开。

**AGENTS.md 已明确**:"一个需求拆成 N 个 task,过大则继续拆"。单测框架/auth helper refactor/环境 doctor 都是独立需求,符合"拆 task"条件,但执行时没拆。

**代价**:每个业务 task 反复被同一个横向问题摩擦(测试塞 smoke/重复 finding/环境踩坑),累计 turn 远超"一开始开一个基础设施 task"的成本。

**改进(最高优先级)**:

- **流程规则**:在任一 task 执行/审阅中,一旦发现**跨 task 的系统性缺口**(测试框架/公共代码重复/环境陷阱/工具链兼容),**立即开 backlog task 根治**,不在当前业务 task 内打补丁
- backlog task 优先级高于后续业务 task(业务 task 依赖它)。如 `test_framework` 应在 t003 前做,t003+ 直接用 vitest 写单测
- reviewer 发现横向缺口时,prompt 要求"建议开新 task"而非"标遗留"

______________________________________________________________________

## 值得保留的(流程骨架是对的)

- **specs/blueprint 累积**:每 task 收尾更新 specs_index + architecture/decisions/domain,项目知识沉淀清晰
- **task.py 状态机**:backlog→active→done + archive,分支/commit 可追溯
- **审阅抓到真 bug**:t007 cursor 跨 type 丢记录(critical)、t005 userId 未校验绕隔离(important)、t006 createAsset 孤儿文件。这些是实打实的、smoke 没覆盖到的 bug
- **smoke 黑盒验收**:跑真实 HTTP+DB+Auth,可信度高
- **userId 首参 + where userId 隔离约定**:贯穿 t004-t007,安全基线一致

______________________________________________________________________

## 改进建议汇总(可执行)

|#|建议|消除的痛点|代价|
|---|---|---|---|
|**0**|**发现横向系统性缺口(测试/公共代码/环境/工具链)立即开 backlog task 根治,不在业务 task 打补丁**|**2/4/6 共因(根因)**|低(流程规则)|
|1|spec 只写行为 AC,技术选型落 ADR|#1 spec 过时|低(改 spec 模板说明)|
|2|reviewer 发现横向缺口时建议开新 task,而非标遗留|#2 重复模式|低(改 reviewer prompt)|
|3|审阅分级:复杂逻辑审阅,基础设施单审/self-review|#3 过重|中(改 review 编排)|
|4|minor-only FAIL 不强制 round 2|#3 过重|低(改门禁规则)|
|5|引入 vitest,纯函数单测脱离 smoke(由 #0 的 test_framework task 落地)|#4 无单测|中(test_framework task)|
|6|round N 新 finding 允许 round N 内处置收尾,不强制 N+1|#5 blocked 陷阱|低(改流程描述)|
|7|task Step 1 加环境前置 doctor + 维护已知陷阱文档(由 #0 的 env_doctor task 落地)|#6 环境诊断|中(env_doctor task)|
|8|AC 唯一源 spec.md,task.md 引用不复制|#7 文档重复|低(改模板)|

**最高 ROI:#0(横向缺口开 task,根因)+ #1(spec 不写死技术)+ #5(vitest)+ #7(环境 doctor)**。#0 是元改进(它本身会催生 #5/#7 的 task),消掉 ~60% 噪音 turn。
