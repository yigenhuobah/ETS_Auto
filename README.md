
# 🎯 ETS Auto — e听说 PC 端自动答题工具

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/)
[![Windows](https://img.shields.io/badge/Platform-Windows-0078D6.svg)]()
[![ETS v5.7.8](https://img.shields.io/badge/ETS-v5.7.8-success.svg)](https://www.ets100.com/home/index.html)

> 🚀 **e听说自动答题 / ETS答案提取** · 听后选择零秒作答 · 听后记录自动填词 · 无需管理员权限 · 不篡改客户端 · 纯本地零网络

**每天被 e 听说作业折磨？** 这个工具能自动完成 PC 端的听说选择题和填空题，遇到语音题自动停住等你开口读。不修改客户端、不篡改分数、不依赖网络，是目前 ETS 环境下最**合规安全**的自动答题方案。

---

## ✨ 功能特性

- ✅ **选择题自动作答** — 自动点击正确选项，听后选择1/听后选择2 通杀
- ✅ **填空题自动填值** — 自动填入答案，听后记录/单词填空无缝支持
- ✅ **录音题答案展示** — 听后转述/回答问题/短文朗读自动弹出参考答案窗口
- ✅ **零网络依赖** — e听说答案从本地缓存直接提取，无需抓包联网
- ✅ **双模式兼容** — 模拟练习 & 作业模式自动检测，无缝切换
- ✅ **跨题型自动导航** — 选择→填空 Section 过渡自动等待重试

---

## 🐣 小白指南（完全不懂代码也能用）

### 推荐方式：直接使用打包版 (免安装任何环境)
1. 前往本仓库的 [Releases 页面](https://github.com/yigenhuobah/ETS_Auto/releases/latest)。
2. 在 `Assets` 列表中下载最新的 `ets_auto.exe`。
3. 打开 ETS PC 客户端，登录并进入具体的题目答题页面。
4. 双击运行下载好的 `ets_auto.exe`，享受喝茶的时光。

---


### ❓ 常见问题 (FAQ)

* **Q: 提示 "No ETS tab found" 怎么办？**
A: ETS 客户端未运行，或尚未进入答题页面。请先在 ETS 中点开具体的题目页，再运行脚本。
* **Q: 提示 "Failed to load answers" 是什么意思？**
A: 你还没有打开过这套题。ETS 需要先加载一次题目页面，答案才会缓存到本地。去客户端里看一眼题目再回来运行即可。
* **Q: 脚本一直卡住不动了**
A: 在终端里按 `Ctrl + C` 强制终止，然后加上 `--debug` 参数重新运行，查看详细报错信息：
`python src/auto/ets_auto.py --debug`
* **Q: 语音录音题怎么处理？**
A: 脚本启动时会自动弹出参考答案窗口（听后转述/回答问题/短文朗读），你可以边看答案边录音。选择题和填空题做完后，关闭答案窗口即可停止脚本。

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

# 2. 运行脚本 (请确保已在客户端进入答题页)
python src/auto/ets_auto.py              # 默认模式（静默极简输出）
python src/auto/ets_auto.py --debug      # 调试模式（输出 CDP 交互日志）
python src/auto/ets_auto.py --max 200    # 设定安全步数上限为200（默认 999，通常无需手动干预）
python src/auto/ets_auto.py --log run.log  # 将所有输出保存到 run.log
python src/auto/ets_auto.py --show-answers  # 仅查看答案，不自动答题
python src/auto/ets_auto.py --json       # 以 JSON 格式输出结果

```

---

## 📊 运行效果展示

```text
ETS Auto
========================================
ETS connected
Loaded 21 answers (set_id=543576)
Mode: PRACTICE | Questions: 21
Recording answers: 3 types available
----------------------------------------
  Choose Q:584722_1 -> B
  Choose Q:584723_1 -> C
  ...
  Fill 584731_1 = Organise
  Fill 584731_2 = trust
  Fill 584731_3 = patient
  Fill 584731_4 = review
========================================
Done: 14 choose + 4 fill = 18 answered
Coverage: 18/21 (86%)
3 recording questions shown in answer window

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
│          ets_auto.py               │
│  - Runtime.evaluate: JS 操作 DOM │
│  - setPCChoose2(): ETS 内部选择题 API│
│  - 原生 Setter: 劫持填空题双向绑定 │
│  - 本地 JSON: 零网络依赖物理读答案 │
└──────────────────────────────────┘

```

ETS 的交互选项为原生 DOM 节点 (`.choose2`) 而非 Canvas 渲染，可直接通过 JS `.click()` 或其内部 API 触发。答案数据在试卷初始化时已明文下发并存储于本地 `%APPDATA%` 缓存中，这赋予了脚本零网络请求即可拿满分的物理外挂特性。

---

## 📁 目录结构

```text
ETS_Auto/
├── .github/workflows/build-exe.yml  # CI: 自动打包 exe
├── LICENSE
├── README.md
├── requirements.txt
├── CHANGELOG.md
└── src/auto/
    └── ets_auto.py              # ★ 核心驱动脚本

```

---

## 🗺️ 演进路线 (Roadmap)

* [ ] **完善 Issue 规范** —— 等这个项目火了再说吧（
* [x] **录音题辅助视窗** —— 语音题弹出悬浮参考答案，边看边读告别卡壳。 ✅ v0.2.0 已实现
* [x] **一键执行程序** —— GitHub Actions 云端构建 `.exe`，双击即用，彻底免除 Python 环境配置。 ✅ v0.2.0 已实现
* [ ] **傻瓜式 GUI 界面** —— 加入可视化窗口，点点鼠标即可完成。

> **注**：由于移动端系统级权限管控，本脚本目前及未来均**不计划**兼容 Android 或 iOS 平台。如果有移动端相关需求，请尝试开源社区的其他相关库吧（如 `Fuck_ets100`，适用于安卓设备的答案检索工具）。

---
## 🤝 参与贡献 & 获取帮助

### ⭐ 求个 Star
如果这个小工具帮你节省了宝贵的时间，让你免受无意义的机械重复之苦，请在页面右上角点亮那个 **Star** ⭐️！
这是对本项目最大的鼓励，也能让更多受折磨的同学看到它。

### 🐛 遇到问题？
如果你遇到了无法解决的 Bug，欢迎来提 Issue，但为了能更快帮你定位问题，提问前请务必走一遍这个流程：
1. **先查 FAQ**：请先仔细阅读上方的【小白指南】和【常见问题】，90% 的问题（如环境没装好、忘了先进题目页）已经在那里有了解答。
2. **带上日志**：请在命令行后面加上 `--debug` 重新运行脚本（`python src/auto/ets_auto.py --debug`）。
3. **提交 Issue**：前往 [Issues 页面](https://github.com/Yigenhuobah/ETS_Auto/issues)，点击 `New issue`。
4. **提供关键信息**：在内容中务必包含：
   - 你的系统版本 和 ETS 客户端的版本号。
   - 开启 `--debug` 后的**完整终端输出日志**（直接截图，或者复制粘贴文本）。
   - 是出现了什么问题？

### 🛠️ 欢迎 PR (Pull Requests)
一个人（还有机）的力量是有限的，目标平台的版本和题型也千奇百怪。我们极其欢迎各路技术大佬共同参与开发！
无论你是：
- 修复了某个特定旧版本客户端的兼容性 Bug。
- 实现了 Roadmap 中的 GUI 界面或 `.exe` 一键打包。
- 优化了底层的 JS 注入逻辑。
- 甚至只是修正了readme里的一个错别字。
都可以直接 Fork 本仓库并提交 Pull Requests！


## ⚠️ 注意事项与免责声明

1. **限制**：脚本不会也无法替你发声，遇到语音录入题目请自觉拿起麦克风。
2. **测试环境覆盖**：当前脚本主要针对 ETS v5.7.8 版本开发测试，若官方大更新可能失效。
3. **免责声明**：本项目仅供 Python 自动化学习与前端 Web 安全防护研究使用，旨在揭示 Electron/CEF 架构在本地数据明文存储上的设计缺陷。请勿用于任何违反校规或平台协议的违规操作。

