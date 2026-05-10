# ETS Auto - e听说PC端自动答题
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
- [ ] **建立标准的 Issue 模板** - 看看会不会火再说吧（
- [ ] **录音题目辅助** — 语音题弹出参考答案窗口，方便边看边录
- [ ] **打包为 .exe** — PyInstaller 一键生成可执行文件，无需 Python 环境
- [ ] **GUI 界面** — 可视化操作窗口，点点鼠标就能跑
- [ ] **作业模式实战验证** — 在真实作业卷上端到端测试

-注：由于安卓和iOS的权限管控，本脚本长期不会兼容安卓端，也永远不会兼容iOS端，如果确有需要，尝试Fuck_ets100库吧！这是一个适用于安卓系统的答案查找工具
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
