# Changelog

## [0.6.8] - 2026-07-14

### Fixed
- **M-BRIDGE** — `inject_bridge` tracks wrap identity (`__ets_wrappedChoose/Blank`); if CEF later replaces the wrap with real `kttb_*` natives, the next inject **re-hooks** and captures them as orig (no nested wrap while ours still installed); `rehooked` only after prior hook
- **Pinia `ets_base`** — `constrain_ets_data_root()` realpath-jails under `%APPDATA%\\ETS`; always returns the ETS **root** (not a random subdir); rejects escape
- **Strategy miss fill** — `answer_choose` / `answer_fill` use `strategy.lookup` when `self.answers` misses a qid (was double-check only)

### Tests
- `TestConstrainEtsDataRoot` (incl. subdir snaps to root), bridge re-hook markers, `TestWordPKLearnMiss`, strategy miss → answer

## [0.6.7] - 2026-07-14

### Fixed / Hardened
- **CDP loopback only** — `is_loopback_ws_url()` on connect/reconnect (`127/8`, `::1`, `::ffff:127.0.0.1`, expanded IPv6)
- **`click_next` waiting** — `next_icon hidden` / `disabled` no longer falls through to main-frame `not found` (keeps `_is_next_waiting`)
- **Unsigned remote kill-switch** — without `ETS_REMOTE_HMAC`/`ETS_REMOTE_PUBKEY`, `allowStart:false` / `force_update` are **warn** (fail-open); signed mode still hard-blocks
- **`format_update_message`** — covers `warn` advisory text
- **Remote cache** — path via `user_data_path` (legacy beside-module fallback); atomic temp+replace; integrity reject logged; serialize errors do not abort `check()`
- **Console** — strategy MISMATCH prints use ASCII `[MISMATCH]` / `[FILL MISMATCH]` (GBK-safe)

### Changed
- **`compute_loop_thresholds()`** — pure helper for exam empty/unreachable caps
- **Version** — `APP_VERSION` / `info.json` → **0.6.7**

### Tests
- Remote classify unsigned/signed; loopback URL; click_next waiting; loop thresholds

## [0.6.6] - 2026-07-13

### Fixed
- **Exam reconnect 空答案 resume** — `set_id` 变化后 `load_answers()` 失败不再假成功；同卷答案表为空 fail-closed
- **page state 语义错误 vs CDP 失败** — 仅 `eval_js_failed` 等触发 reconnect；`no iframe`/`no doc` 仍作过渡页
- **页面/PK 状态静默 `{}`** — `parse_eval_json()`；choose/fill/wait_iframe/next 对齐错误形状
- **录音路径** — GUI 无嵌套 worker + `ready_event`；CLI zombie join；wait 重抛 ConnectionError
- **eval_js 可被停止打断** — recv 切片（1s）检查 `stop_event`
- **pk_extra 损坏** — status-first；禁止坏文件覆盖好 `.bak`；invalid 拒绝盲远程覆盖
- **GUI 关闭竞态** — `_closed`、stream restore 守卫；remote_info 主线程发布

### Changed
- **`ets_auto` 拆分** — `ets_recording_ui` / `ets_rw_mode` / `ets_tee` mixin；公开 import 兼容
- **共享壳** — `reconnect_control()`、`parse_eval_json()`、`is_cdp_parse_error()`
- **CI** — exam/GUI hidden-import 补新模块
- **删除** 死脚本 `_bug_check.py`

### Tests
- unit：**186+**（reconnect 壳、page/PK error、pk_extra bak、eval stop、GUI closed）
- pre_release：**58** 项

## [0.6.5] - 2026-07-12

### Docs & Tooling (2026-07-12)
- **`scripts/sync_to_auto.py`** — Project→Auto 白名单同步；默认 dry-run，`--apply` 写入；防误拷 docs/tools/探测脚本

### Docs & Tests (2026-07-12 补全)
- **文档对齐 0.6.5** — `CLAUDE.md` 版本单源；`src/auto/README` 重写；根 `README` 功能/FAQ/Roadmap/自检命令；`HANDOVER` 头+§3.3/3.4/录音结论；`docs/testing.md` + `docs/release.md` 新建；`deep_bug_audit` §8.1 状态刷新；`dev_log` 页首现状注记
- **`test_unit.py`** — APP_VERSION / user_data_path / stop_event / tab / PK 新词典 / fuzzy / RW rebuild / drop_connection；**另补** reconnect 控制流、录音 wait、词典路径选择、remote allowlist、GUI 进度公式；失败时 **exit≠0**
- **`pre_release_test.py`** — 版本/info.json、user_data_path、integrity API、PK stop_event、safe set_id、pick tab、js_escape（**58** 项）
- **CI** — `build-exe.yml` 在 pre_release 后增加 `python src/auto/tests/test_unit.py`

