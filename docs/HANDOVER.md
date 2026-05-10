# ETS Auto Answer Project - Handover Document

> **这是为接手项目的任何人（人或AI）准备的全上下文交接文档。**
> **读完本文档即能理解项目全貌，无需外部上下文。**

最后更新: 2026-05-10 14:17

---

## 1. 项目是什么

这是一个**e听说（ETS）英语学习平台PC端的自动答题工具**。

- **平台**: Windows PC
- **目标用户**: 学生（账号13520959317）
- **目标**: 自动完成e听说PC客户端中的选择题和填空题，语音题展示参考答案不自动提交
- **为什么做**: 每天大量的听说练习作业重复劳动

---

## 2. 当前进度总览

| 模块 | 状态 | 说明 |
|------|------|------|
| 技术方案确认 | ✅ | DOM直操作方案，抛弃OCR/LLM路线 |
| 本地答案读取 | ✅ | 完整实现，零网络依赖；已处理 "/" 分隔答案 |
| DOM结构探测 | ✅ | 完成，选择题、填空题、多题页面DOM结构已搞清楚 |
| CDP连接 | ✅ | 端口10086，WebSocket通信 + 自动重连 |
| 选择题自动点击 | ✅ | jQuery trigger 点击，iframe重载后轮询等待DOM就绪 |
| 填空题自动填值 | ✅ | scrollIntoView+setter+事件触发，DOM值持久保持 |
| 语音题处理 | ✅ | 检测到按钮禁用自动跳过，等待用户手动录音 |
| 最终自动答题脚本 | ✅ | ets_v7.py，2套模拟卷全流程验证通过 |
| 作业模式兼容 | ⚠️ | inject_bridge wrap已实现，待作业卷实测 |
| 多套卷兼容 | ✅ | 不同试卷（不同题型组合）均通过 |

**一句话总结**: 原理已全部验证通过，ets_v7.py 在2套模拟卷上全流程跑通（选择+填空+语音题自动跳过）。作业模式兼容性已做桥接包装（wrap而非replace），等待作业卷实测验证。

---

## 3. 环境与配置

### 3.1 系统环境
- **操作系统**: Windows 10 x64 (19045)
- **Python**: 3.12+ (64位)
- **ETS客户端**: v5.7.8，安装路径 `C:\Program Files (x86)\ETS\`
- **用户数据**: `C:\Users\SmartBoy\AppData\Roaming\ETS\`

### 3.2 ETS客户端关键信息
- **进程**: `ETSShell.exe` (CEF外壳，32位) + `Ets.exe` (主进程)
- **CDP调试端口**: `10086` (固定，通过命令行参数 `--remote-debugging-port=10086`)
- **主页URL**: `https://statics.ets100.com/ets-student-pc-web/index.html`
- **CEF版本**: libcef.dll 132MB
- **MQ Server**: 端口40001 (Node.js WebSocket，悬浮球通信)

### 3.3 Python依赖
```bash
pip install websocket-client psutil
```
- `websocket-client`: CDP WebSocket连接
- `psutil`: 进程检测
- **不需要**: EasyOCR, Ollama, ddddocr (已全部废弃)

### 3.4 启动方式
1. 手动启动E听说客户端 (双击桌面快捷方式或 `C:\Program Files (x86)\ETS\ETSShell.exe`)
2. 登录账号 (13520959317，密码由客户端自动保存)
3. 进入作业列表 → 点击具体作业进入答题页面
4. CDP端口10086自动可用 (ETSShell.exe启动时自带参数)

---

## 4. 技术方案详解

### 4.1 方案演进（废弃方案及废弃原因）

| 阶段 | 方案 | 为何废弃 |
|------|------|----------|
| 1 | pywinauto 直接操作窗口 | CEF窗口隔离，32-64位兼容问题，无法访问内部元素 |
| 2 | pyautogui 硬编码坐标 | 坐标不稳定，换机器/分辨率失效 |
| 3 | mss 直接截图 | CEF渲染层遮挡，截图全白 |
| 4 | CDP截图 + EasyOCR + Ollama本地LLM | OCR慢(2-5s/题)，LLM可能出错，但证明CDP可用 |
| 5 | ets_get_answer API → CDP点击 | API返回答案但丢题号映射，还需OCR对齐 |
| **6 (当前)** | **本地JSON答案 + CDP JS操作DOM** | **最优方案** |

