违规模块与位置	现状与代码表现
_id_scan.py 历史单文件编号嗅探<br>_id_scan.py:8, 129, 133-157<br>factory_tests/test_pending.py:828-895	scan_max_id 为分配 pNNN/dNNN，遍历全部分支用 git show 读取旧版单文件总账（docs/pending.md、docs/findings.md），正则解析正文小节标题编号，防未合并旧分支重号。配套 5 个回归测试。
template_sync/workflow_migration.py<br>（整模块 518 行）<br>factory_tests/test_repo_sync.py 15+ 测试	500 余行复杂迁移引擎：同时兼容 spec.md + task.md、task.md + note.md 两代旧布局迁移到 progress.md；含 Markdown 围栏状态机、规范块动态提取与字符串替换（_replace_guide_blocks）、旧标题迁移（_rename_legacy_title）及未集成分支扫描阻断。
template_sync/state.py 旧路径兼容<br>state.py:15, 77-100, 108-145	保留 LEGACY_STATE_PATH（.agents/skills/repo-template-sync/sync_state.json），read_state() 回退读取旧路径，cmd_init 内置 _merge_legacy_state() 合并旧 state 字段。
template_sync 文本打补丁模块<br>conventions_migration.py（46 行）<br>specs_index_migration.py（78 行）	conventions_migration.py 正则替换消费仓 conventions.md 旧导入语句；specs_index_migration.py 专门裁剪历史 4 列（含空 supersedes）表格并删除旧说明句。
template_sync/skills_links.py 退役 Hook 清理<br>skills_links.py:83-95	同步时特判并检测消费仓 .claude/hooks/merge_guard.py 是否指向退役目标并执行 unlink()。
check_review_status.py 兼容参数<br>check_review_status.py:97, 106-108, 115	保留 --max-review-round，标注为「只读兼容参数；不能提高已持久化上限」，代码中留有大小校验与回退（args.max_review_round or limits['review_limit']）。
lifecycle.py 历史字段防御清理<br>lifecycle.py:73	cmd_add 创建 task 时执行 fm.pop("schedule_status", None)。
二、违背【拒绝妥协】：局部修补规避底层重构
违规领域与位置	现状与设计妥协
tasks_index.json 派生缓存入库<br>store.py:154-245<br>context.py:12-13<br>decision_log.md L1	状态权威下沉至 progress.md front matter，但仍将 tasks_index.json 作为派生缓存提交到 Git，并在所有写操作后强制同步重建；为缓解冲突拆分为活跃与归档两份；在脏改动门禁中特判豁免这两个 JSON 文件。
repo_task 模块级路径硬编码与局部参数补丁<br>context.py:8-20<br>repo_task/ 下 66 处 ctx.REPO_ROOT	context.py 将 REPO_ROOT 静态写死为模块文件路径推导值。当 repo_sync.py 从模板仓操作消费仓时路径错位。代码未重构上下文抽象，而是在 store.py 补 rebuild_index_at(root)，在 git_ops.py 补 root=None，在 monitoring.py 补 repo_root=None。
track_worktree.py 正则嗅探外挂<br>track_worktree.py:120-146, 166-174	为支持 statusline 显示当前 worktree，未在核心生命周期（start/finish）挂接明确事件钩子，而是通过 PostToolUse hook 截获终端命令，用 re.search(r"--tid[= ](\S+)", command) 正则模糊匹配命令行文本，并用 6 处 except: pass 吞异常。
Markdown 修饰符宽容匹配（Permissive Parsing）<br>repo_task/review.py:13-16, 27-35	REVIEW_SCOPE_RE、AC_ROW_RE 内充斥 _AC_MARK = r"[]"`，宽容匹配反引号、加粗、中英文冒号与逗号，容忍 reviewer 输出漂移。
三、违背【架构至上】：顶层设计纯粹度与边界割裂
1. 入口体系分裂（check_review_status.py vs task.py）
- 绝大多数生命周期与状态控制已收拢为 task.py 子命令，但 check_review_status.py 依然作为单立顶层脚本暴露在 .repo_template/scripts/ 下。纯粹架构应将其彻底收敛为 task.py review-status。
2. 多套 ID 分配与扫描体系割裂
- Task ID 由 repo_task/lifecycle.py 单独通过工作区目录递增分配（t001）。
- Pending ID (pNNN) / Findings ID (dNNN) 由顶层脚本 _id_scan.py 跨分支加锁扫描。
- Spikes ID (sNNN) 又是独立的目录型扫描。
- 四种仓库实体均为带号资产，底层却分裂为两套独立的分配与锁逻辑。
3. parse_front_matter 静默降级
- repo_task/review.py:83-89 重新包装 documents.parse_front_matter，捕获 (OSError, TaskDataError) 并静默返回 {}。在数据损坏时破坏 fail-closed 保证，隐藏底层异常。