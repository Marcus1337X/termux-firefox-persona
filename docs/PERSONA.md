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
python cli.py persona command PERSONA_ID click --params '{"target":"button"}'
python cli.py persona command PERSONA_ID type --params '{"target":"input","text":"hello"}'
python cli.py persona command PERSONA_ID screenshot
python cli.py persona probe PERSONA_ID
python cli.py persona stop PERSONA_ID
python cli.py persona start PERSONA_ID
```

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

它额外要求 Window 中的 WebGL1/2 通过 shader 编译、程序链接、红色三角形像素读回及 RGBA8 framebuffer 绿色像素读回。报告保留 precision、limits 与 extensions 的观测，但这些查询不代表全部精度、边界或扩展行为均已验收。Worker WebGL 和完整核显兼容仍未验证。

`python -m tests.persona_graphics_live` 可串行比较默认、强制 GLX、强制 EGL 三种路径，结果保存在私有 `~/.tbp/graphics-diagnostics`。诊断脚本不会授予模板资格。

资格报告只覆盖模板列出的必需能力；外观、输入、Canvas 和 Audio 的额外观测不等于完整第二批验收。核显模板仍是候选；只修改 renderer 名称不能获得核显资格。`software` 为默认执行后端，`native` 仅表示不强制软件渲染，不能自动等同于手机 GPU 加速。

配置、资格、profile 和日志默认存于 Termux 私有目录 `~/.tbp/personas`。`--root` 可指定另一个私有测试目录，放在子命令之前。共享存储不能用作 profile 根目录。Firefox/后端/字体环境变化会使原资格失效，不会静默修改已保存身份。

默认截图和其他相对导出路径位于该 Persona 私有实例目录，下载位于其 profile 的 `downloads`。环境变化后，当前版本拒绝恢复旧身份；尚未提供保持原 profile 的重新授予资格流程。重新运行 `bootstrap` 可以验证当前环境的新组合，再创建新身份。同环境的组合重测失败也会撤销旧严格 Persona 的启动资格。

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
