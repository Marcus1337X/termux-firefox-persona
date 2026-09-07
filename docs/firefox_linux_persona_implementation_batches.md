# Firefox Linux Persona 分批实现清单

## 本轮进度更新（2026-09-07）

本地 114 项单元测试通过。新增 `linux-firefox-workers-glx-v1` 四个组合全部完成实机资格验证；原有 20 个组合保持有效，当前合格池共 24 个组合。

新模板要求 Dedicated、Shared、Service Worker 分别通过本地字体正反向加载、三个通用别名、OffscreenCanvas 文字像素与 TextMetrics、convertToBlob PNG 解码，以及 WebGL1/2 shader/红色像素/RGBA8 framebuffer 绿色像素检查。各 Worker 字体渲染及图形身份还必须与 Window 一致。Worker 不具备的 toDataURL 明确标为不适用，缺失任一 Worker 证据不授予完整资格。

15 组实机生命周期验收全部通过，包括不同字体的双实例隔离、原生输入与截图、标签页/弹窗/Ctrl+N 继承、重启后 Window 与三类 Worker 的实际像素/测量/GL 结果保持。双实例应用进程数为 30，测试实例全部停止。完整探针在单实例阶段执行，再重启原 profile 进入双实例阶段，不提高预算。本轮 CI 待推送后记录。

此前地理位置、四项外观、私有字体及保留 ID/种子/最终配置/profile 的 requalify 继续有效。Worker 图形探针仅在新模板启用，旧模板保持原资格范围。Liberation、完整 graphics precision/limits/extensions、核显身份、媒体/网络及标签页拖出仍未完成，完整第一批和第二批尚未全部完成。下列勾选只表示明确范围内已有实机证据。

> 目标：在 Android Termux + Termux:X11/Xvfb 中，为每个独立的 Firefox 顶层窗口创建一套经过约束的 Linux Firefox Persona。Persona 来自预先定义的合理模板，并在模板约束内随机组合；它不是允许用户逐字段填写的任意伪装配置。

## 目标、边界与术语

- [ ] 将独立的 Firefox 顶层 X11 Window 作为 Persona 隔离单位；同一窗口中的所有标签页继承该窗口 Persona。
- [ ] 为每个窗口从预设模板池抽取一套完整 Persona，再在兼容范围内随机选择 locale、分辨率、字体、设备能力等变体。
- [ ] 保存随机种子、最终配置、模板版本和能力验证结果；刷新、导航、新标签页、Firefox 重启和窗口恢复都复用同一套 Persona。恢复以已保存的最终展开配置为准；种子用于重现和诊断，并同时记录生成器/schema 版本和能力快照标识。
- [x] 允许不同窗口抽到相同模板或相同最终配置；随机可复现优先于强制唯一，不能把“随机”误解为每个窗口必须不同。
- [ ] 默认运行策略只从 `validated` 模板中随机抽取，并要求该模板的必需字段通过全链路验证；`partial` 能力只进入显式实验池，不混入严格随机池。
- [ ] 不提供“输入任意 GPU 名称、编码器或扩展后强行返回成功”的默认路径。能力未知时先探测，探测失败时记录 `partial` 或 `unsupported`。
- [ ] 允许预设中的 GPU 身份与 Termux 实际执行后端分离，但必须记录实际后端和验证范围；名称、性能接近不等于功能等价。
- [ ] 核显可以作为预设候选，例如 Intel/AMD 集显族；当前 K60 至尊版的 Termux 实际 GPU 作为执行后端候选，具体型号、Mesa/Android 映射和可用扩展必须在设备上实测后才能进入 `validated`。
- [ ] Canvas、WebGL shader、Audio、编解码器和媒体设备等行为以 Firefox 与 Termux 的真实能力为基础验证；只修改字符串、扩展列表或能力查询结果不能使底层获得相应能力。
- [x] 运行时 profile、cookie、缓存和状态只放在 Termux 私有目录；仓库或共享存储可保存源码、文档、测试夹具和不含隐私的模板数据。

