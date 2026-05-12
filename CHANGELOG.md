# Changelog

## [Unreleased]
### Added
- 🎤 **录音题辅助** — 听后转述/回答问题自动弹窗显示参考答案（Catppuccin 深色风格）
- `--log FILE` CLI 参数 — 将所有输出保存到日志文件
- GitHub Actions 自动构建 `.exe` — push tag 时触发 PyInstaller 打包
- `is_recording_page()` 检测 + `show_recording_popup()` tkinter 弹窗
- `collector.picture` / `collector.dialogue` 题型答案加载
- 回调钩子：`on_connect` / `on_question_answered` / `on_complete` / `on_error`（GUI 可用）
- `get_all_answers()` 公开方法 — 返回所有答案字典
- `show_answers()` 方法 — 打印答案清单
- `--show-answers` CLI 参数 — 不自动答题，仅显示答案
- `--json` CLI 参数 — 输出机器可读 JSON
- `run()` 返回结果字典，方便 GUI 程序化调用
- 模块可 import：`from ets_auto import ETSAutoAnswer`
### Changed
- 录音题不再自动跳过 — 识别后弹窗展示答案，等待用户录音完成后继续
- README 指令更新（新 CLI 参数 + 录音题 FAQ）
- README SEO 优化（加入 e听说自动答题 / ETS答案提取 搜索高频词）
- 文件结构：`src/auto/ets_auto.py`
### Removed
- HANDOVER.md
- docs/competitive_analysis.md（内部参考，不发布）

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