# Changelog

## [Unreleased]
### Added
- 回调钩子：`on_connect` / `on_question_answered` / `on_complete` / `on_error`（GUI 可用）
- `get_all_answers()` 公开方法 — 返回所有答案字典
- `show_answers()` 方法 — 打印答案清单
- `--show-answers` CLI 参数 — 不自动答题，仅显示答案
- `--json` CLI 参数 — 输出机器可读 JSON
- `run()` 返回结果字典，方便 GUI 程序化调用
- 模块可 import：`from ets_v8 import ETSAutoAnswer`
### Changed
- README SEO 优化（加入 e听说自动答题 / ETS答案提取 搜索高频词）
- README 题型名称具体化（听后选择1/2、听后记录、听后转述等）
- 文件结构：`src/auto/ets_v8.py`
### Removed
- HANDOVER.md

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