## 能力状态与模板状态

能力状态和模板状态必须分开记录，不能用一个布尔值表示“已经支持”。

| 状态 | 含义 | 是否进入默认严格随机池 |
|---|---|---|
| `supported` | 在目标 Termux/Firefox 版本上有真实实现，并通过所需 Window、Worker、HTTP 或设备行为检查 | 可以 |
| `partial` | 只覆盖部分 context、部分能力或有明确环境限制；必须说明缺口 | 不可以，仅显式实验 |
| `unsupported` | 当前 Firefox、Termux、X11 或设备无法可靠提供 | 不可以 |

| 模板状态 | 含义 |
|---|---|
| `candidate` | 设计上合理，但尚未完成当前设备的端到端验证 |
| `validated` | 模板所需字段和跨 context 关系均通过资格验证，可被严格随机抽取 |
| `disabled` | 与当前环境冲突、验证失败或已知不可用，不得抽取 |

每个字段和模板至少记录：请求值、Firefox/Termux 实际值、实现层、能力状态、验证命令或测试结果、适用版本和禁用原因。模板只有在必需字段全部为 `supported` 且一致性检查通过后才可从 `candidate` 变为 `validated`。

## Persona 与实例隔离模型

Persona 使用稳定的 `persona_id` 作为持久化主键；管理器分配的 `instance_id` 标识运行实例，并映射到 persona_id、PID、XID 与浏览上下文。恢复时可以更换 instance_id，但不能更换保存的 Persona；不能使用 X11 Window ID 作为持久化主键。一个实例至少包含以下资源：

```text
instance_id
├── 独立 Firefox 进程树
├── 独立 Termux 私有 profile 目录
├── 独立 BiDi/控制端口
├── 独立 persona.json、随机种子和生命周期状态
└── 可选的独立 DISPLAY/Xvfb
```

- [x] 启动实例时使用独立 Firefox 进程树和独立 profile，避免固定 profile、固定 socket、固定 PID 文件以及已有 Firefox 进程串用。
- [ ] 为每个实例分配不会冲突的控制端口、状态路径和临时路径；关闭、崩溃恢复和重启时按 `instance_id` 清理和恢复。
- [ ] 可以在同一 DISPLAY 中管理多个顶层窗口；需要不同原生屏幕尺寸或显示环境时，才为实例分配独立 Xvfb/DISPLAY。
- [x] 独立 DISPLAY 的生命周期不能终止其他实例正在使用的 Xvfb，也不能依赖全局可变 `DISPLAY`。
- [ ] Popup、普通新窗口和脚本创建的顶层窗口默认继承创建源窗口的 Persona，并继承其 profile/控制上下文。
- [ ] 关闭拥有派生 Popup/新窗口的主窗口时，结束该 Persona 自有的派生窗口/实例；不得影响其他 Persona 的实例。
- [ ] 需要新建一套 Persona 时，只能通过管理器的显式创建入口分配新 `instance_id`、新 profile 和新状态；不得把窗口拖出、XID 变化或标签页变化当作新 Persona。
- [ ] 将输入、截图、导航和关闭操作路由到实例上下文，验证关闭一个实例不会改变其他实例的 profile、Persona 或 DISPLAY。
- [x] 记录“管理器配置”和“运行时 profile”的边界；不把浏览历史、cookie、token 等运行数据写入共享存储。

---

# 第零批次：隔离底座、模板系统与能力资格验证

第零批必须先完成。后续批次的字段只有在这里具备独立实例、状态持久化和探测机制后才有可验证的归属。

## 实例和状态底座