**关键转折点**: 发现ETS选项实际是DOM节点(`.choose2` div)而非Canvas渲染，整个方案极简化。

### 4.2 当前技术方案

```
┌──────────────────────────────────┐
│     ETS PC 客户端 (CEF + DOM)     │
│  - 主框架: 布局壳 (按钮/导航)     │
│  - iframe: 题目内容区             │
│  - 选项: div.choose2              │
│  - 输入: input[type=text]         │
└──────────┬───────────────────────┘
           │ CDP (WebSocket, port 10086)
           ▼
┌──────────────────────────────────┐
│         自动答题脚本              │
│  - Runtime.evaluate: 执行JS操作   │
│  - 读本地JSON获取答案             │
│  - .click() 点击选项              │
│  - scrollIntoView + setter 填空题 │
└──────────────────────────────────┘
```

**核心原理**: ETS客户端内嵌Chromium (CEF)，题目页面是标准HTML DOM，通过CDP协议注入JavaScript直接操作DOM元素。

### 4.3 为什么本地JSON比API方案好

| 对比项 | 本地JSON | ets_get_answer API |
|--------|----------|---------------------|
| 题号映射 | ✅ 完整 `{stid}_{xth}` | ❌ 丢失，只有题型→答案 |
| 网络依赖 | ❌ 零依赖 | ✅ 需要 |
| 答案准确度 | 100% (官方源) | 99% (逆向) |
| 账号风险 | 无 | 有（API逆向） |
| 可用性 | 本地始终可用 | 取决于API稳定性 |

---

## 5. DOM结构（已确认）

### 5.1 页面架构
```
主框架 (ets-student-pc-web/index.html)
  ├── 左侧导航栏 (DOM)
  ├── 顶部进度条 (DOM)
  ├── iframe (来自 statics.ets100.com/ets-student-pc-web/common/pc-listen.html)
  │   └── 题目+选项 (DOM) ← 操作目标
  └── 底部控制栏 (暂停/上一个/下一个 - DOM)
```

### 5.2 选择题DOM
```html
<div class="question2" id="755676_1">  <!-- 题目容器: {stid}_{xth} -->
  <div class="choose_title">
    <span>1. Where is the man from?</span>
  </div>
  <div class="choose2" id="755676_1_1">   <!-- A选项: {stid}_{xth}_{序号} -->
    <p class="choose2_text">A. New York.</p>
  </div>
  <div class="choose2 choose2_selected choose_selected" id="755676_1_2">
    <p class="choose2_text">B. Los Angeles.</p>
  </div>
  <div class="choose2" id="755676_1_3">
    <p class="choose2_text">C. Chicago.</p>
  </div>
</div>
```

- 选中态class: `choose2_selected` + `choose_selected`
- 选项id = 题目id + 序号(1=A, 2=B, 3=C)
- 操作: `opt.click()` 或 `opt.dispatchEvent(new MouseEvent('click', {bubbles: true}))`

### 5.3 填空题DOM
```html
<div class="listen-fill">
  <input type="text" class="fill-input" id="755685_1" value="">
  <input type="text" class="fill-input" id="755685_2" value="">
  <input type="text" class="fill-input" id="755685_3" value="">
  <input type="text" class="fill-input" id="755685_4" value="">
</div>
```

- id格式: `{stid}_{xth}`
- 操作: scrollIntoView → 原生setter改value → 触发input/change事件

### 5.4 控制按钮（主框架DOM，不在iframe内）
```html
<button class="pause-btn">暂停</button>
<button class="prev-btn">上一个</button>
<button class="next-btn">下一个</button>
<button class="replay-btn">重听</button>
```

- **重要**: 不允许点击左侧导航切换题型区，只能通过"上一个/下一个"线性推进
- "下一个"按钮坐标约 (783, 611)，但坐标不固定，建议用JS `.click()`