### Changed (low-risk simplify)
- **版本单源** — `APP_VERSION` 只在 `ets_common` 定义；`ets_auto` / `ets_word_pk` / `ets_gui` 再导出
- **RW 重连控制流** — `_handle_rw_reconnect()` 收口原 6 处复制粘贴
- **GUI 远程阻断** — `_remote_is_blocked` / `_apply_remote_block` 统一 start/回调/结束路径
- **用户数据路径单源** — `ets_common.user_data_path()`；PK/remote/stats 共用
- **PK 重连控制流** — `_handle_pk_reconnect()` 收口两处复制；finally 用 `_drop_connection()`

### Fixed (OPEN backlog — full pass)
- **OPEN-H1** 版本对齐 — `ets_auto` / `ets_word_pk` / `ets_gui` / `info.json` → **0.6.5**
- **OPEN-H2/M4** CLI PK `stop_event` — `run.py pk` 传 `Event()`；`ETSWordPK` 默认创建 Event
- **OPEN-H3** 多 tab 连接 — `_pick_ets_tab()` 优先 exam/homework/PK URL，不再盲目 `[0]`
- **OPEN-H4** 远程完整性（可选）— `verify_remote_payload_integrity()`；支持 `ETS_REMOTE_HMAC` / `ETS_REMOTE_PUBKEY`；未配置时保持 allowlist-only 兼容
- **OPEN-H5** reconnect 后立即 `inject_bridge()`
- **OPEN-H6** RW 重连重建答案 — `_rw_post_reconnect` + `_build_rw_answers_from_showdata`
- **OPEN-H7** 热键注册失败可观测 — 全失败则 `_registered=False` + 警告；`GetLastError` 日志
- **OPEN-H8** 移除 `HANDOVER.md` 明文账号/密码
- **OPEN-M1** 热键 print 改为 ASCII `[PAUSE]/[SKIP]/[STOP]`
- **OPEN-M3** `js_escape` 转义 U+2028/U+2029
- **OPEN-M5** PK `_fire_question` 增加 `answered` / `total_questions`
- **OPEN-M10** `tools/fix_*.py` 路径改为相对仓库，不再写死本机绝对路径
- **OPEN-M11** `ets_stats.json` 写到项目根/exe 旁，不再写入 `%APPDATA%\\ETS`

### Notes
- 真机项（完整套卷 E2E、作业提交、多 tab 实机）仍需人工验收
- 远程签名默认关闭（未设密钥时与 0.6.4 行为兼容）；生产加固请配置 `ETS_REMOTE_HMAC` 或 Ed25519 公钥

## [0.6.4] - 2026-07-08

### Fixed
- **单词PK无法启动（词典路径变更）** — ETS客户端更新后词典从 `pc_xst_dict/pc_xst_dict.json`（纯JSON）迁移到 `common/material/word/worddict_data.json`（JS变量+Base64编码），旧路径目录为空导致 `load_dictionary()` 直接返回False，PK启动后立即退出。新增 `_load_dict_new_format()` 解析新格式，`__init__` 自动检测新旧路径，完全向后兼容

## [0.6.3] - 2026-06-21

### Added
- **reconnect() 方法** — ETSBase 新增 CDP 重连机制，最多重试 3 次，每次间隔 2 秒，断线后可自动恢复
- **录音题检测与等待** — ets_auto 新增 `is_recording_page()` 和 `wait_for_recording_done()`，到达录音题时提示用户手动完成
- **GUI 进度条** — ets_gui 新增答题进度条和百分比标签，实时显示已完成/总数
- **set_id 兜底扫描** — 当 Pinia 和 URL 均无法获取 set_id 时，扫描 ETS 数据目录找最新套卷
- **RW 子题索引对齐** — 读写同步模式修复子题答案错位，按 qid 出现次数推进 letters 索引

### Fixed
- **Bug 1: GUI 模式录音窗口冲突** — `show_recording_answers_window` 在 GUI 模式下创建第二个 `tk.Tk()` 导致崩溃，改为检测已有 Tk root 并使用 `Toplevel` + 线程同步
- **Bug 3: SemVer 预发布版比较** — `compare_versions` 将 `0.5.1-beta` 与 `0.5.1` 视为相等，现按 SemVer 规范预发布 < 正式版
- **Bug 5: _set_cache 无淘汰** — ETSStrategy 类级缓存无上限，长时间运行内存泄漏，加 LRU 淘汰（上限 20 套）
- **Bug 7: HTML 正则误匹配** — `<[^>]+>` 会误匹配数学表达式 `x < y`，改为 `</?[a-zA-Z][^>]*>` 要求标签以字母开头
- **GUI 启动卡顿** — 移除 `_start` 中的 `_check_remote_async()` + `sleep(0.3)` 阻塞主线程 300ms 的问题
- **worker 线程竞态** — `_restore_streams` 在 worker 线程仍可能写 QueueWriter 时恢复 stdout，现先 `join(timeout=2)` 再恢复
- **GUI 错误状态检测** — worker 线程异常后状态栏仍显示"已完成"，现区分错误/停止/完成三种状态

### Changed
- **show_recording_answers_window 重构** — 拆分出 `_build_recording_window` 共享构建逻辑，CLI 用 `tk.Tk()` + mainloop，GUI 用 `Toplevel` + Event 同步
- **_html_to_text 安全正则** — ets_parser.py 和 ets_strategy.py 同步修正则模式