- [ ] 梳理并复用现有 Firefox、X11/Xvfb、xdotool、截图、输入、session 和窗口管理能力，明确哪些状态当前是全局变量。
- [ ] 建立实例管理器：创建、启动、查询、关闭、崩溃恢复、重启和恢复实例均按 `instance_id` 执行。
- [x] 为每个实例创建独立 profile、控制端口、进程树、Persona 配置和生命周期状态，并检查路径与端口冲突。
- [x] 使用 Firefox 的独立 profile/启动参数隔离实例；运行 profile 存于 Termux 私有目录，启动前检查权限和目录可用性。
- [ ] 实现可选独立 DISPLAY/Xvfb，并验证多个实例互不终止、互不改写对方的显示环境。
- [x] 保存模板 ID、模板版本、随机种子、最终字段、实际后端、能力状态和校验摘要，使配置可复现而不是每次重新抽签。
- [ ] 规定标签页、Popup、普通新窗口、拖出标签页和管理器新实例的 Persona 继承规则，并用实际 Firefox 行为验证。
- [ ] 验证两个以上实例可同时运行、分别导航、刷新、新建标签页、截图和关闭，且 Window A/B 不发生输入、状态或配置串用。

## 预设模板和受约束随机组合

- [ ] 定义模板的兼容性约束：浏览器版本与 UA、platform/oscpu、CPU 线程数、显示尺寸/DPR、locale/语言/时区、字体、GPU/媒体能力之间必须能同时成立。
- [ ] 为每类随机字段定义允许集合、依赖关系、互斥关系和必需字段；先抽取模板，再在模板允许的集合内生成变体。
- [x] 为每个 Persona 固定随机种子并持久化，确保重启和恢复得到同一结果；模板池或验证版本变化时拒绝静默复用不兼容配置。
- [x] 默认入口只允许 `validated` 模板；提供单独的诊断入口加载 `candidate`/`partial` 模板，并明确标记为实验结果。
- [x] 对无法满足约束的组合重新抽取或标记失败，不通过 JavaScript 覆盖隐藏矛盾。
- [ ] 建立模板校验器，检查字段完整性、依赖关系、状态门槛和跨 context 预期值。
- [ ] 建立能力清单，区分 Firefox 原生能力、profile/prefs、X11/Xvfb、fontconfig、Mesa/图形栈、PulseAudio/PipeWire、BiDi、网络层和页面级补丁的责任边界。

## 能力探测和早期跨 context 验证

- [ ] 在目标 Termux Firefox 上探测可用的 WebDriver BiDi 命令、权限接口、网络配置接口和预加载范围；未知命令不得直接标记为支持。
- [x] 建立最小资格探针，覆盖 Window、Dedicated Worker、Shared Worker、Service Worker（可用时）和 HTTP headers，并记录每个 context 的结果。
- [x] 尽早验证 UA/platform、hardwareConcurrency、locale、timezone、Accept-Language、screen/viewport/DPR 的 Window/Worker/HTTP 关系。
- [ ] 明确 document/worker preload 的适用范围；不能把 document preload 当作所有 Worker 类型的通用兜底，Worker 结果必须逐类验证。
- [ ] 对字体、Canvas、WebGL、Audio、codec 和 media devices 建立可重复的行为探针，不以单一查询 API 的返回字符串作为资格证明。
- [ ] 建立“请求值/实际值/证据/状态”报告；探测不通过时将能力降级为 `partial` 或 `unsupported`，不静默宣称成功。

## CI 与 Termux 资格测试边界

- [x] 在 CI 中运行模板 schema 校验、随机种子复现、兼容性规则、状态门槛、配置序列化、静态检查和不依赖 GUI 的一致性逻辑测试。
- [ ] 在 CI 中模拟多实例状态机、端口/profile 分配、Popup 继承、关闭恢复和跨 context 期望值检查。
- [ ] 将真实 Termux、Termux:X11/Xvfb、Firefox GUI、xdotool、GPU、字体、音频、摄像头/麦克风和编解码器资格测试列为本地设备验收。
- [ ] 明确 CI 通过不能把 `candidate` 模板提升为 `validated`，也不能代替当前 K60 至尊版上的 Firefox/Termux 实测。

---