### 5.5 iframe JavaScript接口
iframe的window对象暴露了以下函数:
- `next()`: 进入下一题
- `submit()`: 提交当前答案
- `choose(id)`: 选择选项
- `play()`: 播放音频
- `pause()`: 暂停
- `replay()`: 重听音频

---

## 6. 本地答案数据

### 6.1 数据位置
```
C:\Users\SmartBoy\AppData\Roaming\ETS\
├── 330152\          # 作业包 (数字是作业ID)
├── 392045\          
├── 392046\          
├── 398810\          
├── 398811\          
├── 543576\          
├── 699597\          # "2025-高一(上)期末练习B"
└── 73265\           
```

每个作业包下有 `content_{stid}\` 子目录，包含:
- `info.json` - 题目答案（主要读取对象）
- `content.json` - 题目内容详情（含选项文本）
- `content2.json` - 去答案版本（给学生的前端页面数据）
- `material\` - 音频素材目录

### 6.2 info.json 数据格式

```json
[
  {
    "code_id": "content_choose",
    "code_json_obj": "{\"stid\":\"755676\",\"xtlist\":[{\"xt_xh\":\"1\",\"xt_nr\":\"题目文本...\",\"answer\":\"B\",\"xxlist\":[{\"xx_mc\":\"A\",\"xx_nr\":\"选项文本\"}]},{\"xt_xh\":\"2\",\"xt_nr\":\"题目文本2...\",\"answer\":\"A\"}]}"
  },
  {
    "code_id": "answer",
    "code_json_array": "[{\"stid\":\"755676\",\"xth\":\"1\",\"answer\":\"B\"},{\"stid\":\"755676\",\"xth\":\"2\",\"answer\":\"A\"}]"
  }
]
```

**关键字段**:
- `stid`: 题目模板ID
- `xt_xh` / `xth`: 题号 (1, 2, 3...)
- `answer`: 正确答案 (选择题A/B/C，填空题文本)
- `xxlist`: 选项列表 (xx_mc=标识, xx_nr=文本)
- `xt_nr`: 题目文本

### 6.3 答案读取代码（已验证）
```python
import json, os

def load_answers(package_id):
    base = os.path.join(r"C:\Users\SmartBoy\AppData\Roaming\ETS", str(package_id))
    answers = {}
    for ct_dir in os.listdir(base):
        if not ct_dir.startswith("content_"):
            continue
        info_path = os.path.join(base, ct_dir, "info.json")
        if not os.path.exists(info_path):
            continue
        data = json.load(open(info_path, "r", encoding="utf-8"))
        for item in data:
            if item.get("code_id") == "answer":
                arr = json.loads(item.get("code_json_array", "[]"))
                for a in arr:
                    answers[f"{a['stid']}_{a['xth']}"] = a["answer"]
    return answers
# 返回: {"755676_1": "B", "755676_2": "A", ...}
```

### 6.4 不同题型的答案格式

| 题型 | code_id | answer格式 | 示例 |
|------|---------|-----------|------|
| 听后选择 | content_choose | A/B/C | "B" |
| 听后记录 | content_fill | 英文文本 | "interests" |
| 听后转述 | collector.picture | 参考文本(数组) | 3段完整转述 |
| 短文朗读 | collector.read | 朗读原文HTML | text about "Brain fog" |
| 回答问题 | collector.dialogue | 参考答案变体 | 每题18/15/32种 |

---

## 7. 关键代码片段

### 7.1 CDP连接
```python
import urllib.request, json, websocket

CDP_PORT = 10086

def get_ws():
    tabs = json.loads(
        urllib.request.urlopen(f"http://localhost:{CDP_PORT}/json", timeout=5).read()
    )
    tab = [t for t in tabs if "ets100.com" in t.get("url", "")][0]
    return websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=10)

def cdp_eval(ws, expression):
    """在页面中执行JS并返回结果"""
    msg = json.dumps({
        "id": 1,
        "method": "Runtime.evaluate",
        "params": {"expression": expression, "returnByValue": True}
    })
    ws.send(msg)
    resp = json.loads(ws.recv())
    return resp.get("result", {}).get("result", {}).get("value")
