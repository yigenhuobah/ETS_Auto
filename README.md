
# 🎯 ETS Auto — e听说 PC 端自动答题工具

[![License: GPL v3](https://img.shields.io/badge/License-GPL%20v3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/)
[![Windows](https://img.shields.io/badge/Platform-Windows-0078D6.svg)]()
[![ETS v5.7.8](https://img.shields.io/badge/ETS-v5.7.8-success.svg)](https://www.ets100.com/home/index.html)

> 🚀 **e听说自动答题 / 单词PK自动答题 / 离线试卷浏览器** · 听后选择零秒作答 · 听后记录自动填词 · 单词PK 85%+ 命中率 · 离线浏览所有缓存试卷 · 无需管理员权限 · 不篡改客户端 · 纯本地零网络

**每天被 e 听说作业折磨？** 这个工具能自动完成 PC 端的听说选择题和填空题，还能自动答单词 PK，更内置离线试卷浏览器让你提前预览所有答案。遇到语音题自动停住等你开口读。不修改客户端、不篡改分数、不依赖网络，是目前 ETS 环境下最**合规安全**的自动答题方案。

---

## ✨ 功能特性

- ✅ **套卷自动答题** — 选择题自动点击 + 填空题自动填值，听后选择/听后记录通杀
- ✅ **单词PK自动答题** — 四级匹配策略 + 派生词生成 + 双向自学习，85%+ 命中率
- ✅ **录音题答案展示** — 听后转述/回答问题/短文朗读自动弹出参考答案窗口，dialogue 逐题显示问答对
- ✅ **📚 离线试卷浏览器** — 扫描本地缓存，选择题标红正确答案、填空题展示标准答案、口语题列出所有可接受回答
- ✅ **🖥️ 图形界面** — CustomTkinter GUI，选模式→输端口→点开始，小白友好
- ✅ **零网络依赖** — 答案从本地缓存直接提取，无需抓包联网
- ✅ **双模式兼容** — 模拟练习 & 作业模式自动检测，无缝切换
- ✅ **跨题型自动导航** — 选择→填空 Section 过渡自动等待重试
- ✅ **断连自动重连** — CDP WebSocket 断开后自动 reconnect（套卷/PK/RW）
- ✅ **录音页等待** — 到达录音题提示手动完成，提交后自动继续
- ✅ **全局热键** — F9 暂停 / F10 跳过 / F12 停止
- ✅ **GUI 进度条** — 实时显示已完成/总数
- ✅ **远程配置（可选网络）** — 版本检查/杀开关/公告；**答案仍纯本地**，不上传题目

---

## 🐣 小白指南（完全不懂代码也能用）

### 推荐方式：直接使用打包版 (免安装任何环境)
1. 前往本仓库的 [Releases 页面](https://github.com/yigenhuobah/ETS_Auto/releases/latest)。
2. 在 `Assets` 列表中下载你需要的 exe：
   - `ets_auto.exe` — 套卷答题（命令行）
   - `ets_pk.exe` — 单词PK（命令行）
   - `ets_gui.exe` — 图形界面（集成套卷/PK/离线浏览器）
3. 打开 ETS PC 客户端，登录并进入答题页面（套卷进题目页，PK 进 PK 匹配页）。
4. 双击运行对应的 exe，享受喝茶的时光。

### 图形界面使用
运行 `ets_gui.exe` 后：
1. **Tab 1 套卷/PK**：选择模式、输入 CDP 端口，点击 🚀 开始
2. **Tab 3 📚 离线试卷浏览器**：左侧选择试卷，右侧逐节查看题目和答案

---

### ❓ 常见问题 (FAQ)

* **Q: 提示 "No ETS tab found" 怎么办？**
A: ETS 客户端未运行，或尚未进入答题页面。请先在 ETS 中点开具体的题目页，再运行脚本。
* **Q: 提示 "Failed to load answers" 是什么意思？**
A: 你还没有打开过这套题。ETS 需要先加载一次题目页面，答案才会缓存到本地。去客户端里看一眼题目再回来运行即可。
* **Q: 脚本一直卡住不动了**
A: 在终端里按 `Ctrl + C` 强制终止，然后加上 `--debug` 参数重新运行，查看详细报错信息：
`python src/auto/run.py exam --debug`
* **Q: 语音录音题怎么处理？**
A: 脚本启动时会自动弹出参考答案窗口（听后转述/回答问题/短文朗读），你可以边看答案边录音。选择题和填空题做完后，关闭答案窗口即可停止脚本。
* **Q: 单词 PK 怎么用？**
A: 运行 `python run.py pk` 或 `ets_pk.exe`，然后进入 ETS 的单词 PK 匹配页面即可。脚本会自动抓取题目并从本地字典匹配答案，答错的会自动学习，命中率越来越高。
* **Q: 离线试卷浏览器看不到任何试卷？**
A: 浏览器读取 `%APPDATA%\ETS` 目录下的缓存。需要先在 ETS 客户端中打开过至少一次作业，系统才会缓存试卷数据。
* **Q: 热键怎么用？**
A: 运行中全局有效：**F9** 暂停/继续，**F10** 跳过当前题，**F12** 紧急停止（断开 CDP）。命令行与 GUI 均可。
* **Q: 不是说「零网络」吗，为什么还会联网？**
A: **答题答案始终读本地缓存**，不抓包、不上传题目。可选联网仅用于检查 `info.json`（版本/杀开关/公告）和热更 `pk_extra.json`；可断网使用。

---

## 💻 快速开始（致开发者）

### 环境与前置要求

* Python 3.12+
* Windows 10 / 11 操作系统（更早的系统未经测试）
* 处于运行状态的 ETS PC 客户端 (开启了 CDP 调试端口 `10086`)

### 部署与运行

```bash
# 1. 克隆并安装依赖
git clone https://github.com/yigenhuobah/ETS_Auto.git
cd ETS_Auto
pip install -r requirements.txt

# 2. 运行图形界面 (推荐)
python src/auto/ets_gui.py

# 3. 运行套卷答题 (请确保已在客户端进入答题页)
python src/auto/run.py exam                # 默认模式（静默极简输出）
python src/auto/run.py exam --debug        # 调试模式（输出 CDP 交互日志）
python src/auto/run.py exam --max 200      # 设定安全步数上限
python src/auto/run.py exam --log run.log  # 保存日志
python src/auto/run.py exam --show-answers # 仅查看答案
python src/auto/run.py exam --json         # JSON 格式输出

# 4. 运行单词PK (请确保已在客户端进入PK匹配页)
python src/auto/run.py pk                  # 默认模式
python src/auto/run.py pk --debug          # 调试模式
python src/auto/run.py pk --max 50         # 限制题数

# 5. 离线试卷浏览器 (独立运行)
python src/auto/ets_parser.py

# 或者直接运行子模块
python src/auto/ets_auto.py --debug        # 套卷答题
python src/auto/ets_word_pk.py --debug     # 单词PK

# 6. 发版前 / 开发自检（无需 ETS 客户端）
python pre_release_test.py
python src/auto/tests/test_unit.py
```

---

## 📊 运行效果展示

```text
# ── 套卷答题 ──
ETS Auto v0.6.5
========================================
ETS connected
Loaded 21 answers (set_id=721920)
Mode: PRACTICE | Questions: 21
Recording answers: 3 types available
----------------------------------------
  [CHS] 787404_1 → B
  [CHS] 787405_1 → C
  ...
  [FIL] 787413_1 → Recycle
  [FIL] 787413_2 → slightly
  [PIC] 787414 → Hello, everyone! I'm Sam Smith...
  [RD]  787415 → Upcycling is the practice of...
  [DLG] 787416 → Q1→Upcycling is... / Q2→Because it...
========================================
Done: 14 choose + 4 fill = 18 answered
Coverage: 18/21 (86%)

# ── 单词PK ──
ETS Word PK Auto v5 (Derivatives + Phrases)
=============================================
Dictionary: 4200 base + 800 ecdict + 120 deriv + 45 compound + 30 extra = 5195 total
---------------------------------------------
  #3/15 -> besides [dict]
  #4/15 -> deliberately [learned]
  ...
=============================================
Done: 13 hit / 15 total = 87% | 2 miss | 0 err | 3 learned

# ── 离线试卷浏览器 ──
扫描 %APPDATA%\ETS → 发现 18 套试卷
试卷 398810 (9题)  📝 🗣️ 🖼️ 📖
  ━━ 📝 选择题 ━━
  【题1】1. Who did Lucy think invented the light bulb?
     A. Emerson.
     B. Edison.
  ✅ C. Einstein.
  正确答案：C

  ━━ ✏️ 填空题 ━━
  【第16空】parents
  【第17空】March
  【第18空】36000/36,000/thirty-six thousand

  ━━ 💬 对话问答 ━━
  Q1: What is upcycling?
  参考答案: Upcycling is the practice of transforming waste materials...
  Q2: Why is it important?
  参考答案: Because it reduces waste and creates something new...

```

---

## ⚙️ 工作原理剖析

本项目基于 Chrome DevTools Protocol (CDP) 的底层协议注入：

```text
┌──────────────────────────────────┐
│     ETS PC 客户端 (CEF + DOM)    │
│  - 主框架: Vue 3 + Pinia         │
│  - iframe: Vue 1.x 题目渲染      │
│  - CDP 端口: 10086               │
└──────────┬───────────────────────┘
           │ Chrome DevTools Protocol (WebSocket)
           ▼
┌──────────────────────────────────┐
│          ets_common.py (ETSBase)  │
│  - CDP 连接 & eval_js()          │
│  - debug 日志 & js_escape 工具   │
└──────┬───────────────┬───────────┘
       │               │
       ▼               ▼
┌──────────────┐  ┌───────────────┐
│ ets_auto.py  │  │ ets_word_pk.py│
│ 套卷自动答题  │  │ 单词PK自动答题 │
│ - setPCChoose2│  │ - 四级匹配策略 │
│ - 原生Setter │  │ - 派生词生成   │
│ - 本地JSON   │  │ - 双向自学习   │
└──────────────┘  └───────────────┘
       │
       ▼
┌──────────────┐  ┌───────────────┐
│ ets_gui.py   │  │ ets_parser.py │
│ 图形界面启动器 │  │ 离线试卷浏览器 │
│ - 模式/端口   │  │ - 选择题标红   │
│ - 实时日志    │  │ - 填空标准答案 │
│ - Tab 3 集成  │  │ - 口语可接受答案│
└──────────────┘  └───────────────┘

```

ETS 的交互选项为原生 DOM 节点 (`.choose2`) 而非 Canvas 渲染，可直接通过 JS `.click()` 或其内部 API 触发。答案数据在试卷初始化时已明文下发并存储于本地 `%APPDATA%` 缓存中，这赋予了脚本零网络请求即可拿满分的物理外挂特性。

离线试卷浏览器直接解析 `%APPDATA%\ETS\<set_id>\content_xxxx\content.json`，支持五种题型：`collector.choose`（选择题）、`collector.fill`（填空题）、`collector.role`（口语问答）、`collector.picture`（图片描述）、`collector.read`（朗读）。

---

## 📁 目录结构

```text
ETS_Auto/
├── .github/workflows/build-exe.yml  # CI: 自动打包三 exe
├── LICENSE
├── README.md
├── requirements.txt
├── CHANGELOG.md
├── ecdict_pk.json                # ECDICT 字典补充 (PK用)
└── src/auto/
    ├── ets_common.py             # ★ 共享基类 (CDP/重连/版本/路径)
    ├── ets_auto.py               # ★ 套卷自动答题
    ├── ets_word_pk.py            # ★ 单词PK自动答题
    ├── ets_strategy.py           # 答案策略层
    ├── ets_hotkey.py             # 全局热键
    ├── ets_remote.py             # 远程配置
    ├── ets_gui.py                # ★ 图形界面 (CustomTkinter)
    ├── ets_parser.py             # ★ 离线试卷浏览器数据层
    ├── ets_browser_ui.py         # 离线浏览 UI
    └── run.py                    # 统一入口 (exam|pk|gui)

```

---

## 🗺️ 演进路线 (Roadmap)

* [x] **录音题辅助视窗** —— 语音题弹出悬浮参考答案，边看边读告别卡壳。 ✅ v0.2.0
* [x] **一键执行程序** —— GitHub Actions 云端构建 `.exe`，双击即用。 ✅ v0.2.0
* [x] **单词PK自动答题** —— 四级匹配+派生词+自学习，85%+命中率。 ✅ v0.3.0
* [x] **模块拆分** —— 提取 ets_common.py 共享基类，统一入口 run.py。 ✅ v0.3.0
* [x] **傻瓜式 GUI 界面** —— CustomTkinter 可视化窗口，点点鼠标即可完成。 ✅ v0.4.0
* [x] **离线试卷浏览器** —— 扫描本地缓存，红字高亮正确答案，支持五种题型。 ✅ v0.4.0
* [x] **热键支持** —— F9 暂停 / F10 跳过 / F12 停止（全局 RegisterHotKey）。 ✅ v0.5+
* [x] **断连恢复** —— CDP `reconnect()` + 套卷/PK/RW 自动重连与 bridge 重注入。 ✅ v0.6.3–0.6.5
* [x] **策略层双重验证** —— 复合 key 索引 + 模糊 + DOM 回退；`set_id` 数字校验。 ✅ v0.5–0.6.5
* [x] **远程配置** —— 版本/杀开关/公告/pk_extra 热更；可选 HMAC/Ed25519 完整性。 ✅ v0.6.0–0.6.5
* [ ] **作业模式全量真机验收** — 真实作业卷提交链路（桥接已做，缺稳定作业卷回归）

> **注**：由于移动端系统级权限管控，本脚本目前及未来均**不计划**兼容 Android 或 iOS 平台。如果有移动端相关需求，请尝试开源社区的其他相关库吧（如 `Fuck_ets100`，适用于安卓设备的答案检索工具）。

---
## 🤝 参与贡献 & 获取帮助

### ⭐ 求个 Star
如果这个小工具帮你节省了宝贵的时间，让你免受无意义的机械重复之苦，请在页面右上角点亮那个 **Star** ⭐️！
这是对本项目最大的鼓励，也能让更多受折磨的同学看到它。

### 🐛 遇到问题？
如果你遇到了无法解决的 Bug，欢迎来提 Issue，但为了能更快帮你定位问题，提问前请务必走一遍这个流程：
1. **先查 FAQ**：请先仔细阅读上方的【小白指南】和【常见问题】，90% 的问题已经在那里有了解答。
2. **带上日志**：请在命令行后面加上 `--debug` 重新运行脚本（`python src/auto/run.py exam --debug`）。
3. **提交 Issue**：前往 [Issues 页面](https://github.com/Yigenhuobah/ETS_Auto/issues)，点击 `New issue`。
4. **提供关键信息**：在内容中务必包含：
   - 你的系统版本 和 ETS 客户端的版本号。
   - 开启 `--debug` 后的**完整终端输出日志**（直接截图，或者复制粘贴文本）。
   - 是出现了什么问题？

### 🛠️ 欢迎 PR (Pull Requests)
一个人（还有机）的力量是有限的，目标平台的版本和题型也千奇百怪。我们极其欢迎各路技术大佬共同参与开发！
无论你是：
- 修复了某个特定旧版本客户端的兼容性 Bug。
- 补齐真机验收项（作业提交、多 tab、整卷 E2E）或纯逻辑单测。
- 优化了底层的 JS 注入逻辑。
- 甚至只是修正了 readme 里的一个错别字。
都可以直接 Fork 本仓库并提交 Pull Requests！

提交前建议本地跑通：
```bash
python pre_release_test.py
python src/auto/tests/test_unit.py
```


## ⚠️ 注意事项与免责声明

1. **限制**：脚本不会也无法替你发声，遇到语音录入题目请自觉拿起麦克风。
2. **测试环境覆盖**：当前脚本主要针对 ETS v5.7.8 版本开发测试，若官方大更新可能失效。
3. **免责声明**：本项目仅供 Python 自动化学习与前端 Web 安全防护研究使用，旨在揭示 Electron/CEF 架构在本地数据明文存储上的设计缺陷。请勿用于任何违反校规或平台协议的违规操作。
