# 开发与本机验收记录

## 2026-09-07：隔离底座与基础组合资格

环境：K60 至尊版，Android Termux，Firefox 154.0.1，Mesa 26.0.6-2，Python 3.14.6。上游基线 `b95eccd3d1abc188c3aa488a23c519ebacc99fcf`。

已实现：

- 有限预设组合、可复现特征与独立 Persona ID、版本化私有持久化。
- 严格池绑定当前环境与完整模板变体；逐变体显示资格数量，全部合格才显示整个模板 validated。
- 证据结构、数值边界、语言/时区一致性和资格撤销检查；同环境重测失败后，保存的严格 Persona 也拒绝再次启动。
- 每 Persona 独立控制进程、Firefox profile、Xvfb、端口与状态；按 PID/start ticks 管理自有资源。
- Firefox 直接 BiDi 接入、原有输入/截图等操作复用、标签页上下文路由。
- 本地 HTTP、Window 和 Dedicated/Shared/Service Worker 探针；有限组合串行 bootstrap。
- 启动失败清理、基础进程预算和异常退出识别。
- 私有 GitHub 开发仓库已创建并验证 push/pull；实现提交 `710f18e` 已同步。

已取得的验证证据：

- GitHub Actions 的 Python 3.10 与 3.14 两组检查均通过：[运行记录](https://github.com/Marcus1337X/termux-firefox-persona/actions/runs/34100366602)。
- 本地 59 项单元测试通过。单元测试中的模拟证据只检查结构与逻辑，不替代设备资格。
- 基础真实后端模板 8/8 个组合完成 K60 至尊版串行 bootstrap，浏览器身份、CPU、显示、语言和时区满足当前 Window、三类 Worker 与 HTTP 资格检查。
- 原生输入、点击及私有路径截图通过；标签页继承与刷新保持 Persona。
- 两个不同 DISPLAY 的 Persona 同时运行，分别导航和输入，localStorage 与持久 cookie 相互隔离。
- 普通派生新窗口继承，以及关闭主窗口联动结束派生窗口和自有实例，通过实机验收。修复窗口定位后，六组实机验收全部通过。
- 双实例运行时应用进程数为 30，测试前后均为 6；本次清理回到基线。
- 停止 A 后 B 继续运行；重新启动 A 后身份配置、localStorage 和持久 cookie 保持。此结果不包含 session cookie 永久保存承诺。

未完成或需继续验证：

- 脚本弹窗、标签页拖出及手动 Ctrl+N 尚未分别验收；通过 BiDi 创建的普通派生新窗口不能替代这些入口的验证。
- 第一批剩余地理位置、字体、完整外观和初始/动态生命周期行为。
- WebGL 创建失败，已取得 `FEATURE_FAILURE_NO_DISPLAY` / `EXHAUSTED_DRIVERS` 错误；尚无合格核显身份模板。基础真实后端组合通过不代表核显模板通过。
- 第二批完整图形、Canvas、Audio、媒体和设备行为，以及第三批其余存储、权限、隐私和网络验收。
- 环境变化后的旧 Persona 会拒绝恢复；本轮尚无保持原 persona_id/profile/最终配置的重新资格入口，不会静默改写身份。
- 后续批次继续使用私有仓库同步，CI 不替代本机资格。

两实例通过表明本次条件下的并发可用，不代表更高并发上限或任意重页面都已验证。最初多进程测试曾导致 Termux 中断，现有预算属于本地准入估算，后续负载仍需按设备能力控制。

两个计划中的勾选仅对应已取得证据的明确子项；完整组合条目及尚未验收的其他批次保持未勾选。
