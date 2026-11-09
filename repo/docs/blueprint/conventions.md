# 约定（项目级）

模板流程的编号与 Markdown 格式见 `.repo_template/docs/usage.md`「命名与格式」。本文只写本项目的语言、框架与 schema 落点例外。

- 语言和框架已有稳定惯例时，在本文件补充项目级例外，不强行覆盖生态要求。

## schema 类型落点

按消费方决定落点，`schemas/` 只放跨服务契约。

|类型|例子|落点|
|---|---|---|
|跨服务接口契约|OpenAPI、gRPC `.proto`、GraphQL `.graphql`、AsyncAPI|`schemas/`，按协议分子目录：`schemas/openapi/`、`schemas/proto/`、`schemas/graphql/`；单一协议直接扁平|
|代码内数据契约|Pydantic model、TS interface、Zod schema、Go struct tag|跟模块走：`src/<module>/schemas/` 或语言惯例位置（`src/types/`、`src/models/`）|
|数据库 schema|Alembic、Prisma schema、SQL migration、Django migration|工具默认：`migrations/` / `prisma/` / `alembic/`，不另立目录|
|配置 schema|JSON Schema 校验 config、CI workflow schema、env schema|跟配置走：`config/schemas/`，或跟消费方|
|文档/元数据 schema|front matter、YAML metadata 校验|`docs/schemas/`，或跟文档源|

原则：

- 跨服务契约会触发上下游同步，独立根目录便于发现和工具扫描。
- 代码内契约不外露，跟源码同源，避免双份维护。
- 数据库 schema 跟 migration 工具走，工具约定优先于本文件。
- 多种类型并存时，按主消费方归类；归属不清记入 `docs/blueprint/decisions.md`。

## 原子写

- 写权威状态数据（`progress.md` front matter、`docs/pending/`、review prompt 文件等）必须走 tmp 文件 + fsync + `os.replace` 原子写，防中断/掉电产生半写状态。派生 task 索引默认不落盘；需要机器可读导出时用 `task.py list --json` 输出到 stdout。
- 公共 helper：`from repo_task.documents import atomic_write_text`。

## 路径隐私

- 提交与合并门禁只阻断本次新增文本行中的开发者家目录绝对路径；使用仓库相对路径或 `<worktree>`。未修改历史行留给显式审计处理，不阻断无关交付。
- 交付比较器为 `repo_task.privacy.scan_privacy_changes`。finish 候选比较 HEAD 与 index，并覆盖本次将暂存的 task 文件；pre-commit 比较 HEAD 与 index；merge 比较第一父提交与目标 tree；tip 核对比较第一父提交与实际提交。提交消息作为本次新增文本单独扫描。review 报告作为完整新增证据扫描。
- 比较前先严格解码完整 blob，再按文本行计算差异。支持 UTF-8、UTF-16/32（含 BOM 与可判定的无 BOM 文本）、GBK；无法解码的 Git 二进制对象或含 NUL 二进制跳过，其他编码错误阻断。不按扩展名豁免文本；所有交付入口使用同一二进制策略。
- 全量审计命令为 `python3 .repo_template/scripts/task.py privacy-audit --ref HEAD`，在消费仓根执行；只读扫描非归档文本，发现泄漏退出 1。无泄漏输出 `privacy-audit=PASS`。全量 index/tree 库函数仅供显式审计，不能接入日常提交门禁。
- 非个人环境固定路径只能在 `repo_task/privacy.py` 的 `FIXED_PATH_EXCEPTIONS` 中登记有依据的完整路径，不按用户名或容器身份全局豁免。隐私范围仍排除 `docs/archive/` 与 `.scratch/`。
