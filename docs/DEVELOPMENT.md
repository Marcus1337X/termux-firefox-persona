# 开发与本机验收记录

## 2026-09-07：原生 Audio 探针与可信点击

新增 `linux-firefox-audio-glx-v1/1.0.0` 的四个组合全部通过本机资格验证，继承地区/外观、私有字体与 Window/三类 Worker 图形资格，增加 `audio_offline` 和 `audio_realtime`。明色组合选择 44100 Hz，暗色选择 48000 Hz；Firefox 原生默认 AudioContext 采样率由私有配置生效，不修改页面 API 返回值。

- OfflineAudioContext 两次真实渲染双声道、2048 帧固定 buffer/gain 图，资格检查实际数值、样本 hash、尺寸和自然状态链。
- AudioContext 通过真实指针点击获得用户激活，检查 resume/suspend/resume/close、时钟行为与 analyser 波形/频谱；末端 gain 为 0，资源在失败路径也清理。
- `click_native` 使用 BiDi 指针动作，并在验收页面记录 `event.isTrusted`。旧 `click` 的默认 CSS 左键路径优先使用 DOM 合成点击，不能保证可信激活；它另有 human/坐标的 xdotool 路径。历史验收使用默认 CSS 路径，过去的点击成功记录不证明可信用户激活。
- 双声道能力只限图内运算；物理输入/输出均为 `not_verified`，延迟是诊断观测，Audio Worker 为 `notapplicable`。未验证媒体设备、codec 或完整物理音频后端。
- 环境快照升级 schema 3，纳入音频环境变量、配置摘要及可获得的服务端事实。旧 24 个组合已在新环境快照下逐个重新验证通过，ID、种子和最终配置保持；资格不由 schema 升级自动继承。
- 生命周期脚本新增双实例默认 AudioContext 采样率隔离及派生上下文继承，重启保留 Offline 实际证据和 Realtime 稳定结构/状态比较，排除实时相位、时钟、延迟。完整探针仍在单实例阶段执行。

