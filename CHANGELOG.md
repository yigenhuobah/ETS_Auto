# Changelog

## [0.3.1] - 2026-05-23

### Fixed
- **TeeOutput class 声明丢失** — `_run_loop` 方法末尾缺少 `class TeeOutput:` 声明，导致其 `__init__` 覆盖 `ETSAutoAnswer.__init__`，import 报 `OSError: [WinError 6]`
- **same_count 鬼畜连点 (ets_word_pk)** — same_count >= 5 时清空 last_title 导致跳过保护失效，改为 same_count=-10 冷却保留 last_title
- **__ets_recorded 内存泄漏 (ets_exam)** — Bridge 注入的记录数组只 push 不清理，长时间运行 OOM。加 length > 200 时 slice(-100) 水位线
- **toString 反爬指纹 (ets_exam)** — `fn.toString = ...` 赋值可被枚举检测。改为 `Object.defineProperty` + enumerable:false
- **路径斜杠混搭 (ets_exam)** — ets_base 用正斜杠拼接后 os.path.join 产出反斜杠。统一改用 os.path.join
- **GBK 终端 UnicodeEncodeError** — ecdict 含 IPA 音标，Windows 默认 GBK 崩溃。__main__ 入口强制 sys.stdout/stderr UTF-8
- **record_miss 全量重写 (ets_word_pk)** — 每次读-改-写整个 JSON，大文件 I/O 阻塞。改为 JSONL 追加 (O(1) disk I/O)

## [0.3.0] - 2026-05-23

### Added
- **模块拆分** — 提取 `ets_common.py` 共享基类，消除 exam/pk 重复代码
- **单词PK自动答题** — `ets_word_pk.py` v5，派生词生成+短语提取+四级匹配+自学习
- **统一入口** — `run.py exam|pk` 子命令式启动
- **双 EXE 构建** — GitHub Actions 同时构建 `ets_auto.exe`（套卷）和 `ets_pk.exe`（PK）
- ECDICT 字典补充（ecdict_pk.json）
- PK 自学习映射（pk_extra.json）+ 未命中记录（pk_misses.json）

### Changed
- `ets_auto.py` → `ets_exam.py`（继承 `ETSBase`）
- `eval_js()` 增加事件过滤（跳过 CDP 事件消息直到匹配响应）
- `connect()` 拆分为基类连接 + 子类 Pinia 读取
- `_js_escape()` → `ETSBase.js_escape()` 静态方法

### Removed
- `ets_auto.py` — 重命名为 `ets_exam.py`

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
- 回调钩子：`on_connect` / `on_question_answered` / `on_complete` / `on_error`
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
- 文件结构：`src/auto/ets_auto.py`

### Removed
- `is_recording_page()` — 不再需要逐页检测录音题
- `show_recording_popup()` — 改为启动窗口
- `_handle_recording()` — 录音题不再阻塞主循环

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
