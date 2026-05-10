# ETS Auto - e听说PC端自动答题

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/)
[![Version 0.1.0](https://img.shields.io/badge/version-0.1.0-green.svg)](.)

> e听说(ETS) PC端自动答题工具 — 听后选择、听后记录等选填题一键完成

---


## ⚠️ 本项目仅供前端安全研究和Python自动化学习使用，严禁用于任何实际使用场景，开发者不对滥用行为负责
---


## ✨ 功能特性

- ✅ **选择题自动作答** — 自动点击正确选项
- ✅ **填空题自动填值** — 自动填入答案文本
- ✅ **语音题自动跳过** — 检测后跳过，用户手动录音
- ✅ **零网络依赖** — 答案从本地缓存读取，无需联网
- ✅ **模拟练习 + 作业模式兼容** — 自动检测模式并适配
- ✅ **跨题型自动导航** — Section过渡自动等待重试
- ✅ **自动结束检测** — 试卷做完自动停止

---

## 🐣 小白指南（完全不懂代码也能用）

### 第一步：安装 Python

1. 去 [python.org](https://www.python.org/downloads/) 下载 Python 3.12+
2. 安装时 **务必勾选** "Add Python to PATH"（重要！）
3. 按 `Win+R` 输入 `cmd` 回车，输入 `python --version` 看到版本号即成功

### 第二步：下载本项目

**方式 A：Git（推荐）**
```bash
git clone https://github.com/your-username/ETS_Auto.git
cd ETS_Auto
```

**方式 B：直接下载 ZIP**
1. 点击页面绿色 "Code" → "Download ZIP"
2. 解压到任意目录（如 `E:\ETS_Auto`）

### 第三步：安装依赖

在项目目录右键 → "在终端中打开"，输入：

```bash
pip install -r requirements.txt
```

看到 `Successfully installed` 即可。

### 第四步：运行

1. 启动 ETS PC 客户端，登录账号
2. 进入模拟练习或作业页面
3. 在命令行输入：

```bash
python src/ets_v8.py
```

4. 看到 `All questions answered. Exam complete!` 即完成 🎉

### 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| "No ETS tab found" | ETS 客户端没运行 | 先打开 ETS 进入题目页再运行 |
| "Failed to load answers" | 首次打开该套题 | 先在 ETS 里点开看一遍 |
| 脚本卡住不动 | 题目加载较慢 | `Ctrl+C` 终止后加 `--debug` 重跑 |
| 语音题没处理 | 需要手动录音 | 正常，脚本自动跳过语音题 |

---

## 🚀 快速开始（有经验用户）

```bash
git clone https://github.com/your-username/ETS_Auto.git
cd ETS_Auto
pip install -r requirements.txt

# 运行
python src/ets_v8.py              # 默认（静默输出）
python src/ets_v8.py --debug      # 调试模式
python src/ets_v8.py --max 200    # 安全步数上限（默认999）
```

---

## 📊 运行效果

```
ETS Auto Answer v8
========================================
ETS connected
Loaded 20 answers (set_id=20409)
Mode: PRACTICE | Questions: 20
----------------------------------------
  Choose Q:82750_1 -> C
  Choose Q:82751_1 -> B
  ...
  Fill 82774_1 = parents
  Fill 82774_2 = March
  Fill 82774_3 = 36000
  Fill 82774_4 = plane
  Fill 82774_5 = Venice
========================================
Done: 15 choose + 5 fill = 20 answered
Coverage: 20/20 (100%)

All questions answered. Exam complete!
```

---

## 🔧 工作原理

```
┌──────────────────────────────────┐
│     ETS PC 客户端 (CEF + DOM)     │
│  - 主框架: Vue 3 + Pinia          │
│  - iframe: Vue 1.x 题目渲染        │
│  - CDP端口: 10086                 │
└──────────┬───────────────────────┘
           │ Chrome DevTools Protocol
           ▼
┌──────────────────────────────────┐
│         ets_v8.py                │
│  - Runtime.evaluate: JS操作DOM    │
│  - setPCChoose2(): 选择题API      │
│  - 原生setter: 填空题填值          │
│  - 本地JSON: 零网络依赖读答案       │
└──────────────────────────────────┘
```

**核心发现**: ETS选项是DOM节点(`.choose2`)而非Canvas，可直接JS `.click()`。答案存储在本地缓存，零网络依赖。

---

## 📁 目录结构

```
ETS_Auto/
├── LICENSE
├── README.md
├── requirements.txt
├── CHANGELOG.md
├── src/
│   └── ets_v8.py              # ★ 主脚本
└── docs/
    └── HANDOVER.md             # 技术细节文档
```

---

## 📋 Roadmap

- [ ] **录音题目辅助** — 语音题弹出参考答案窗口，方便边看边录
- [ ] **打包为 .exe** — PyInstaller 一键生成可执行文件，无需 Python 环境
- [ ] **GUI 界面** — 可视化操作窗口，点点鼠标就能跑
- [ ] **作业模式实战验证** — 在真实作业卷上端到端测试
- 注：由于安卓和iOS的权限管控，本脚本长期不会兼容安卓端，也永远不会兼容iOS端，如果确有需要，尝试Fuck_ets100库吧！这是一个适用于安卓系统的答案查找工具
---

## ⚠️注意事项
1. **语音题需手动录音** — 脚本自动跳过
2. **作业模式待实测** — 兼容代码已实现，等待真实作业卷验证
3. **ETS版本兼容** — 仅测试过 ETS v5.7.8
4. **ETS 需先加载题目** — 首次打开某套题时 ETS 会缓存答案到本地

---

## 🛠️ 技术细节

### DOM 结构

```javascript
// 选择题
<div class="choose2" id="{stid}_{xth}_{序号}">
  // 序号: 1=A, 2=B, 3=C...
  // 选中态: .choose_selected / .choose2_selected
</div>

// 填空题
<input type="text" class="fill_word_input" id="{stid}_{xth}">
```

### 答案来源

ETS本地缓存自动下载答案数据：

```
C:\Users\{User}\AppData\Roaming\ETS\{set_id}\content_{stid}\content.json
```

### 关键 API

```javascript
// 选择题：ETS原生API（推荐）
win.setPCChoose2(targetId);

// 填空题：原生setter绕过框架拦截
var setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
setter.call(inp, 'answer');
inp.dispatchEvent(new Event('input', {bubbles: true}));
```

---

## 📄 许可证

[MIT License](LICENSE)

---

## 🙏 相关项目

| 项目 | 平台 | 原理 |
|------|------|------|
| ets_get_answer | 全平台 | API逆向获取答案 |
| Fuck_ets100 | Android | 本地JSON答案 |