```

### 7.2 选择题点击
```javascript
// 在iframe的contentDocument中执行
(function() {
    var iframe = document.querySelector('iframe');
    var doc = iframe.contentDocument || iframe.contentWindow.document;
    var option = doc.getElementById('755676_1_2');  // B选项
    option.click();
    option.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
})()
```

### 7.3 填空题设置值（已验证）
```javascript
(function() {
    var iframe = document.querySelector('iframe');
    var doc = iframe.contentDocument || iframe.contentWindow.document;
    var inp = doc.getElementById('755685_1');
    
    // 滚动到可见
    inp.scrollIntoView({behavior: 'instant', block: 'center'});
    
    // 原生setter写值（绕过React/Vue等框架拦截）
    var setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value'
    ).set;
    setter.call(inp, 'interests');
    
    // 触发事件
    inp.dispatchEvent(new Event('input', {bubbles: true}));
    inp.dispatchEvent(new Event('change', {bubbles: true}));
})()
```

### 7.4 获取当前页面所有可见题目
```javascript
(function() {
    var iframe = document.querySelector('iframe');
    if (!iframe) return JSON.stringify(null);
    var doc = iframe.contentDocument || iframe.contentWindow.document;
    var questions = doc.querySelectorAll('.question2');
    var result = [];
    questions.forEach(function(q) {
        if (q.offsetHeight > 0) {  // visible check
            result.push(q.id);
        }
    });
    return JSON.stringify(result);
})()
```

---

## 8. 文件结构

```
E:\download\ETS_Project\
├── README.md                         # 项目概述（面向开发者，v2）
├── HANDOVER.md                       # 本文件（面向接手者，v2）
├── docs/
│   └── dev_log.md                    # 详细开发日志（按日期）
├── src/
│   └── auto/
│       ├── ets_v7.py                 # [主脚本] ★ 生产级自动答题脚本（v7，当前主力）
│       ├── ets_auto.py               # [脚本] 上古版全扫描方案（作业模式验证过）
│       ├── ets_auto_final.py         # [脚本] v1最终版备份
│       ├── ets_v4.py                 # [脚本] v4版本（多题页面）
│       ├── ets_v5.py                 # [脚本] v5版本
│       ├── ets_v6.py                 # [脚本] v6版本
│       ├── ets_universal.py          # [脚本] 通用版（配置驱动，v2.0）
│       ├── ets_config.py             # [配置] 通用版配置系统
│       ├── ets_probe.py              # [工具] DOM探测工具
│       ├── answer_current.py         # [工具] 选择题点击测试
│       ├── check_page.py             # [工具] 页面检测
│       ├── cdp_screenshot.py         # [工具] CDP截图，存screenshots/
│       ├── cdp_dom_probe.py          # [工具] DOM结构探测
│       ├── cdp_iframe_probe.py       # [工具] iframe内容探测
│       ├── check_all_answers.py      # [工具] 验证本地JSON答案完整性
│       ├── check_fill_answers.py     # [工具] 检查填空题答案
│       ├── check_fill_content.py     # [工具] 检查填空题content.json
│       ├── explore_fill.py           # [工具] 填空题DOM结构探索
│       ├── test_scroll.py            # [工具] scrollIntoView可行性测试
│       ├── test_scroll_fill.py       # [工具] 填空填写完整测试 ✅
│       ├── test_bridge.py            # [工具] Bridge注入测试
│       ├── test_bridge_proto.py      # [工具] Bridge注入原型验证
│       ├── test_answer_record.py     # [工具] 答案记录机制测试
│       ├── test_full_flow.py         # [工具] 完整流程测试
│       ├── auto_answer.py            # [脚本] 自动答题主控(v1, 半成品)
│       └── deprecated/               # 全部弃用脚本（详细分类了下）
│           ├── ocr_llm/             # OCR+LLM方案（8个脚本）
│           ├── early_cdp/           # 早期CDP一次性实验（14个）
│           ├── answer_attempts/     # v1-v6答题尝试（16个，iframe重载问题）
│           └── old_tests/           # pywinauto时代测试（8个）
└── screenshots/                      # CDP截图存档（18张）
```

**最有价值的文件**: `test_scroll_fill.py` - 已验证完整填空题写入链路，可作为填空题模块的参考实现。

---

## 9. 已确认的问题（及应对方案）

### 9.1 问题1: iframe重载后DOM丢失 ✅ **已解决**

**现象**: 点击"下一个"按钮后，iframe会重新加载内容页面，JS执行上下文中之前的DOM引用全部失效。

**解决方案**: 轮询等待iframe DOM就绪 + 每次操作前重新获取iframe引用（不在内存中缓存）。
ets_v7.py 中通过 `wait_iframe_ready()` + 超时重试机制稳健处理。

**已废弃的方案及失败原因**:
- v3: 硬编码2秒sleep后直接操作 → DOM未加载完，找不到元素
- v4: 坐标点击"下一个" → 点击成功但下一步iframe未就绪
- v5: 加retry循环 → WebSocket连接本身挂住
- v6: 重新每次获取iframe引用 → WebSocket挂住
```python
def wait_iframe_ready(ws, timeout=10):
    """Poll until iframe has clickable/inputtable elements"""
    for i in range(timeout * 2):
        result = cdp_eval(ws, """
            (function() {
                var iframe = document.querySelector('iframe');
                if (!iframe || !iframe.contentDocument) return 'no iframe';
                var elements = iframe.contentDocument.querySelectorAll(
                    '.choose2, input[type="text"], textarea'
                );
                return elements.length > 0 ? 'ready:' + elements.length : 'loading';
            })()
        """)
        if result and result.startswith('ready:'):
            return True
        time.sleep(0.5)
    return False
