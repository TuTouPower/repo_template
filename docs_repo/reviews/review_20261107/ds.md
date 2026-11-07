1. 中高 baseline 解析重复实现且已分歧
render_review_prompts.py:163-188 硬编码分支名 main，而 git_ops.default_branch() 支持 origin/HEAD、init.defaultBranch、master 探测，store.load_task_backlog_baseline() 也走 default_branch()。默认分支非 main 的消费仓中，契约 drift 基线会退化为 diff_anchor，主干 backlog 权威读取失败，drift 检查静默跳过。另外这里用 "status: backlog" in stdout 子串判断，与 store 的 front matter 解析严格性不一致。建议复用 store 的解析逻辑，消除第二真相源。
2. 中 宽泛吞掉 TaskDataError
store.py:356-360 为兼容“新分支 tree 尚无 owner task 目录”而 catch 全部 TaskDataError。progress.md 损坏、front matter 非法、tid 不匹配等结构性错误也被静默跳过，该分支从 effective 视图消失、状态回落 backlog，掩盖数据损坏。建议区分“task 缺失”与其它数据错误（专用异常或带原因的返回）。
3. 中低 隐私门禁范围与文档声明不符
conventions.md:33 声明覆盖“已跟踪的非归档文本文件”，实际 pre-commit 只扫 staged *.md（hooks/pre-commit:33），finish 只扫 staged docs/ + 当前 task 目录（lifecycle.py:730-744）。src/*.py、根目录 .txt 等含家目录路径不会被拦截。收窄文档或扩大门禁。另 .scratch/ 排除在 pathspec -- docs 下是死条件。
4. 中低 伪冲突判定字段不全
integration.py:645 只比较 tid/slug/depends_on/conflicts_with 就认定“纯 backlog→done 冲突”，随后 checkout --theirs。主干上若更新了 review_level 等定义字段会被静默丢弃。建议比较除 status/diff_anchor/branch/worktree 外的完整定义。
5. 低 隐私正则覆盖缺口与策略不一致
privacy.py:10-13：
- POSIX 规则要求尾部 /，/Users/alice（行尾）、C:\Users\alice（无尾反斜杠）漏报。
- /home/root/... 因 CONTAINER_USERS 豁免，但 /root/... 拦截；同一身份两种策略。
- /root/.config/google-chrome 单点白名单过窄，其它容器固定路径会误报。
6. 低 其它
- _materialize_task_dir_from_ref 只处理 mode 100755，symlink（120000）会被写成普通文件；建议显式拒绝非常规类型。
- load_task_backlog_baseline:446-453 把 merge-base 非 0 一律当“非祖先”，并 except TaskDataError: pass 吞掉 primary 读取错误，git 故障被降级处理。
- _privacy_paths_to_check 与 _markdown_paths_to_check 重复执行同一 git diff --cached，可合并。
- 测试缺口：无“默认分支非 main”的 render baseline 用例；无“损坏 progress.md 分支不得被静默忽略”的负向用例。
建议：#1、#2 修复后再合并其余低优先级项；#3 至少修正文档声明。