# 第一批次：基础浏览器、设备、显示、地区与字体特征

第一批只纳入能形成稳定基础 Persona 的字段；每一项完成实现、跨 context 检查和 Termux 实测后再勾选。

## 浏览器 / 平台

- [x] `User-Agent`
- [x] `navigator.userAgent`
- [x] `navigator.appVersion`
- [x] `navigator.platform`
- [x] `navigator.oscpu`
- [x] Firefox 实际版本关联信息
- [x] 验证 UA、platform、oscpu、Firefox 真实版本和模板之间的关联，不生成与实际 Firefox 构建明显冲突的组合。

## CPU

- [x] `navigator.hardwareConcurrency`
- [x] `WorkerNavigator.hardwareConcurrency`
- [ ] 验证 Window 与 Worker 一致，并记录实际线程数、模板值和运行性能之间的限制。

## 显示环境

- [x] `screen.width`
- [x] `screen.height`
- [x] `screen.availWidth`
- [x] `screen.availHeight`
- [x] `screen.colorDepth`
- [x] `screen.pixelDepth`
- [x] `devicePixelRatio`
- [x] viewport width
- [x] viewport height
- [x] `window.innerWidth`
- [x] `window.innerHeight`
- [x] `window.outerWidth`
- [x] `window.outerHeight`
- [ ] `window.screenX`
- [ ] `window.screenY`
- [ ] screen orientation
- [ ] 验证 screen、viewport、窗口尺寸、位置和 DPR 的数值关系；不同 DISPLAY 只在实际 X11/Xvfb 能提供时加入严格模板。

## 语言 / 地区

- [ ] `navigator.language`
- [x] `navigator.languages`
- [ ] Worker `navigator.language`
- [x] Worker `navigator.languages`
- [x] `Accept-Language`
- [x] timezone
- [x] Worker timezone
- [ ] 验证 locale、languages、Accept-Language、timezone 和模板地区之间的合理组合，并记录 HTTP 层实测值。

## 地理位置

- [x] latitude
- [x] longitude
- [x] accuracy
- [ ] 验证 geolocation 权限、返回值和 timezone/locale 的关系；没有真实权限链路时标记 `partial`。

## 外观

- [x] `prefers-color-scheme`
- [x] `prefers-reduced-motion`
- [x] `prefers-contrast`
- [x] `forced-colors`
- [ ] 验证媒体查询结果与 Firefox profile、X11 外观和模板设定的一致性。

## 字体

- [x] DejaVu 字体族
- [ ] Liberation 字体族
- [x] Noto 字体族
- [x] Noto Color Emoji
- [x] 按 locale 配置 Noto CJK 等语言字体
- [x] 在 Termux 中确认字体实际安装、fontconfig 查找顺序和 Firefox 可见字体集合。
- [x] 用字体枚举、Canvas、TextMetrics 和截图探针验证字体不是仅写入配置文件；结果不稳定时模板不得进入严格池。

## 推荐实现层与本批验证

- [ ] X11 / Xvfb
- [ ] Firefox profile / prefs
- [ ] WebDriver BiDi
- [ ] fontconfig
- [ ] 必要时使用 document / worker preload，并逐 context 验证其实际作用范围
- [ ] 为每个字段生成请求值、实测值、状态和证据报告。

---

# 第二批次：图形、Canvas、音频、媒体与输入

第二批必须以真实能力探测为入口。GPU 预设可以描述合理的候选身份，但不能仅通过伪造 renderer、extensions、limits 或 codec 查询把候选提升为已验证能力。

## WebGL / GPU