```

### 9.2 问题2: 填空题DOM值 ✅ **已验证**

**已知**: 原生setter + input/change事件后，`inp.value` 持久保持正确值。
**验证结果**: 模拟练习中填空题值在"下一个"切换后保持、视觉反馈正常。两点已确认的新修复：
- 音频重复播放已填填空题 → `fill_count > 0` 判断避免误退出
- 答案含 "/" 分隔（如 "Organise/Organize"）→ `split('/')[0]` 只取第一个

**待实测**: 作业模式提交时ETS是否从DOM取值（概率低，ETS前端为传统jQuery风格）。

### 9.3 问题3: 语音题无法自动提交 🔴 **确认不做**

**原因**: 用户明确TTS生成的录音老师能听出来，提交会被发现。
**方案**: 遇到语音题（转述/朗读/回答）时，从本地JSON读取参考答案文本，展示给用户，等用户自己录音完成。

### 9.4 问题4: 导航限制 ⚠️ **已确认**

- **不能点左侧导航**（无论DOM还是坐标都无效）
- **只能点"上一个/下一个"按钮**线性切换
- **或等题目计时结束自动跳转**（不推荐，太慢）

### 9.5 问题5: CDP WebSocket稳定性 ⚠️ **已知**

WebSocket连接偶尔会卡住，需要超时+重连机制:
- 设置 `ws.settimeout(10)`
- 操作超时时 close + 重建连接
- 避免长时间持有同一个连接

### 9.6 问题6: 编码问题

- PowerShell输出中文乱码 (GBK/UTF-8不一致)
- 按钮文本检测用char code比较而非中文直接匹配
- 文件读写都用 `encoding="utf-8"`

### 9.7 v7开发中修复的额外问题 ✅

**多题页面检测**:
- 部分页面含2道题（同一音频配2道选择题），原逻辑只检测第一道
- 修复: `get_question_info()` 支持多题页面，正确获取第一题以外的选项

**第一题点击失败**:
- 每页第一道题 jQuery trigger 后 choose_selected 没加上，第二道题正常
- 根因: jQuery trigger 后面还跟着原生 MouseEvent 事件干扰
- 修复: 删除多余原生事件，只保留 `$(el).trigger('click')`

**填空题误退出**:
- 音频重复播放相同填空题时所有 input 已被填入，`answer_fill()` 返回 False
- 修复: 改成 `fill_count > 0` 判断

**"/" 分隔答案**:
- 答案格式 "Organise/Organize" 表示二选一，脚本之前整体填入
- 修复: `load_answers()` 中 `ans.split('/')[0].strip()`

**作业模式 Bridge Wrap**:
- 问题: inject_bridge() 直接覆盖 win.kttb_ReturnChoose，作业模式下会干掉原生CEF函数
- 修复: 改为 wrap 模式 —— 先调原生函数（如果存在），再记录到 __ets_recorded
- 启动时打印模式: HOMEWORK（nativeChoose=true）或 PRACTICE（nativeChoose=false）

---

## 10. 下一步：写最终版自动答题脚本

### 10.1 设计原则

1. **只做选择题+填空题，语音题只展示答案**
2. **线性推进**: 读题 → 作答 → 点"下一个" → 等待iframe就绪 → 下一题
3. **ifarme每次操作前重新获取引用**（不在内存中缓存）
4. **每步加5秒超时，超时后重试**

### 10.2 脚本流程

```
1. 连接CDP (端口10086)
2. 加载本地JSON答案 (包ID如699597)
3. 主循环:
   a. 获取当前iframe中DOM
   b. 判断题型:
      - .choose2存在 → 选择题: 匹配答案 → click选项
      - input[type=text]存在 → 填空题: scrollIntoView → setter填值
      - 其他 → 语音题: 打印参考答案，提示用户
   c. 点击主框架"下一个"按钮
   d. wait_iframe_ready() 轮询等DOM就绪
   e. 重复
