# Changelog

## [Unreleased]
### Added
- 深度竞品分析文档（docs/competitive_analysis.md，覆盖 8 个 PC + 1 个 Android 项目）
### Changed
- README SEO 优化（加入 e听说自动答题 / ETS答案提取 搜索高频词）
- README 题型名称具体化（听后选择1/2、听后记录、听后转述等）
### Removed
- HANDOVER.md（技术实现细节已移入竞品分析文档）

## [0.1.0] - 2026-05-10

### Added
- 选择题自动作答（setPCChoose2 API）
- 填空题自动填值（原生 setter + 框架绕过）
- 语音题自动跳过
- 本地 JSON 答案读取（零网络依赖）
- Pinia 动态路径读取（无需硬编码）
- 作业/练习模式自动检测
- Iframe 重载轮询等待
- Section 过渡自动重试
- 自动结束检测（next disabled）
- "/" 分隔答案处理（如 Organise/Organize）
- Bridge wrap 模式（兼容 CEF 原生函数）
- `--debug` 调试模式
- 模拟练习 2 套卷 100% 验证通过（测试卷 20409 全部合格）