- [x] WebGL vendor
- [x] WebGL renderer
- [x] WebGL2 availability
- [ ] WebGL extensions
- [ ] `MAX_TEXTURE_SIZE`
- [ ] `MAX_CUBE_MAP_TEXTURE_SIZE`
- [ ] `MAX_RENDERBUFFER_SIZE`
- [ ] `MAX_VIEWPORT_DIMS`
- [ ] `MAX_VERTEX_ATTRIBS`
- [ ] `MAX_VERTEX_UNIFORM_VECTORS`
- [ ] `MAX_FRAGMENT_UNIFORM_VECTORS`
- [ ] shader precision
- [ ] framebuffer capabilities
- [ ] texture format capabilities
- [ ] GPU / Mesa capability profile
- [ ] 记录身份预设、Firefox graphics stack、Termux/Android 实际后端和能力子集之间的关系。
- [ ] 以 shader 编译、精度、纹理/帧缓冲、扩展调用和 limits 行为验证能力；只覆盖 vendor/renderer 字符串或扩展列表不能通过验证。
- [ ] 将 Intel/AMD 集显族作为可选候选模板时，先标记 `candidate`；具体型号和扩展只有在目标设备实测后才能成为 `validated`。
- [ ] 将 K60 至尊版的实际 GPU/驱动能力作为执行后端单独记录；性能等级接近某个集显模板不能推断其 renderer、扩展、精度或兼容性完全相同。
- [ ] 验证软件渲染、Android GPU 映射、Mesa/virgl 等实际路径；不可用后端能力标记 `unsupported`，依赖该后端的模板标记 `disabled`。
- [ ] 在第二批结束前，至少完成一个当前设备上真实运行的核显模板的端到端验证并标记为 `validated`；若无法通过，模板保留为 `candidate` 或标记 `disabled`，能力按情况标记 `partial`/`unsupported`，明确该批未完成。软件或其他后端可以承担实际执行，但必须通过同一身份与功能兼容验收并如实记录后端；仅验证真实后端可用不能冒充核显模板通过。

## Canvas

- [ ] Canvas 2D rendering
- [x] 字体 Canvas rendering（新 Worker 模板的 Window 与 Dedicated/Shared/Service 范围）
- [x] `getImageData()`（新 Worker 模板的 Window 与 Dedicated/Shared/Service 范围）
- [x] `toDataURL()`（当前字体模板的 Window 范围）
- [x] `toBlob()`（Window）与 `convertToBlob()`（三类 Worker），含 PNG 解码像素验证
- [x] `TextMetrics`（新 Worker 模板的 Window 与 Dedicated/Shared/Service 范围）
- [x] WebGL Canvas rendering（新 Worker 模板的 Window 与三类 Worker 基础 shader/像素/FBO 范围）
- [ ] 通过固定测试图、像素输出、字体测量和 WebGL 结果验证；允许记录实际后端差异，但不把固定字符串当作像素行为。

## Audio

- [ ] `AudioContext.sampleRate`
- [ ] `AudioContext.state`
- [ ] Audio analyser behavior
- [ ] channel capabilities
- [ ] output latency
- [ ] audio input / output device environment
- [ ] 验证 AudioContext、采样率、声道、延迟、输入/输出设备和 PulseAudio/PipeWire/Termux 后端；没有设备或权限时标记 `partial`。

## 媒体能力

- [ ] `MediaCapabilities`
- [ ] H.264 support
- [ ] VP8 support
- [ ] VP9 support
- [ ] AV1 support
- [ ] AAC support
- [ ] Opus support
- [ ] WebRTC capabilities
- [ ] camera devices
- [ ] microphone devices
- [ ] media device labels
- [ ] 以实际解码、播放、编码/传输（适用时）和 WebRTC 行为验证 codec；只修改 `MediaCapabilities` 查询结果不能表示 Firefox 真能播放。
- [ ] 验证 camera/microphone 枚举、权限和设备标签；没有实际设备时模板不得宣称存在真实设备。

## 输入设备

- [ ] mouse
- [ ] keyboard
- [ ] pointer fine / coarse
- [ ] hover capability
- [ ] `maxTouchPoints`
- [ ] touch capability
- [ ] wheel / scroll behavior
- [ ] 通过 X11/xdotool 和页面事件验证输入能力，不把页面级属性覆盖当成真实输入设备。

