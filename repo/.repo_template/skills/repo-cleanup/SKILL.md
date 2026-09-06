---
name: repo-cleanup
description: none
disable-model-invocation: true
---

# repo-cleanup

清理缓存、构建产物和无用运行文件。默认 dry-run；脚本参数和分类以 `repo_cleanup.py --help` 为准。

## 流程

1. 运行 dry-run，报告分类、路径、大小和被 keep 保护的内容。
2. 用户未指定范围时采用保守类别；不把源码、测试、配置、文档、数据或未知目录当垃圾。
3. 用户确认删除范围后执行 apply；再次报告已删、跳过和失败项。
4. 清理后运行 `git status --short`，确保没有误删 tracked 文件。

## 边界

- 不使用通配 `rm` 代替脚本保护。
- 不清 `.git`、`.repo_template`、docs 账本、`.env`、用户数据或未识别目录。
- 用户给出的 keep 始终优先。
