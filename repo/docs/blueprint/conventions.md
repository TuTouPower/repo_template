# 约定（项目级）

模板流程的编号与 Markdown 格式见 `.repo_template/docs/conventions.md`。本文只写本项目的语言、框架与 schema 落点例外。

- 语言和框架已有稳定惯例时，在本文件补充项目级例外，不强行覆盖生态要求。

## schema 类型落点

按消费方决定落点，`schemas/` 只放跨服务契约。

|类型|例子|落点|
|---|---|---|
|跨服务接口契约|OpenAPI、gRPC `.proto`、GraphQL `.graphql`、AsyncAPI|`schemas/`，按协议分子目录：`schemas/openapi/`、`schemas/proto/`、`schemas/graphql/`；单一协议直接扁平|
|代码内数据契约|Pydantic model、TS interface、Zod schema、Go struct tag|跟模块走：`src/<module>/schemas/` 或语言惯例位置（`src/types/`、`src/models/`）|
|数据库 schema|Alembic、Prisma schema、SQL migration、Django migration|工具默认：`migrations/` / `prisma/` / `alembic/`，不另立目录|
|配置 schema|JSON Schema 校验 config、CI workflow schema、env schema|跟配置走：`config/schemas/`，或跟消费方|
|文档/元数据 schema|frontmatter、Cosmjs、yaml metadata 校验|`docs/schemas/`，或跟文档源|

原则：

- 跨服务契约会触发上下游同步，独立根目录便于发现和工具扫描。
- 代码内契约不外露，跟源码同源，避免双份维护。
- 数据库 schema 跟 migration 工具走，工具约定优先于本文件。
- 多种类型并存时，按主消费方归类；归属不清记入 `docs/blueprint/decisions.md`。