## 窗口交互

- [ ] focus
- [ ] blur
- [ ] resize behavior
- [ ] scroll state
- [ ] fullscreen state
- [ ] active / background tab state
- [ ] `document.visibilityState`
- [ ] 验证多个实例切换和同实例标签页切换时的焦点、可见性、全屏和窗口状态归属。

## 推荐实现层与本批验证

- [ ] Mesa / virgl / GPU 环境
- [ ] Firefox graphics stack
- [ ] fontconfig
- [ ] PulseAudio / PipeWire
- [ ] 虚拟 camera / microphone（仅在 Termux 实际可用且通过行为验证时加入模板）
- [ ] X11 / xdotool
- [ ] Firefox media configuration
- [ ] 建立图形、像素、shader、音频、codec 和设备行为探针；将结果写入能力报告并按状态门槛更新模板。

---

# 第三批次：Profile、权限、存储、隐私、网络与生命周期

第三批补全持久化和外部环境，同时把第一、第二批形成的字段放入完整的跨 context 和生命周期测试。网络模拟和权限能力按 Termux/Firefox 实际接口提供，不能承诺无 root 的系统级丢包、DNS 或 UDP 隔离。

## Browser Profile

- [ ] cookies
- [ ] session cookies
- [x] `localStorage`
- [ ] `sessionStorage`
- [ ] IndexedDB
- [ ] Cache API
- [ ] HTTP cache
- [ ] Service Workers
- [ ] site preferences
- [ ] HTTP authentication state
- [ ] 验证跨 Persona 的上述数据隔离，关闭/恢复行为符合各存储类型原生语义。同一 Persona 内按 origin、标签页和浏览上下文保留正常共享/隔离规则；尤其 sessionStorage 不得错误合并为整个 profile 共享，session cookies 不承诺永久保存。

## 权限

- [ ] geolocation permission
- [ ] notification permission
- [ ] camera permission
- [ ] microphone permission
- [ ] clipboard permission
- [ ] persistent permission state
- [ ] 验证权限状态、页面 API 返回值、profile 持久化和 Popup/新窗口继承关系；不存在的设备不能通过权限记录伪造出来。

## 隐私设置

- [ ] Do Not Track
- [ ] Global Privacy Control
- [ ] Tracking Protection configuration
- [ ] cookie policy
- [ ] 验证 prefs、HTTP 结果、页面可见值和模板隐私策略保持一致。

## 网络环境

- [ ] online / offline
- [ ] latency
- [ ] bandwidth
- [ ] packet loss
- [ ] proxy configuration
- [ ] DNS environment
- [ ] connection transitions
- [ ] 为每个网络字段记录实际隔离层和适用范围；代理可按实例隔离时纳入模板，无法由当前权限稳定实现的项目标记 `partial`/`unsupported`。
- [ ] 验证 Window、Worker、Service Worker（适用时）和 HTTP 请求实际看到的网络变化，而不是只设置查询接口。

## 页面生命周期

- [ ] navigation history
- [ ] page reload state
- [ ] visibility transitions
- [ ] focus transitions
- [ ] fullscreen transitions
- [ ] orientation transitions
- [ ] 验证导航、刷新、后台/前台切换、全屏、方向变化和恢复操作不会丢失或串用 Persona。

## 跨 Context 一致性

- [x] Window / Worker `hardwareConcurrency` 一致
- [x] Window / Worker locale 一致
- [x] Window / Worker timezone 一致
- [x] UA / platform / Firefox version 一致
- [x] `Accept-Language` / `navigator.languages` 一致
- [x] screen / viewport / DPR 数值关系一致
- [ ] timezone / locale / geolocation 组合一致
- [ ] fonts / Canvas / `TextMetrics` 一致
- [ ] WebGL renderer / extensions / limits 一致
- [ ] Audio capabilities / codec capabilities 一致
- [ ] media devices / permission state 一致
- [ ] Window、Dedicated Worker、Shared Worker、Service Worker 和 HTTP 层的支持范围分别记录；未验证的 context 不得被模板标记为全链路 `supported`。

