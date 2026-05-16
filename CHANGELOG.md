# Changelog

## [0.2.0] - 2026-05-16

### Added
- **录音题启动窗口** — 脚本启动即显示所有录音题（听后转述/短文朗读/回答问题）参考答案，关闭窗口即停止脚本
- `collector.read`（短文朗读）题型答案加载
- `show_recording_answers_window()` — 深色主题 Catppuccin 风格 tkinter 窗口，后台线程运行
- `--log FILE` CLI 参数 — 将所有输出保存到日志文件
- GitHub Actions 自动构建 `.exe` — push tag 时触发 PyInstaller 打包
- `collector.picture` / `collector.dialogue` 题型答案加载（含 info.value 为空时的 fallback 链）
- Anti-cheat toString 伪装 — Hook 函数伪装为 native code，防止指纹检测
- `_js_escape()` 统一转义方法 — 安全处理 JS 注入字符串
- 回调钩子：`on_connect` / `on_question_answered` / `on_complete` / `on_error`（GUI 可用）
- `get_all_answers()` 公开方法 — 返回所有答案字典
- `show_answers()` 方法 — 打印答案清单
- `--show-answers` CLI 参数 — 不自动答题，仅显示答案
- `--json` CLI 参数 — 输出机器可读 JSON
- `run()` 返回结果字典，方便 GUI 程序化调用
- 模块可 import：`from ets_auto import ETSAutoAnswer`

### Changed
- 录音题处理从"逐题检测弹窗"改为"启动即展示所有答案窗口"，大幅简化逻辑
- `get_page_state()` 增加 `offsetHeight > 0` 可见性检查，过滤 Vue 幽灵 DOM
- `load_answers()` 填空题 "/" 分隔答案取第一项
- Pinia set_id 不匹配时自动回退到 URL 提取
- Bridge wrap 模式兼容 CEF 原生函数（作业/练习双模式）
- README SEO 优化 + 小白指南 + 竞品对比
- 文件结构：`src/auto/ets_auto.py`

### Removed
- `is_recording_page()` — 不再需要逐页检测录音题
- `show_recording_popup()` — 改为启动窗口
- `_handle_recording()` — 录音题不再阻塞主循环
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
