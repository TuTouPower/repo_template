# Task 看板 Demo

用于演示任务 DAG、链式规划与批次交互的前端应用，仅供模板工厂开发参考，不随 `repo/` 复制到消费项目。

## 数据与边界

页面从 `src/lib/mockData.ts` 读取内置模拟数据；不是消费仓 `task.py view --serve` 的实时看板。界面上的规划和完成操作用于演示，不据此判定真实 task 状态或派发 agent。设计背景见 [执行计划](../plan.md)，现行工具链入口见 [消费仓用法](../../../repo/.repo_template/docs/usage.md)。

## 本地运行

前置条件：Node.js 与 npm。当前 `package-lock.json` 中 Vite 和 React 插件的 Node.js 要求为 `^20.19.0 || >=22.12.0`；安装前检查 `node --version`。依赖精确版本以 lockfile 为准，而非初始化记录 `info.md`。

从工厂仓根进入 Demo 后执行：

```bash
cd docs_repo/demos/app
npm ci
npm run dev -- --host 127.0.0.1
```

`npm ci` 会按 lockfile 重建本目录的 `node_modules/`，需要依赖下载源可达。安装或启动失败时停止，按报错检查 Node.js 版本、网络和依赖，不删除 lockfile 绕过问题；修复后重跑失败步骤。开发端口配置为 3000，实际地址以终端输出为准；用 Ctrl+C 停止服务。

## 检查与构建

在本目录执行：

```bash
npm run lint
npm run build
npm run preview -- --host 127.0.0.1
```

各步骤分别检查退出码，失败时停止后续步骤。`build` 先运行 TypeScript 构建检查，再由 Vite 输出到已忽略的 `dist/`；只在构建成功后启动预览。预览服务用 Ctrl+C 停止，生成文件不手工维护，修复源码后重新构建。

浏览器验证：页面可加载；切换数据集后 DAG 与链列表更新；选择任务可查看详情。构建通过不等于这些交互已验证，也不代表与真实 task 工具链完成集成。

## 实现入口

- `src/pages/Home.tsx`：页面状态、数据集切换与交互编排。
- `src/lib/mockData.ts`：模拟数据与异步取数。
- `src/lib/chainPlan.ts`、`src/lib/batchPlan.ts`：链操作与批次规划。
- `src/components/board/`：DAG、链列表与任务详情组件。
- `src/main.tsx`、`vite.config.ts`：HashRouter 与相对资源路径配置。