本地 137 项单元测试、编译与差异检查通过。新四个组合 bootstrap 与旧 24 个组合重验全部通过，当前 schema 3 合格池共 28 个组合。实现提交 `90bfb9e` 的 Python 3.10 / 3.14 CI 均通过：[运行记录](https://github.com/Marcus1337X/termux-firefox-persona/actions/runs/34112107654)。

四个 Audio bootstrap 身份：`persona_28d90612f97e1b204bc96269424b1378`、`persona_e67b23491ee2229ad90a3cf72ad5d6a9`、`persona_24941d4caa25cb7afcdef85b4f441d86`、`persona_04da5d6e159eefab1bcdb06ff3c48ddc`。

16 组 Audio 实机生命周期验收全部通过，身份为 `persona_78f63997a4754087bd5825e835efc49f` / `persona_4fc7f7b383204ccda6748a93cb5c7227`。双实例分别呈现 44100/48000 Hz 默认 AudioContext，实际 Offline 渲染和 analyser 测量通过，重启证据保持；原生点击的 `isTrusted=true` 与脚本弹窗验证通过。应用进程数为基线 7、双实例 31、结束 7，测试实例已停止。

复现命令：`python cli.py persona bootstrap --template linux-firefox-audio-glx-v1`，然后运行 `python -m tests.persona_live --template linux-firefox-audio-glx-v1`。旧身份先停止，再执行 `python cli.py persona requalify PERSONA_ID`；此操作保留 ID、种子、最终配置与 profile。

## 2026-09-07：Worker 字体与 OffscreenCanvas/WebGL

新增 `linux-firefox-workers-glx-v1/1.0.0`，四个候选组合全部取得本机资格，当前合格组合为 24 个。旧 20 个组合及其资格范围保持不变，没有修改系统字体或图形环境。

- 复用 Window 字体和 GL 绘制代码，Worker 使用原生 FontFace/self.fonts/OffscreenCanvas，不覆盖页面返回值。
- Dedicated、Shared、Service Worker 各自上报最终完成结果；Service Worker 使用 waitUntil 保持异步探针生命周期。默认旧模板不启动额外 Worker 图形探针。
- 字体要求正反向本地加载、通用别名、direct/local 与 Window 的实际像素和完整 TextMetrics 一致；convertToBlob 的 PNG 解码必须匹配。Worker 的 toDataURL 明确不适用。
- WebGL1/2 分别要求实际 shader 编译/链接、红色三角形和绿色 RGBA8 framebuffer 读回，以及同 Window 的 Mesa llvmpipe 身份。完整 precision/limits/extensions 与核显兼容仍未完成。
- 单测覆盖缺失 Worker、伪造 hash、PNG 导出缺失/错误与错误 GL 像素，失败不能进入严格池。

四个 bootstrap 身份：`persona_c5cb84b895fdea132e118e6d0a5796dd`、`persona_6286501b9d759893f4a86a448d08f426`、`persona_f01241f647427a4e51b2d7903006ff5b`、`persona_1181cb73bd318ad46a9d81deb7840dab`。

15 组实机生命周期验收全部通过，身份为 `persona_d8722614d2e745d2b9bd2d411f85e75f` / `persona_857221f37c194923b28523cdbc48f682`，包括双实例不同字体集合、Window/三类 Worker 重启后实际证据保持。基线 6、双实例 30、结束 7 个应用进程；测试实例已停止。本地 114 项单元测试、编译和差异检查通过。实现提交 `05c20f3` 的 Python 3.10 / 3.14 CI 均通过：[运行记录](https://github.com/Marcus1337X/termux-firefox-persona/actions/runs/34109507645)。

## 2026-09-07：私有字体集合与旧身份重新验证

新增 `linux-firefox-fonts-glx-v1`：中文/英文 × 明暗四个组合均已通过本机完整资格检查，合格组合增至 20 个。明色使用 DejaVu Sans/Serif/Sans Mono、Noto Sans CJK SC、Noto Color Emoji；暗色将 DejaVu Serif 换为 Noto Serif CJK SC。没有安装 Liberation。

- Fontconfig 通过 ctypes 查询实际字体；找不到指定 family 时拒绝系统 fallback。每个实例独立配置、字体目录和缓存，TTC 中未选地区的字面也被排除。
- Firefox 的 `font.system.whitelist` 会禁用所有 CSS `local()`，因此不启用该 pref；以私有 Fontconfig 约束集合，并禁用 Firefox 捆绑字体。
- `local_names` 保存真实完整字体名称，例如 `Noto Sans CJK SC Regular`。探针要求选定字体加载成功、排除字体及随机不存在的字体加载失败。
- Canvas 使用固定不透明背景，检查非空文字、重复像素、直接 family 与本地 FontFace 的完整 TextMetrics/像素匹配，以及两种 PNG 导出解码结果。资格判定核对实际 hash 和测量，不能只提交通过标志。
- 三个 CSS generic 别名独立验证；CJK/emoji 作为语言回退角色，不冒充 CSS generic。Worker 字体仍为 `not_verified`。
- 环境快照补充 Android 系统字体、Firefox 捆绑字体及 Fontconfig 配置变化。原 16 个组合均通过显式 `requalify` 在新快照下重新验证。
- 实机验证旧严格 Persona `persona_c9a8c8f7df024ae597eb5242a0b5937f` 的 ID、种子、最终配置、profile 目录及原文件保持，只有环境与资格更新。失败、取消、并发、环境再变和版本不兼容有单元测试覆盖。

本地 106 项单元测试通过，编译及差异检查通过。字体双实例 12 组生命周期验收全部通过，种子为 1/4，身份为 `persona_d06e5b276eaa4edcba329eb8d22074d6` / `persona_5c3d31c952f74635a31c4e7319d69b5f`。两份截图已检查；双实例进程数 30，结束时 7，测试实例均已停止。完整探针后重启同一 profile 再进入双实例阶段，避免探针保留的内容进程占用预算。实现提交 `20bc86d` 的 Python 3.10 / 3.14 CI 均通过：[运行记录](https://github.com/Marcus1337X/termux-firefox-persona/actions/runs/34108013570)。

## 2026-09-07：地理位置与外观组合

新增 `linux-firefox-region-appearance-v1` 的四个组合：上海/纽约各自配明暗外观，坐标和时区受预设约束，精度为 50 米。已有八个基础组合保持原验证范围。

- 地理位置在空白页阶段通过 BiDi 安装到实例 user context，网站仍使用原生权限流程。
- 本地探针检查初始 `prompt`、授权后坐标与精度、拒绝后的错误码 1，以及最后恢复到 `prompt`。权限变更只针对临时 loopback origin。
- 四项外观查询纳入必需资格：配色、减少动画、对比度、forced-colors。Worker 不适用的接口单独记录。
- 四个组合均在当前 K60 至尊版通过完整资格检查，已进入本机合格池。
- 重启后再次取得正确位置，原页面权限保持 `prompt`。
- 原生 Ctrl+N 验收发现上游将组合键当作普通字符串丢弃；输入层已增加修饰键解析与事件回归测试。

地区外观模板的八组窗口验收全部通过，覆盖双实例隔离、恢复、定位权限、脚本弹窗、原生 Ctrl+N 与主窗口联动关闭。本轮本地 79 项单元测试通过；实现提交 `8e2faeb` 已推送，Python 3.10 / 3.14 的 [CI 检查](https://github.com/Marcus1337X/termux-firefox-persona/actions/runs/34103445934) 均通过。字体和核显模板仍未完成资格验证，第三批的持久权限与网络能力也未据此宣称完成。

## 2026-09-07：软件 GLX 与实际 WebGL 绘制

三个独立 profile 的串行对照确认：默认路径及强制 EGL 均报 `FEATURE_FAILURE_NO_DISPLAY`；仅加入 `gfx.x11-egl.force-disabled=true` 的 GLX 路径成功，Firefox 返回 Mesa / `llvmpipe, or similar`。未修改全局环境或旧模板的渲染策略。

新增 `linux-firefox-software-glx-v1`，四个地区外观组合全部通过本机资格检查。它请求实际软件后端，并验证 Window 的 WebGL1/2：

- 顶点与片元 shader 编译、链接。
- 覆盖整个小画布的三角形及精确红色 RGBA 像素读回。
- RGBA8 texture、framebuffer completeness 与精确绿色 RGBA 像素读回。
- 两种 context 各自的真实 vendor/renderer；记录后再释放 context。

资格判定逐项检查证据，错误像素即使伴随 `passed=true` 也会失败。precision、limits、extensions 保留为查询观测，Worker WebGL 标记 `not_verified`。这不是核显身份或完整第二批资格。

连同旧八个基础组合与四个地区外观组合，当前本机合格组合共 16 个。软件 GLX 的八组窗口验收全部通过，包含双实例、定位与图形资格恢复、脚本弹窗、Ctrl+N 及主窗口联动。双实例观测为 30 个应用进程，测试实例均已停止。

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
- 原生键盘输入、默认 CSS 路径的 DOM 合成点击及私有路径截图通过；标签页继承与刷新保持 Persona。该次点击未验证 `isTrusted` 或可信用户激活。
- 两个不同 DISPLAY 的 Persona 同时运行，分别导航和输入，localStorage 与持久 cookie 相互隔离。
- 普通派生新窗口继承，以及关闭主窗口联动结束派生窗口和自有实例，通过实机验收。修复窗口定位后，六组实机验收全部通过。
- 双实例运行时应用进程数为 30，测试前后均为 6；本次清理回到基线。
- 停止 A 后 B 继续运行；重新启动 A 后身份配置、localStorage 和持久 cookie 保持。此结果不包含 session cookie 永久保存承诺。

未完成或需继续验证：

- 脚本弹窗、标签页拖出及手动 Ctrl+N 尚未分别验收；通过 BiDi 创建的普通派生新窗口不能替代这些入口的验证。
- 第一批剩余 Liberation 字体、标签页拖出和未覆盖的初始/动态生命周期行为；地区/外观与 DejaVu/Noto 字体见上方后续验收。
- WebGL 创建失败，已取得 `FEATURE_FAILURE_NO_DISPLAY` / `EXHAUSTED_DRIVERS` 错误；尚无合格核显身份模板。基础真实后端组合通过不代表核显模板通过。
- 第二批完整图形、Canvas、Audio、媒体和设备行为，以及第三批其余存储、权限、隐私和网络验收。
- 环境变化后的旧 Persona 会拒绝直接恢复；现已提供上文所述 requalify，Firefox 版本不兼容时仍需创建新身份。
- 后续批次继续使用私有仓库同步，CI 不替代本机资格。

两实例通过表明本次条件下的并发可用，不代表更高并发上限或任意重页面都已验证。最初多进程测试曾导致 Termux 中断，现有预算属于本地准入估算，后续负载仍需按设备能力控制。

两个计划中的勾选仅对应已取得证据的明确子项；完整组合条目及尚未验收的其他批次保持未勾选。