## [0.6.2] - 2026-06-19

### Added
- **回调钩子系统** — ETSBase 新增 on_connect/on_question/on_complete/on_error 回调，支持外部监听答题进度
- **force_utf8_stdio()** — ets_common 新增统一 UTF-8 输出设置函数，支持 line_buffering 参数
- **导出MD文件存在确认** — 桌面已存在同名文件时弹窗询问是否覆盖
- **导出成功反馈** — MD导出后弹窗显示保存路径
- **HTML图片嵌入** — 图片题在打印/预览HTML中嵌入base64图片，打印时可见
- **MD图片引用** — 图片题在导出Markdown中引用本地图片绝对路径
- **pre_release_test 扩展** — 新增 ets_browser_ui 导入、force_utf8_stdio、回调钩子测试，共20项

### Changed
- **UI 代码分离** — ets_parser.py 的浏览器 UI 代码提取到 ets_browser_ui.py，create_browser_tab 委托调用
- **UTF-8 去重** — ets_auto/ets_word_pk/ets_gui 中的重复 stdout reconfigure 代码统一为 force_utf8_stdio() 调用
- **并发控制** — ets_auto.py 的 `_run_loop` 添加 `threading.Event`，异常终止设 stop_event，正常完成不设
- **策略层缓存** — ETSStrategy 添加类级 `_set_cache`，`load_set()` 先查缓存避免重复加载

### Fixed
- **ets_remote 缓存防御** — _load_cache 添加 `isinstance(raw, dict)` 检查，防止缓存文件格式异常时崩溃

## [0.6.1] - 2026-06-15

### Added
- **dialogue 逐题参考答案** — load_answers() 不再将全文材料当作每题答案，改为从 `question[].std` 提取每道题的最短标准参考答案；新增 `q_answers` 字段 (`[{ask, answer}, ...]`)
- **答案类型标签** — show_answers() 区分 CHS/FIL/PIC/RD/DLG 五种标签，不再将 picture/read/dialogue 全部标为 `[FIL]`
- **顶层 import re** — 修复展示窗口中 `re.sub()` 缺少 re 模块导致的潜在 NameError

### Changed
- **HTML 标签统一清理** — 新增 `_strip_html()` 工具函数，保留段落换行（`</p><p>` → `\n`，`<br>` → `\n`），适用于 read/picture/dialogue 三种题型
- **展示窗口冗余清理移除** — show_recording_answers_window() 不再对已清理的 answer 重复执行 `re.sub` 去 HTML
- **docstring 位置修正** — load_answers() 的 docstring 从 `_strip_html` 定义后移回函数体开头

### Fixed
- **dialogue 答案错位** — 旧逻辑将全文 material value 当作每题答案，实际答案在 `question[].std[].value` 中
- **picture/read 答案含 HTML 残留** — `<p>`、`<br>` 标签未清理，现已统一通过 `_strip_html()` 处理
- **stdout 无输出** — Windows 下 Python 默认缓冲导致 print 不及时，`__main__` 入口添加 `line_buffering=True`
- **show_answers 类型标签** — picture 标为 `[FIL]`，dialogue 标为 `[FIL]`，read 标为 `[FIL]`，现已正确区分

## [0.6.0] - 2026-06-01

### Added
- **远程配置 ets_remote.py** — 版本比较(semantic versioning)、强制更新(minVer阈值)、远程开关(allowStart杀开关)、公告推送、pk_extra.json静默热更新
- **多源容灾** — 三级CDN回退：ghproxy → gitee → github，每源8s超时
- **GUI集成** — 启动时后台检查远程配置，版本过低或远程关闭时禁用开始按钮，有公告时日志提示
- **pk_extra.json 自动更新** — 检测到远程URL后后台静默下载覆盖，自动备份旧文件
- **本地缓存** — remote_info_cache.json 24小时缓存，断网时回退
- **ets_auto.py __version__** — 新增版本常量，与 GUI APP_VERSION 同步
- **info.json 示例** — GitHub仓库根目录配置文件模板

## [0.5.1] - 2026-05-31

### Fixed
- **ets_parser.py**: scan_sets() 读取 template res.json 获取分数和题型名称，按分数降序排列（大卷在前）；填空题题号优先读 xth 字段
- **ets_strategy.py**: _html_to_text() 中 `<br>`/`<p>` 替换为空格而非空字符串，防止单词粘连；_text_similarity() 从字符级 Jaccard 改为词级 SequenceMatcher，抵抗字谜盲区
- **ets_hotkey.py**: is_paused/should_skip/should_stop 属性加线程锁；消息泵启动前 PeekMessageW 强制初始化队列；GetMessage 返回 -1 时跳出循环
- **ets_auto.py**: 选择题 qid 提取改用正则 `/_\d+$/` 替代 split，修复 ID 含多个下划线时的截断问题；关闭弹窗时设置 stop_event 通知工作线程退出
- **ets_word_pk.py**: get_stems() 先做词干剥离再做英美拼写转换，避免 'organising'→'organizing'→'organiz' 错误路径；同题检测改用 title+options 的复合哈希，防止不同题目共享标题时误判

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