## 推荐实现层与本批验证

- [ ] 独立 Firefox profile；user context 可辅助实现，但不能替代实例/profile 隔离
- [ ] Firefox permission manager
- [ ] WebDriver BiDi
- [ ] 网络模拟层
- [ ] X11 窗口状态
- [ ] 自动化一致性测试脚本
- [ ] 完成实例并发、窗口关闭、重启恢复、Popup 继承、新 Persona 创建、profile 隔离、权限隔离和网络状态隔离的 Termux 验收。

---

# 最终验收清单

## Persona 与实例

- [x] 默认创建窗口时从 `validated` 预设模板池按约束随机抽取，并持久化模板版本、随机种子和最终配置；不同窗口不要求最终配置唯一。
- [x] `candidate`、`partial` 和 `unsupported` 不会被默认严格随机池静默使用；实验入口会明确显示限制。
- [x] 可以在同一个 Termux 环境中同时运行多个独立 Firefox 顶层窗口，并分别应用不同 Persona。
- [x] 同一 Firefox 窗口内新建标签页后继续继承该窗口 Persona，不能被当成新的 Persona。
- [x] Popup 和普通新窗口按约定继承源 Persona；管理器显式创建的新实例才分配新 Persona。
- [x] 关闭一个拥有派生窗口/实例的主窗口时，其派生窗口/实例随该 Persona 结束；其他 Persona 继续运行。
- [ ] Window A 与 Window B 之间切换、刷新、导航或新建标签页时，不发生 Persona、输入、profile 或状态串用。
- [x] 关闭一个 Persona 的窗口不影响其他 Persona 的运行状态；同 Persona 派生窗口遵循前述主窗口关闭规则。
- [x] 重新创建 Firefox 顶层窗口时，可以根据 `persona_id` 稳定加载指定 Persona并建立新的运行实例映射，不依赖 XID。

## 字段与一致性

- [ ] 第一批次中的 UA、platform、CPU、screen、viewport、DPR、语言、时区、地理位置、外观和字体按窗口正确生效或有明确状态。
- [ ] 第二批次中的 WebGL、Canvas、Audio、媒体和输入特征按实际能力生效，相关能力之间不存在已知矛盾。
- [ ] 第三批次中的 profile、权限、storage 和网络状态按设计隔离，不同窗口之间不会意外污染。
- [ ] Window、Worker 和 HTTP headers 中涉及同一环境属性的结果保持一致，未覆盖的 context 明确记录。
- [ ] screen、viewport、DPR、窗口尺寸和位置保持合理关系。
- [ ] locale、languages、Accept-Language、timezone 和 geolocation 保持合理一致。
- [ ] fonts、Canvas、TextMetrics、WebGL 和媒体能力与已验证模板相符；性能接近不作为功能一致的证明。
- [ ] 某项能力受 Termux、X11、Firefox 或真实设备限制无法可靠实现时，明确标记为 `partial`/`unsupported`，不静默伪装为成功。
- [ ] 未知能力先经过探测；document/worker preload 不被当作所有 context 的通用兜底。

## 工程与验证

- [x] 创建 GitHub 私有开发仓库并从 Termux 验证 push/pull；保留上游许可证，不提交真实 profile、cookies、凭据或会话数据。
- [ ] Persona 功能不破坏 `termux-browser-pilot` 原有的 Firefox 浏览、截图、点击、输入和 session 能力。
- [x] GitHub Actions 自动执行所有适合 CI 的模板、状态机、配置和逻辑测试，并能看到明确的通过/失败结果。
- [ ] 真实 Termux/X11/Firefox/GPU/音频/设备资格测试保留为本地验收，并与 CI 范围明确区分。
- [ ] CI 通过不会自动替代当前设备资格验证，也不会单独把模板状态提升为 `validated`。
