## [0.5.0] - 2026-05-30

### Added
- **策略层 ets_strategy.py** — 复合key答案索引, 避免索引错位；三级回退链：精确匹配→模糊匹配(similarity>0.6)→DOM答案回退；支持 choose/fill/role/dialogue/picture/read 六种题型
- **全局热键 ets_hotkey.py** — Windows ctypes 零依赖实现，F9暂停/恢复、F10跳过当前题、F12紧急停止（+Alt变体防冲突）；后台线程消息泵
- **ets_auto.py 策略集成** — 初始化时创建 ETSStrategy+ETSHotkey 实例，主循环每次迭代检查热键信号，答题前先查策略层做双重验证(local+DOM)
- **stop_event 参数** — ETSAutoAnswer.__init__ 新增 stop_event，支持 GUI 外部停止信号

### Changed
- ets_auto.py 文件结构从4文件架构扩展为9文件架构
- 文件同步规则更新：ets_auto.py 两边同名（不再 rename 为 ets_exam.py）

