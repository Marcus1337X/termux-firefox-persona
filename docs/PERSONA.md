# Linux Firefox Persona 开发版

Persona 从预设模板生成，并为每个身份启动独立 Firefox 实例、profile、控制进程和 Xvfb。相同种子可以生成相同特征，但新创建的 Persona ID 和 profile 不同。恢复按保存的最终配置执行，不重新抽签。

## 安装和启动

在项目目录执行：

```sh
python -m pip install -e '.[persona]'
python cli.py persona doctor
python cli.py persona templates
```

首次使用先在当前 Termux 上逐个验证候选组合：

```sh
python cli.py persona bootstrap --template linux-firefox-native-phase0
```

`bootstrap` 串行启动和停止测试实例，记录 Window、Dedicated/Shared/Service Worker 及首次 HTTP 请求观测。通过的完整组合才进入本机默认随机池。CI 的 mock 测试不能授予本机资格。

然后随机创建并打开一个窗口：

```sh
python cli.py persona create --start
python cli.py persona list
```

命令输出中的 `persona_id` 用于后续操作。以下 `PERSONA_ID` 需要替换成实际 ID：

```sh
python cli.py persona command PERSONA_ID goto --params '{"url":"https://example.com"}'
python cli.py persona command PERSONA_ID eval --params '{"expression":"navigator.languages"}'
python cli.py persona command PERSONA_ID tab_new
python cli.py persona command PERSONA_ID tab_list
python cli.py persona command PERSONA_ID click_native --params '{"target":"button"}'
python cli.py persona command PERSONA_ID type --params '{"target":"input","text":"hello"}'
python cli.py persona command PERSONA_ID screenshot
python cli.py persona probe PERSONA_ID
python cli.py persona stop PERSONA_ID
python cli.py persona start PERSONA_ID
```

`click_native` 使用 BiDi 指针动作发送浏览器原生鼠标输入，可触发 `event.isTrusted=true` 的点击；支持 CSS `target` 或 viewport 的 `x`/`y`，以及 `button`（left/middle/right）和 `count`（1–3）。旧 `click` 的默认 CSS 左键路径优先使用 DOM 合成点击，不能保证可信激活；它另有 human/坐标的 xdotool 路径。音频探针使用 `click_native` 并核对实际事件的 `isTrusted`。

大部分上游 action 通过独立控制进程复用。Persona 的 UA/地区不能通过旧的覆盖命令随意改变；新身份使用 `create`。诊断候选使用 `create --experimental --template TEMPLATE_ID`，明确标记为实验，不改变默认资格门槛。

## 当前交付范围

当前基础模板为 `linux-firefox-native-phase0`，包含地区、屏幕、CPU 的八个组合：

- 中文/上海时区或英文/纽约时区。
- 1280×800 或 1366×768 屏幕，DPR 1，最大化窗口，viewport 从真实窗口派生。
- 2 或 4 个可见并发线程。
- 当前 Firefox 原生 Linux 桌面身份，UA 版本关联实际构建。

新增 `linux-firefox-region-appearance-v1`，包含中文/上海与英文/纽约各自的明暗外观，共四个组合：

- 上海位置 `31.2304, 121.4737`，纽约位置 `40.7128, -74.0060`，精度均为 50 米。
- 明色变体使用普通动画，暗色变体使用减少动画；这是预设选择，两者没有普遍的必然关系。
- 对比度为 `no-preference`，不启用 `forced-colors`。
- 地理位置在初始空白页应用到实例的默认 user context，后续标签页和派生窗口继承。网站仍需取得原生定位授权。

在当前设备验证并使用新模板：

```sh
python cli.py persona bootstrap --template linux-firefox-region-appearance-v1
python cli.py persona create --template linux-firefox-region-appearance-v1 --start
```

