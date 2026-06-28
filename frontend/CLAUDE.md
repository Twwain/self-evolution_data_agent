# frontend — 前端工程说明

## 技术栈

React 18 + TypeScript + Vite + Ant Design 5 + React Router 6

## 目录结构

```
src/
  api/              HTTP 服务层（axios）
  components/       共享组件
  context/          React Context
  pages/            页面组件
    ModelManagement/   模型配置管理（CRUD + 激活 + 协议选择）
  styles/           全局样式
  types/            公共类型
  utils/            工具函数
```

## 测试

- **框架**：Vitest + @testing-library/react（jsdom 环境）
- **命令**：`npm run test:unit`（单次）/ `npm run test:unit:watch`（监听）
- **覆盖率**：`npm run test:coverage`
- **覆盖率门**：lines ≥ 80%，functions ≥ 80%，branches ≥ 75%，statements ≥ 80%
- **测试文件**：`src/**/*.test.{ts,tsx}` 或 `src/__tests__/`

## 新增页面规范

新增 `src/pages/<PageName>/` 时必须：
1. 在 `src/App.tsx` 注册路由
2. 在 `src/components/Layout.tsx` 添加侧边栏菜单项
3. 补充对应的单元测试（至少覆盖核心业务逻辑分支）
4. 更新本文件的目录结构说明

## 关键约定

- API Key 等敏感字段：前端只展示脱敏值（`sk-****abcd`），测试时带 `id` 让后端从 DB 取真实值
- 模型协议（`protocol`）：`anthropic` provider 自动推导为 `"anthropic"`；`custom` provider 允许用户手选
- active Embedding 配置：编辑/删除/切换按钮前端禁用，最终由后端 409 兜底
