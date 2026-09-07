# Firefox Linux Persona 任务说明

## 本轮进度更新（2026-09-07）

本地 137 项单元测试、编译和差异检查通过。新增 `linux-firefox-audio-glx-v1` 四个组合，继承 Window 与 Dedicated/Shared/Service Worker 字体和 WebGL 资格，并增加 `audio_offline`、`audio_realtime`。四个新组合 bootstrap 和旧 24 个组合在 schema 3 下重验全部通过，旧身份 ID、种子、最终配置保持，当前本机合格池共 28 个组合。实现提交 `90bfb9e` 的 Python 3.10 / 3.14 CI 均通过：[运行记录](https://github.com/Marcus1337X/termux-firefox-persona/actions/runs/34112107654)。

明色/暗色组合分别配置原生默认 AudioContext 44100/48000 Hz。Offline 探针两次实际渲染双声道、2048 帧 buffer/gain 图并核对样本、hash、结构和自然状态；Realtime 探针通过 BiDi `click_native` 可信点击激活，检查状态转换、时钟及 analyser 波形/频谱，末端静音并清理资源。旧 `click` 的默认 CSS 左键路径优先使用 DOM 合成点击，不能保证可信激活；其 human/坐标路径另用 xdotool。历史验收使用默认 CSS 路径，点击成功不能证明 `isTrusted` 或可信用户激活。

音频范围仅覆盖浏览器图内运算：物理输入/输出为 `not_verified`，延迟只作诊断，Worker Audio 为 `notapplicable`。环境 schema 3 纳入音频配置和可获得的服务端事实，旧 24 个组合已显式重新验证通过。生命周期脚本已扩展双实例默认采样率及新标签、刷新、弹窗、Ctrl+N、重启继承；重启比较实际 Offline 证据及 Realtime 稳定结构/状态，排除时钟、相位与延迟。16 组 Audio 实机生命周期验收全部通过，含不同默认采样率、实际 Offline/analyser、重启证据保持以及 `isTrusted=true` 点击和弹窗；应用进程数为基线 7、双实例 31、结束 7，测试实例已停止。

命令：`python cli.py persona bootstrap --template linux-firefox-audio-glx-v1`；通过后执行 `python -m tests.persona_live --template linux-firefox-audio-glx-v1`。旧身份使用 `python cli.py persona requalify PERSONA_ID` 保留 ID、种子、最终配置和 profile 重新验证。

此前 Worker 模板的四个组合及旧 20 个组合在上一环境取得资格，15 组生命周期检查通过；实现提交 `05c20f3` 的 [CI 记录](https://github.com/Marcus1337X/termux-firefox-persona/actions/runs/34109507645) 仅对应该历史实现。旧模板资格范围不扩大；Liberation、完整 graphics precision/limits/extensions、核显身份、物理音频/媒体/网络及标签页拖出仍未完成。以下勾选只对应已取得实机证据的明确子项，Audio 只勾选本轮实际通过的采样率、状态、analyser 和双声道图内运算，完整声道、物理设备及延迟仍未完成。

## 目标与范围

基于 <https://github.com/salviz/termux-browser-pilot> 的 Firefox 模式，开发一套适用于 Android Termux 的 Linux Firefox Persona 系统。

用户设备为 K60 至尊版；开发和运行环境均为 Termux，Firefox 通过 X11/Xvfb 运行。实际绘图、音频、媒体解码和网络操作由该环境中的 Firefox 及其可用后端执行，不要求外部桌面 Linux 机器，也不预设设备已 root。

核心需求是：**建立经过验证的合理预设库，每次创建独立 Persona 窗口时，按约束随机抽取并组合一套自洽特征；生成后保存，同一窗口的全部标签页持续继承。** 默认产品流程不提供任意字段自由拼接；模板维护者通过版本化配置维护候选值、关联规则和验证证据。

允许对外呈现预设的 Linux 桌面身份，同时由 Termux 后端执行真实操作。身份呈现与执行后端可以不同，但不得把修改返回值当作获得真实功能。目标是参数一致、受支持操作可用、行为经过验证；不承诺复制某台桌面机器的性能、逐像素渲染或所有硬件特征。

具体任务和验收按 `firefox_linux_persona_implementation_batches.md` 的第零、第一、第二、第三批推进。实现时同步更新清单；本文规定总体约束，分批清单规定交付顺序和检查项目。

## 预设库与随机生成

### 预设组成

- 浏览器/平台组：Linux Firefox UA、appVersion、platform、oscpu，与实际 Firefox 版本和经过验证的平台呈现方式关联，不引入 Chromium 专属字段。
- CPU 组：选择当前实现可以在 Window 和 Worker 中一致呈现的 hardwareConcurrency 档位，不据此宣称真实 CPU 型号或性能。
- 显示组：screen、工作区、窗口尺寸、viewport、DPR、色深和方向作为关联配置；考虑浏览器工具栏、窗口管理器、缩放和全屏状态。
- 地区组：locale、languages、Accept-Language、时区、地理位置和字体按地区模板组合；可保留合理的多语言情形，不强制语言与国家一一对应。
- 图形组：预设 GPU 身份、WebGL/WebGL2 能力以及实际后端的兼容规则。
- 媒体/输入/外观组：在真实可用能力内选择音频、编解码器、设备、桌面鼠标键盘和外观组合。
- 权限/隐私/网络组：从受支持的权限、隐私和代理策略中选择；代理地址、凭据等真实资源必须来自已配置资源池，不能随机编造。

### 生成规则

1. 探测 Firefox 构建、图形后端、显示、字体、音频和解码能力，形成带版本的本机能力快照。
2. 先选择兼容的主模板，再选择依赖组并生成允许范围内的值；禁止每个字段无约束独立抽签。
3. 对组合执行约束校验；无可用组合时明确返回原因，不自动放宽限制、不静默换成其他 GPU 身份。
4. 首次以空白页面启动，应用所需配置并完成就绪检查后，才允许进入目标页面，避免先暴露默认配置再注入。
5. 持久化最终展开配置、随机种子、模板/生成器/schema 版本和能力快照标识。恢复以最终配置为准，种子仅用于重现与诊断。
6. 默认从合格组合随机抽取，不承诺不同窗口绝不重复。刷新、导航、标签切换和新建标签页不得重新抽取。
7. 重新抽取属于显式的新 Persona 创建操作，默认使用新 profile；运行中的 Persona 不热切换启动期环境。
8. Firefox、Mesa、字体或其他相关后端升级后重新确认兼容性；不兼容时暂停恢复并报告原因，不暗中改变已保存身份。

## 核显预设与执行后端

优先研究常见 Linux Intel/AMD 集成显卡身份模板，不以独立显卡型号为首批目标。具体型号、vendor/renderer 字符串、驱动关联与能力数据须有来源和验证记录，不能凭名称编造，也不在方案阶段认定某型号已经支持。

K60 至尊版作为性能与兼容性实测设备。是否适合作为某核显模板的执行后端，应根据实际 Firefox 渲染路径、WebGL 功能、稳定性和资源占用判断，不能仅由手机性能或跑分相近推断。先确认 Firefox 是否真正使用 GPU；软件渲染和硬件渲染应分别建立能力快照。

- 允许覆盖可验证的 GPU 身份信息，实际绘制继续交给 Termux 中可用的 Mesa/硬件或软件后端。
- 扩展、limits、shader precision、纹理格式和 framebuffer 能力必须与执行行为兼容；不能宣称后端没有的功能。
- 如选择比后端能力更小的子集，查询结果、功能入口、越界行为及相关 API 必须一致；仅把 limits 数字调小或从列表隐藏扩展不算实现。
- 通过代表性 shader、纹理/framebuffer、Canvas 导出和 Worker/OffscreenCanvas 可用路径检查身份与能力组合。无法覆盖的差异要列入模板限制。
- 不要求与真实核显逐像素相同或性能相同；候选模板存在明显功能矛盾时不能进入默认随机库。
- 编解码器必须经 Firefox 实际播放/解码验证，区分编码配置、容器、分辨率和帧率；安装 Termux 外部工具不等于浏览器已获得对应能力。不得只改 `MediaCapabilities` 返回值。

## 窗口与隔离模型

“窗口”指 Firefox 顶层浏览器窗口/X11 Window，标签页不是 Persona 分配单位。

- 每个独立 Persona 使用独立 Firefox 实例（进程树）和 profile，并拥有独立状态、端口、运行目录及生命周期。
- 使用稳定的 persona_id 保存配置；运行期建立 persona_id、实例 PID、X11 Window ID 与浏览上下文的映射，不能用会变化的 Window ID 作为持久身份。
- 通过管理入口创建的新 Persona 窗口重新抽取配置。网页弹窗、普通 Ctrl+N 窗口和从标签页拖出的窗口继承来源实例的 Persona；需要新身份时必须通过管理入口创建独立实例。
- 禁止跨 Persona 实例直接转移标签页和会话状态；如提供“在另一 Persona 打开”，只传递 URL 并重新导航。
- 同一 DISPLAY 下输入和剪贴板操作需要共享互斥与明确窗口定位；避免回退到第一个名称匹配的 Firefox 窗口。
- 需要独立原生屏幕环境时使用独立 Xvfb DISPLAY；相容模板可研究共享显示。独立 Xvfb 窗口不自动显示在同一个 Termux:X11 桌面中，同桌面平铺展示不作为默认验收项。
- 关闭主窗口时，只清理该 Persona 所属的子窗口、Firefox 实例和自有资源；关闭派生窗口不结束其他 Persona。保存的配置/profile 按持久化策略保留。
- 所有进程清理必须基于资源所有权；不得通过固定 DISPLAY、全局 `pkill firefox` 等方式影响其他实例。
- 配置与 profile 隔离不意味着 GPU、音频硬件和系统网络物理隔离，也不保证并发运行时性能互不影响。
- profile、锁、socket 和运行数据放在 Termux 私有可写目录；当前共享存储工作目录可保存方案及源码，不用作默认 Firefox profile 目录。

## 实现原则与现有项目改造

优先复用 Firefox、X11、xdotool、截图、输入、session 等能力。原生 prefs、进程环境、fontconfig、Mesa 和经探测可用的 WebDriver BiDi 优先；必要时才使用覆盖层。

上游评估基线为 `b95eccd3d1abc188c3aa488a23c519ebacc99fcf`，正式开发时重新核对差异。重点改造固定 daemon/profile/socket/PID 路径、模块级覆盖状态、默认 DISPLAY 及其启动清理逻辑、全局环境变量写入、窗口匹配和 Firefox 强制软件渲染设置。现有 session/profile 导出功能不等于完整 Firefox profile 隔离，不能直接替代。

BiDi 作为新增适配层按实际构建探测命令支持，不假定当前项目已经接入。页面 preload 不能直接保证所有 Worker 的覆盖；Dedicated Worker、Shared Worker、Service Worker、跨源 iframe 与可用的 OffscreenCanvas 路径分别验证。HTTP 首次导航和资源请求也必须纳入验证，导航完成后补注入不能满足首次脚本一致性要求。

对外观、焦点、全屏、可见性、AudioContext.state 等动态行为，通过真实状态转换实现，不能为保持模板而固定返回值。模板定义初始条件和行为约束，允许用户操作后的合理变化。

## 能力与模板状态

字段/实现路径状态：

- `supported`：在明确的 Firefox/后端/上下文范围内实现，并通过相关功能验证。
- `partial`：仅部分 API、上下文或行为覆盖，必须记录限制和实际结果。
- `unsupported`：无法可靠实现或缺少必要后端，明确返回原因。

模板状态另行管理：`candidate`（待验证）、`validated`（满足声明范围的验收）、`disabled`（失效/停用）。状态不能互相替代；模板报告保留请求值、实测值、实现方式、验证环境、证据及限制。

默认随机池只选择与当前能力快照相容的 validated 模板，所声明的必需字段须全部通过对应上下文与功能检查。每批明确必需字段和未交付字段，不把阶段完成称为全部完成。partial 组合只能作为显式实验模板，不能混入默认随机池。没有核显模板通过时，应报告核显池为空；真实后端模板可以单独提供，但不能冒充核显模板成功。

## 开发同步与验证

正式开发时创建新的 GitHub 私有仓库同步代码，保留上游许可证与来源，并持续推送开发提交。

GitHub Actions 执行 Python 单元测试、静态检查、schema 校验、模板约束、固定种子重现、持久化/迁移和不依赖设备的路由与隔离逻辑测试。代理凭据、真实 profile、cookies 和会话数据不进入仓库或 CI 日志。

Termux 本地执行 Firefox GUI/X11、真实输入/截图、字体/图形/音频/媒体、跨上下文及 HTTP 回显测试。CI 中的 mock 或其他平台结果不能替代 K60 至尊版的模板资格验证。至少两个不同 Persona 同时运行，并记录内存、稳定性与清理结果；最大并发数由实测确定。

## 最终验收

- [x] 默认流程从预设库带约束随机生成 Persona，无需逐项手填；不合法组合被明确拒绝。
- [ ] 两个不同 Persona 的独立窗口可同时运行，导航/刷新/新建标签页不串用，标签页沿用所属 Persona。
- [ ] 普通新窗口、弹窗和标签页拖出按来源继承；独立新 Persona 通过管理入口创建。
- [x] 最终配置、版本与种子持久化；关闭并恢复后保持身份，显式新建才重新抽取。
- [ ] profile、权限和存储按声明范围隔离；关闭一个 Persona 不破坏另一个实例或原有浏览、截图、输入、session 功能。
- [ ] 第一批基础组满足显示/地区/浏览器关联规则，所声明的 Window、Worker 和 HTTP 属性一致。
- [ ] 第二批至少有一个经本机验证的核显身份模板；如没有合格模板，明确该交付未完成，不以字符串覆盖替代。
- [ ] 图形、Canvas、TextMetrics、音频和媒体完成清单规定的功能验证，实验模板及未支持能力单独报告。
- [ ] 第三批网络、权限和生命周期按声明范围生效，系统级限制不会被当作已实现功能。
- [ ] 模板准入与失效规则可验证，后端升级不会静默更换已保存 Persona。
- [ ] 私有仓库同步和 Actions 检查可用，本地实机验收记录与 CI 结果明确区分。

## 技术依据

- [上游项目与实现基线](https://github.com/salviz/termux-browser-pilot/tree/b95eccd3d1abc188c3aa488a23c519ebacc99fcf)
- [Mozilla：直接建立 BiDi 连接](https://developer.mozilla.org/en-US/docs/Web/WebDriver/How_to/Create_BiDi_connection)
- [W3C：BiDi preload 的作用范围](https://w3c.github.io/webdriver-bidi/#preload-scripts)
- [Mesa：环境变量与能力覆盖限制](https://docs.mesa3d.org/envvars.html)
- [MDN：媒体解码能力查询](https://developer.mozilla.org/en-US/docs/Web/API/MediaCapabilities/decodingInfo)

以上资料用于确定实现边界；命令与后端是否可用仍以开发时安装版本的探测和实测为准。
