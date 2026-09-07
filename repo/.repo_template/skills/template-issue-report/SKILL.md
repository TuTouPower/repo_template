---
name: template-issue-report
description: 将消费仓发现的模板工具链或约定问题写成脱敏 issue；只描述现象和期望，不替模板仓定位根因。
disable-model-invocation: true
---

# template-issue-report

把消费仓观察到的模板问题交给模板仓维护者。报告模板和脱敏要求是硬契约。

## 流程

1. 收集可复现事实：触发操作、实际结果、期望结果、复现步骤和涉及的 `.repo_template` 组成部分。只读复现或写 `.scratch/`。
2. 判断归属：模板分发的 skill/script/hook/template 或通用工作流约定属于模板问题；消费项目业务代码走正常 task。
3. 分类为 `bug` 或 `需求`。
4. 在 `.scratch/repo_template_issues/` 写本地完整版，使用既定报告模板。
5. 生成 `.issue.md` 脱敏版：去除用户名、绝对路径、仓库名、业务细节、内部 URL/IP 和所有 secret；模板相对路径可保留。
6. 从已确认的模板源确定 GitHub `<owner>/<repo>`，不要默认用消费仓 origin。核对 `gh` 可用且已登录、目标仓库与脱敏正文，查重后用 `gh issue create --repo <owner>/<repo> --title <问题摘要> --body-file <脱敏版路径>` 发布。目标无法确认或 GitHub 不可用则停止发布，报告两份本地文件供人工交接。成功须取得 issue 编号或地址；超时先查询是否已创建，避免重复发布。

## 报告结构

```markdown
# 模板仓问题报告

## 元信息
- 报告时间：ISO 8601 带时区
- 消费仓：本地版写真实信息；issue 版泛化
- 分类：bug | 需求
- 报告人视角：消费仓 agent

## 问题概述

## 现象
- 触发操作：
- 实际结果：
- 复现步骤：
- 涉及模板仓组成部分：

## 影响

## 期望行为

## 补充上下文
```

文件名为 `<YYYYMMDD>-<HHMMSS>.md` 和对应 `.issue.md`。一个无关问题一对文件，不 commit、不 push。报告只写现象和期望，不读模板实现、不提供根因或修复方案。