定位探针只为自己的临时 loopback origin 授权，检查返回坐标与精度，再拒绝授权检查错误，最后恢复原权限状态。它不会给访问的网站自动授予定位权限。实现使用 [WebDriver BiDi 的地理位置与权限命令](https://w3c.github.io/webdriver-bidi/)，失败或权限未恢复的组合不能获资格。Geolocation 与这些外观媒体查询不属于 Worker API，报告明确列为不适用。

旧基础模板保持原来的验证范围；未指定 `--template` 时，默认池可以抽到当前环境中任一合格模板。

`linux-firefox-software-glx-v1` 在相同的四个地区外观组合上使用软件 GLX 路径。当前设备的 EGL 初始化失败，而 GLX 能创建 Mesa llvmpipe 上下文；此模板明确请求软件渲染，不能与 `--backend native` 混用。

```sh
python cli.py persona bootstrap --template linux-firefox-software-glx-v1
python cli.py persona create --template linux-firefox-software-glx-v1 --start
```

它额外要求 Window 中的 WebGL1/2 通过 shader 编译、程序链接、红色三角形像素读回及 RGBA8 framebuffer 绿色像素读回。报告保留 precision、limits 与 extensions 的观测，但这些查询不代表全部精度、边界或扩展行为均已验收。该模板不要求 Worker WebGL；完整核显兼容仍未验证。

`python -m tests.persona_graphics_live` 可串行比较默认、强制 GLX、强制 EGL 三种路径，结果保存在私有 `~/.tbp/graphics-diagnostics`。诊断脚本不会授予模板资格。

`linux-firefox-fonts-glx-v1` 在软件 GLX 的四个地区外观组合上增加两套实际字体集合。明色集合使用 DejaVu Sans、Serif、Sans Mono、Noto Sans CJK SC 和 Noto Color Emoji；暗色集合将 DejaVu Serif 替换为 Noto Serif CJK SC。所有字体必须真实安装，缺少字体时启动失败。

```sh
python cli.py persona bootstrap --template linux-firefox-fonts-glx-v1
python cli.py persona create --template linux-firefox-fonts-glx-v1 --start
python -m tests.persona_live --template linux-firefox-fonts-glx-v1
```

每个实例使用私有 Fontconfig 配置、字体目录和缓存，排除未选中的字体及 CJK 合集中其他地区的字面。Firefox 自带字体在此模板中禁用。没有修改系统字体目录，也没有安装 Liberation，因此不包含 Liberation 资格。

字体资格检查 CSS `local()` 的正反向加载、三个通用字体别名、实际 Canvas 像素与 TextMetrics，以及 `toDataURL`/`toBlob` 导出后解码的像素一致性。字体文件的完整名称与 family 名称分别保存；不会用不存在的字体名称冒充支持。未启用 Firefox 的 `font.system.whitelist`，因为它会禁用所有 CSS `local()` 来源；集合由私有 Fontconfig 约束。该模板不要求 Worker 字体资格。

`linux-firefox-workers-glx-v1` 进一步要求 Dedicated、Shared、Service Worker 分别通过字体和 WebGL 检查。它沿用四个地区/外观/字体组合，新增两个必需资格 `fonts_workers` 和 `webgl_workers`。旧模板的验证范围保持不变。

```sh
python cli.py persona bootstrap --template linux-firefox-workers-glx-v1
python cli.py persona create --template linux-firefox-workers-glx-v1 --start
python -m tests.persona_live --template linux-firefox-workers-glx-v1
```

Worker 使用真实 `FontFace`、`self.fonts` 和 `OffscreenCanvas`，检查正反向本地字体加载、三个通用别名、实际文字像素/TextMetrics 与 Window 一致，并验证 `convertToBlob()` 导出的 PNG 解码结果。Worker 没有 `toDataURL()`，该项明确记录为不适用。三类 Worker 的 WebGL1/2 各自执行 shader 编译/链接、红色像素和绿色 RGBA8 framebuffer 读回，并核对实际图形身份与 Window 一致。接口缺失、超时或任一上下文证据失败，都不能获得完整资格。

完整 Worker 探针仅在新模板显式启用。Service Worker 的异步验证使用 `event.waitUntil()` 保持生命周期，页面等待各 Worker 的最终结果。生命周期验收还比较重启前后的 Worker 字体/图形证据；双实例阶段使用轻量检查，完整探针在单实例阶段执行。规范依据：[Worker 字体来源](https://drafts.csswg.org/css-font-loading/#font-face-source)、[OffscreenCanvas](https://html.spec.whatwg.org/multipage/canvas.html#the-offscreencanvas-interface)。

`linux-firefox-audio-glx-v1` 继承 Worker 模板的四个地区/外观/字体组合，增加必需资格 `audio_offline` 和 `audio_realtime`。明色组合的原生默认 `AudioContext.sampleRate` 为 44100 Hz，暗色为 48000 Hz；通过实例私有 Firefox 配置选择采样率，不覆盖 JavaScript API 返回值。这是模板搭配，不表示配色与采样率存在普遍关系。

```sh
python cli.py persona bootstrap --template linux-firefox-audio-glx-v1
python cli.py persona create --template linux-firefox-audio-glx-v1 --start
python -m tests.persona_live --template linux-firefox-audio-glx-v1
```

Offline 探针使用真实双声道、2048 帧的 `OfflineAudioContext`，将固定输入经过 gain 节点渲染两次，核对实际采样率、声道、帧数、每声道样本和 hash，以及渲染前后自然状态。Realtime 探针创建不指定采样率的 `AudioContext`，通过 `click_native` 的可信点击激活，验证 resume/suspend/resume/close 状态、时钟推进与暂停，以及正弦波的 analyser 波形、频谱峰值和结构。图中最终 gain 为 0，避免探针发声；结束或输入失败都会请求清理。

这些资格覆盖浏览器图内的音频运算。物理输入和输出仍标记 `not_verified`；双声道渲染不证明扬声器或麦克风具备相应能力，延迟只记录为诊断值。Audio 在三类 Worker 中标记 `notapplicable`。媒体设备、codec 与完整音频后端兼容尚未验收。

Audio 生命周期脚本保留原字体和 Worker 检查。双实例阶段只创建默认 AudioContext、读取采样率后立即关闭，核对两个实例与各自配置一致且采样率不同，并检查新标签、刷新、弹窗、Ctrl+N 与重启继承。完整探针在单实例阶段执行；重启比较 Offline 的两次实际测量和 Realtime 的稳定结构、状态、频谱峰值，不要求实时相位、时钟或延迟重复相等。Audio 轮次的四个新组合及旧 24 个组合在 schema 3 下全部通过资格验证，当时合格池为 28 个组合。Audio 生命周期 16 组实机验收通过，包括不同默认采样率、实际音频测量、重启保持及可信点击/弹窗；应用进程数为基线 7、双实例 31、结束 7。本地 137 项单元测试、编译和差异检查通过；实现提交 `90bfb9e` 的 Python 3.10 / 3.14 CI 均通过：[运行记录](https://github.com/Marcus1337X/termux-firefox-persona/actions/runs/34112107654)。

`linux-firefox-media-glx-v1/1.0.0` 是媒体模板，四个完整组合已取得本机资格，继承 Audio 配置，增加必需资格 `media_codecs_window`。这项资格要求以下六个内置 fixture 全部通过，任一种失败都不能进入严格池：

| Fixture | 编码范围 | 实际内容 |
| --- | --- | --- |
| `h264.mp4` | H.264 Baseline，`avc1.42c00a` | 64×64、8 fps、2 秒，前红后 lime 绿 |
| `vp8.webm` | VP8 | 同上 |
| `vp9.webm` | VP9 profile 0 | 同上 |
| `av1.webm` | AV1 Main、8 bit | 同上 |
| `aac.m4a` | AAC-LC，`mp4a.40.2` | 48 kHz、单声道、2 秒、1 kHz 正弦波 |
| `opus.webm` | Opus | 同上 |

H.264、VP9、AV1 fixture 显式完成 BT.601 到 BT.709 的转换并写入色彩元数据；VP8 使用其格式的 BT.601-like（SMPTE170M）色彩表示。最终六种 codec 的实际解码与播放全部通过，红/绿像素阈值保持严格，未因转换问题放宽。

资源由探针自己的 loopback 白名单路由提供，支持视频 seek 所需的单段字节 Range，不读取任意路径。探针检查 `canPlayType` 与 `MediaCapabilities.decodingInfo().supported`，同时检查真实解码和播放：视频必须推进播放时间、到达结束，并在 seek 后读回对应红/绿像素；音频必须经过 `decodeAudioData` 得到已知信号，再由媒体元素经 `MediaElementAudioSourceNode` 进入 analyser 验证波形和频谱。实际点击要求 `isTrusted=true`，输出图末端静音，成功或失败均清理资源。

这些观测仅证明上述 fixture 的有限 profile、尺寸和时长，不代表所有编码 profile、分辨率、编码能力、WebRTC 或媒体设备已验证。物理输入/输出与 WebRTC 均为 `not_verified`；`smooth` 和 `powerEfficient` 保留观测，不作为稳定身份或性能承诺。

```sh
python cli.py persona bootstrap --template linux-firefox-media-glx-v1
python cli.py persona create --template linux-firefox-media-glx-v1 --start
python -m tests.persona_live --template linux-firefox-media-glx-v1
```

`linux-firefox-interaction-glx-v1/1.0.0` 是桌面输入与交互候选模板，继承媒体六编解码器、Audio、私有字体及 Window/三类 Worker 图形配置，新增必需资格 `input_window`。该模板显式核查桌面精确指针（`pointer: fine`）、桌面悬停（`hover: hover`）及零触摸点（`maxTouchPoints: 0`），并在 Dedicated/Shared/Service Worker 中核验 `maxTouchPoints` 跨 Realm 一致性；同时要求窗口前台活跃（`visibilityState == "visible"`）且具备活跃焦点。第一批次的 `window.screenX/screenY` 非负性、`screen.orientation` landscape 有效性及 Window/Worker 的 `navigator.language == locale` 亦一并纳入约束。

`linux-firefox-privacy-storage-glx-v1/1.0.0` 是第三批次隐私与存储候选模板，继承输入交互、多媒体编解码、音频及图形栈全部能力，新增必需资格 `privacy_window_http`、`storage_window` 与 `network_window_worker`（共 17 项必需能力）。在隐私层核验 `navigator.doNotTrack` 与 HTTP `DNT: 1` 请求头一致性、`navigator.globalPrivacyControl` 与 HTTP `Sec-GPC: 1` 请求头及 Worker 跨 Realm 一致性；在存储层核验 `localStorage`、`sessionStorage`、`indexedDB` 与 Cache API 可用性与独立操作；在网络层核验 Window 与 Worker `navigator.onLine` 状态一致性。

在多显示环境隔离与生命周期层面，系统全面支持基于 `flock` 互斥租借的独立 DISPLAY 分配（规避系统已有 `.X{num}-lock`），实现多实例完全并行运行；实例停止（`stop`）采用精准所有权追踪定向回收，绝不跨实例全局误杀。
在硬件媒体与通信设备层面，引入设备枚举与 WebRTC 探针，无物理硬件时严防虚构声明，严格标记为 `unsupported` / `not_verified`。
在底层网络流控层面，明确 Termux 无 root 边界约束，要求内核流控（`tc netem`）在无 root 时严格判定为 `unsupported` 并附带证据，应用层模拟标记为 `partial`。
在真实硬件 GPU 驱动栈层面，核实 Mesa llvmpipe 软件渲染栈下核显候选模板的不可替代性，防止将软件后端混淆为硬件核显。本地 228 项单元测试全部通过。

创建和生命周期命令需要先在本机获得完整资格。媒体完整探针仍仅在单实例阶段运行；双实例只复用轻量字体/Audio 检查和六种 `canPlayType` 查询。重启比较实际解码像素/音频与稳定播放结构，不比较实时波形相位、时钟和性能观测。本轮 168 项单元测试、编译和差异检查通过；六个 fixture 合计约 67 KB，已完成生成后解码校验，隔离构建的 wheel 包含全部 8 个媒体资源文件。四个媒体组合与旧 28 个组合在 schema 4 下全部通过资格检查，旧身份 ID、种子和最终配置保持，当前本机合格池为 32 个组合。媒体生命周期 20 组实机验收全部通过：A/B 六种 codec 实际解码/播放、A 重启后的解码像素和音频保持，以及可信弹窗、原生 Ctrl+N 和其他继承检查均通过。应用进程数为基线 7、双实例 31、结束 7；测试实例已停止。验收身份与详细记录见 `docs/DEVELOPMENT.md`。实现提交 `957e7a5` 的 Python 3.10 / 3.14 CI 均通过：[运行记录](https://github.com/Marcus1337X/termux-firefox-persona/actions/runs/34115976144)。

资格报告只覆盖模板列出的必需能力；任何模板之外的额外观测不等于完整第二批验收。核显模板仍是候选；只修改 renderer 名称不能获得核显资格。`software` 为默认执行后端，`native` 仅表示不强制软件渲染，不能自动等同于手机 GPU 加速。

配置、资格、profile 和日志默认存于 Termux 私有目录 `~/.tbp/personas`。`--root` 可指定另一个私有测试目录，放在子命令之前。共享存储不能用作 profile 根目录。Firefox/后端/字体环境变化会使原资格失效，不会静默修改已保存身份。环境快照现为 schema 4，记录音频客户端选择、配置摘要与可获得的服务端事实，并纳入 codec 库、动态加载器路径及按加载顺序排列的候选 C++ 库及实际库 hash。这些信息变化也需重新验证；旧 schema 的资格不能直接沿用，本轮旧 28 个组合已显式重验通过。

默认截图和其他相对导出路径位于该 Persona 私有实例目录，下载位于其 profile 的 `downloads`。环境变化后，先停止旧身份，再显式重新验证：

```sh
python cli.py persona stop PERSONA_ID
python cli.py persona requalify PERSONA_ID
python cli.py persona start PERSONA_ID
```

`requalify` 保留原 ID、种子、最终配置和 profile。验证期间只开放状态、探针和停止操作；完整探针通过、测试进程退出且环境未再次变化后，才更新保存的环境绑定与资格。失败或取消保留原身份元数据。Firefox 版本变化导致原 UA/版本组合不再兼容时，需要新建 Persona，不能通过重新验证改写身份。重新运行 `bootstrap` 则验证当前环境的组合，供新建身份使用。同环境的组合重测失败也会撤销旧严格 Persona 的启动资格。

独立 Xvfb 窗口属于不同虚拟显示；本版本不提供把所有虚拟桌面平铺到 Termux:X11 的展示界面。截图、输入和上下文操作通过 Persona 管理接口完成。

## Android 进程限制与诊断

开发中一次双实例测试发生了控制会话中断；没有取得明确系统终止原因。已减少 Firefox 预启动进程及每站点内容进程数量，保留站点隔离，并增加启动/探针操作的进程预算和 PID/start-ticks 资源所有权检查。

默认预算是当前应用 UID 可见进程数的本地估计，不是 Android 全局限制的准确读取。其他应用和网页动态增加的进程仍可能影响系统行为。达到预算时先停止闲置 Persona，不继续盲目启动。只有了解设备实际设置时才调整 `TBP_PERSONA_PROCESS_BUDGET`；代码不会修改 Android 系统设置。

```sh
python cli.py persona status PERSONA_ID
python cli.py persona command PERSONA_ID diagnostics
```

异常退出会显示 `interrupted`；`stop` 和后续启动只回收 PID 与启动时间仍匹配的自有资源，不通过全局 pkill 清理。报告与日志可能包含访问内容，应留在私有运行目录。

## 验证

```sh
python -m unittest discover -s tests/unit -v
python -m compileall -q src cli.py
```

完成本机 `bootstrap` 后可运行 `python -m tests.persona_live`，验证原生输入、截图、双实例隔离和恢复。它会创建两套私有测试身份，结果保存在 `~/.tbp/personas/live-acceptance.json`，不加入 CI。测试结束会停止测试实例。

使用 `python -m tests.persona_live --template linux-firefox-region-appearance-v1` 可专门验收新模板，额外检查重启后的定位资格与权限状态。

GitHub Actions 在 Python 3.10 和 3.14 上运行这些逻辑检查。`tests/test_*.py` 是上游手动浏览器测试，不纳入无 GUI 的单元测试发现范围。Termux 实机记录见 `docs/DEVELOPMENT.md`。
