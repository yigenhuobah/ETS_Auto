# Changelog

## [0.5.0] - 2026-05-30

### Added
- **策略层 ets_strategy.py** — 复合key答案索引 (`{structure_type}_{stid}_{qid}`)，避免索引错位；三级回退链：精确匹配→模糊匹配(similarity>0.6)→DOM答案回退；支持 choose/fill/role/dialogue/picture/read 六种题型
- **全局热键 ets_hotkey.py** — Windows ctypes 零依赖实现，F9暂停/恢复、F10跳过当前题、F12紧急停止（+Alt变体防冲突）；后台线程消息泵
- **ets_auto.py 策略集成** — 初始化时创建 ETSStrategy+ETSHotkey 实例，主循环每次迭代检查热键信号，答题前先查策略层做双重验证(local+DOM)
- **stop_event 参数** — ETSAutoAnswer.__init__ 新增 stop_event，支持 GUI 外部停止信号

### Changed
- ets_auto.py 文件结构从4文件架构扩展为9文件架构
- 文件同步规则更新：ets_auto.py 两边同名（不再 rename 为 ets_exam.py）

## [0.4.3] - 2026-05-30

### Added
- **collector.dialogue 题型支持** — ets_parser.py 新增对话问答题型渲染（材料 + 口语问答 + 可接受答案变体），图标 💬，标签"对话问答"

### Fixed
- **答案 HTML 残留** — std.value 字段统一经 `_html_to_text()` 处理，消除 role/dialogue/picture 三种题型答案中的 `</br>` 残留

## [0.4.2] - 2026-05-29

### Changed
- **可中断 sleep** — `time.sleep()` 改为轮询式 `_interruptible_sleep()`，GUI 停止按钮可即时响应
- **GUI 清理** — 移除冗余 import，精简日志输出格式

## [0.4.1] - 2026-05-29

### Added
- **离线试卷浏览器** — `ets_parser.py`，扫描 %APPDATA%\ETS 本地缓存，渲染 choose/fill/role/picture/read 五种题型答案
- **GUI Tab 2 集成** — 在 GUI 中新增"试卷浏览"标签页，下拉选套卷→显示题目与答案

## [0.4.0] - 2026-05-28

### Added
- **GUI 图形界面** — `ets_gui.py`，CustomTkinter 实现，支持套卷答题/单词PK模式选择、CDP端口配置、实时日志输出、开始/停止控制
- **`run.py gui` 子命令** — `python run.py gui` 启动图形界面
- **CI 构建 ets_gui.exe** — GitHub Actions 新增 GUI 构建（--noconsole + --hidden-import=customtkinter）
- **customtkinter 依赖** — requirements.txt 新增 `customtkinter>=5.2`
- **PK 双向反向查找** — 反向查找同时匹配 pk_extra 的 key（中→英）和 value（英→中），修复中文题目匹配 en→cn 记录的盲区

### Changed
- CI workflow 新增 ets_gui.exe artifact 上传和 release 发布
- `run.py` 帮助信息更新，新增 gui 命令说明

## [0.3.4] - 2026-05-24

### Changed
- **许可证 MIT → GPL-3.0** — 开源许可证变更

### Added
- **复习模式检测 (ets_auto.py)** — 识别 ETS 复习/回顾模式并自动答题

## [0.3.3] - 2026-05-24

### Added
- **PK 模糊匹配 + 反向查找** — Strategy 0 增加 substring 模糊匹配和中文题目反向查找
- **same_count 冷却** — same_count 归零后重置 last_title 重新评估

### Changed
- README 中 ets_exam.py 引用更新为 ets_auto.py

## [0.3.2] - 2026-05-23

### Fixed
- **io.TextIOWrapper 重包装** — 改用 sys.stdout.reconfigure() 避免 rewrap 错误
- **TeeOutput 清理顺序** — 先恢复 stream 再关闭文件
- **on_connect 回调时机** — 移到 load_answers() 之后，total_questions 正确可用
- **run.py try/finally** — 主逻辑包裹 try/finally 保证安全清理

### Changed
- **ets_exam.py → ets_auto.py** — 重命名匹配 CI 构建目标
- **ets_common.py** — 改进 CDP 错误处理与超时

## [0.3.1] - 2026-05-23

### Fixed
- **TeeOutput class 声明丢失** — `_run_loop` 末尾缺少 `class TeeOutput:` 声明，导致 `__init__` 覆盖 `ETSAutoAnswer.__init__`
- **same_count 镜像连线 (ets_word_pk)** — same_count >= 5 时清空 last_title 导致跳过保护失效，改为 same_count=-10 冷却保留 last_title
- **__ets_recorded 内存泄漏 (ets_exam)** — Bridge 注入的记录数组只 push 不清理，长时间运行 OOM。加 length > 200 时 slice(-100) 水位线
- **toString 反爬指纹 (ets_exam)** — `fn.toString = ...` 赋值可被枚举检测。改为 `Object.defineProperty` + enumerable:false
- **路径斜杠混搭 (ets_exam)** — 用正斜杠拼接后 os.path.join 产出反斜杠。统一改用 os.path.join
- **GBK 终端 UnicodeEncodeError** — ecdict 含 IPA 音标，Windows 默认 GBK 崩溃。`__main__` 入口强制 sys.stdout/stderr UTF-8
- **record_miss 全量重写 (ets_word_pk)** — 每次读-改-写整个 JSON，大文件 I/O 阻塞。改为 JSONL 追加 (O(1) disk I/O)

## [0.3.0] - 2026-05-23

### Added
- **模块拆分** — 提取 `ets_common.py` 共享基类，消除 exam/pk 重复代码
- **单词PK自动答题** — `ets_word_pk.py` v5，派生词生成+短语提取+四级匹配+自学习
- **统一入口** — `run.py exam|pk` 子命令式启动
- **双 EXE 构建** — GitHub Actions 同时构建 `ets_auto.exe`（套卷）和 `ets_pk.exe`（PK）
- ECDICT 字典补充（ecdict_pk.json）
- PK 自学习映射（pk_extra.json）、未命中记录（pk_misses.jsonl）

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
- Anti-cheat toString 伪装 — Hook 函数伪装为 native code，防止指紋检测
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
- Bridge wrap 模式兼容 CEF 原生函数（作为练习双模式）
- 文件结构：`src/auto/ets_auto.py`

### Removed
- `is_recording_page()` — 不再需要逐题检测录音题
- `show_recording_popup()` — 改为启动窗口
- `_handle_recording()` — 录音题不再阻塞主循环

## [0.1.0] - 2026-05-10

### Added
- 选择题自动作答（setPCChoose2 API）
- 填空题自动填值（原生 setter + 框架绕过）
- 语音题自动跳过
- 本地 JSON 答案读取（零网络依赖）
- Pinia 动态路径寻址（无需硬编码）
- 作业/练习模式自动检测
- Iframe 重载轮询等待
- Section 过渡自动重试
- 自动结束检测（next disabled）
- "/" 分隔答案处理（如 Organise/Organize）
- Bridge wrap 模式（兼容 CEF 原生函数）
- `--debug` 调试模式
- 模拟练习 2 套卷 100% 验证通过（测试卷 20409 全部合格）