4. 所有题处理完 → 关闭
```

### 10.3 输出建议

脚本运行时实时输出:
```
[选择题] 755676_1 = B → 已点击
[选择题] 755676_2 = A → 已点击
[填空题] 755685_1 = interests → 已填入
[填空题] 755685_2 = scene → 已填入
[语音题] 755686 = 听后转述 → 请用户手动录音
  ── 参考文本 ──
  The school is organizing a movie night...
  ──────────────
```

### 10.4 用户偏好

- **不做语音题自动提交**（红线）
- 只写测试/验证脚本，不写完整功能实现（除非明确要求）
- 逐步推进，每步验证后继续
- 脚本放在 `E:\download\ETS_Project\src\auto\` 下

---

## 11. 外部参考

### 11.1 开源项目
- **ets_get_answer** (github.com/code-leitianshuo/ets_get_answer, 2026-05-05创建)
  - 逆向ETS100 API拿答案，但丢失题号映射
  - 签名密钥: `555ffbe95ccf4e9535a110170b445ab8` (已暴露)
  - ⚠️ 原作者(code-leitianshuo)因安全暴露问题（"请求伪造.py"硬编码测试账号密钥明文）被提醒后**删号跑路**，项目已孤儿化
  - 项目已迁移至 github.com/listen-answer/ets_get_answer（由hicccc77接手维护）
- **FuckETS** (PC): PyAutoGUI+Tesseract+智谱GLM-4，可能已停维
- **Fuck_ets100** (github.com/listen-answer/Fuck_ets100, Android): 读取本地JSON答案，活跃
  - issue #11: 多人讨论跨平台协作，zhang090210发起
  - **zhang090210已退出PC端**（"暂时没有时间和精力管这种破解的项目了"），PC端坑位空出
  - 用户(GitHub: yigenhuobah)是唯一有验证PC端方案的人

### 11.1.1 关键人物格局

| 人物 | 核心能力 | 当前状态 | 备注 |
|------|----------|----------|------|
| **hicccc77 = ycccccccy** | 微信逆向+ETS API逆向+资源索引 | 活跃，ets_get_answer维护者 | 同一人双号。旧号ycccccccy做EchoTrace(微信导出)，2026-01-18停维，README导流到WeFlow。新号hicccc77做WeFlow(Electron+C++) | Telegram + TRC20打赏，不留国内实名联系方式 |
| qiuqiqiuqid | Flutter移动端 | 活跃，做移动端app | 已和hicccc77互关抖音私聊 |
| zhang090210 | rust/flutter/kotlin/python/前端 | **已退出PC端** | issue #11发起人 |
| ets_get_answer原作者 | API逆向 | **删号跑路** | 安全暴露后被提醒后消失 |

**关键发现**: hicccc77和ycccccccy是同一人——ycccccccy的EchoTrace停维时README直接导流到WeFlow（hicccc77创建），时间线吻合（WeFlow 2026-01-10创建 → EchoTrace 2026-01-18停维）。旧号留小红书群聊(已失效)，新号只留Telegram+TRC20，隐私策略大幅升级。

**hicccc77是团队实际技术核心**: 既做微信逆向(比ETS难得多)，又做ets_get_answer(API级逆向)，issue里最务实的声音(劝退LSPatch、强调用户群体)。纯开源贡献者模式(做出来→开源→接受自愿打赏)。

### 11.1.2 ETS加固与安全
- 移动端ETS使用**360天域壳+签名校验**（hicccc77确认）
- LSPatch路线已被否决：root手机极少+签名验证过不了
- qiuqiqiuqid尝试脱壳得到几个dex，hicccc77说是"邦邦的壳"（加固方案有争议，可能不同版本/平台不同）
- 移动端最新算法已放弃读名称，改为"答案+地区"匹配

### 11.1.3 资源索引
- Gitee仓库: https://gitee.com/asdasasdasdasfsdf/Etsresource
- 14个JSON文件：beijing-{C1,C2,C3,G1,G2,G3}.json + resource-{C1,C2,C3,G1,G2,G3}.json
- 映射教材单元名→MD5哈希(fileIdentifiers)，用于手机端定位答案
- PC端不需要此方案（CDP可直接读stid）

### 11.2 ETS服务端API
- API地址: `api.ets100.com`
- 登录接口: 10次错误即锁定10分钟
- 语音评测引擎: AI, AI_2MIX, MSC, AI_2INS, AI2 (北京初中用MSC)
- 评分曲线: 按区域分段线性（北京初中/高中、广州、深圳、东莞、江苏各市各自不同）
- 超时配置: read_sentence=35s, read_chapter=130s, read_word=30s, topic=125s

### 11.3 登录凭据 (仅本地使用)
- 账号: 13520959317
- 密码: 加密存储 `k2+1dkMuvyugkbpQvG0KSw==`
- 自动登录已开启
- ETS.db: `C:\Program Files (x86)\ETS\localdata\ETS.db` (12KB SQLite)

---

## 12. 相关记忆文件

- `C:\Users\SmartBoy\.qclaw\workspace-agent-42a61cff\memory\2026-05-05.md` — 项目探索阶段的完整对话记录
- `C:\Users\SmartBoy\.qclaw\workspace-agent-42a61cff\memory\2026-05-07.md` — 人物格局分析、安全策略、协作策略讨论

## 13. 协作策略（2026-05-07更新）

### 当前局势
- PC端无人做，用户是唯一有验证方案(CDP+DOM)的人
- hicccc77(技术核心)做API逆向+微信逆向，但没做PC端工具
- qiuqiqiuqid做移动端Flutter
- zhang090210已退出

### 用户GitHub身份
- GitHub: yigenhuobah
- 在Fuck_ets100 issue #11评论过但姿态被动("求带")

### 建议行动
1. 在issue #11追评，姿态从"求带"改为"我来"，亮出CDP+DOM方案已验证成果
2. 联系方式留GitHub ID或Telegram（hicccc77不用国内实名渠道，不进QQ群）
3. 先把最终自动答题脚本做出来再谈合作，有成品才有话语权

### 安全注意
- hicccc77从ycccccccy换马甲后隐私策略大升级，说明他对风险有清醒认知
- 未来做发行版必须剥离所有可溯源信息
- hicccc77的模式是纯开源+打赏，不做付费工具

---

## 附录: 快速操作清单

```bash
# 检查CDP是否可用
curl http://localhost:10086/json

# 查看当前页面DOM
cd E:\download\ETS_Project\src\auto
python cdp_dom_probe.py

# 查看当前页面状态
python cdp_check_state.py

# 验证答案数据完整性
python check_all_answers.py

# 测试填空填写
python test_scroll_fill.py

# 截图
python cdp_screenshot.py
```