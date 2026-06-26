# WDA 打包说明

当前 `WDA.ipa` 是基于 Appium WebDriverAgent 重新构建的 iPhoneOS arm64 包：

- 构建日期：2026-06-17
- 源码：`https://github.com/appium/WebDriverAgent`
- 构建方式：`build-for-testing`
- 打包策略：保留 Xcode 生成的 `XCUnit.framework`、`XCTest.framework`、`XCUIAutomation.framework` 等 XCTest 运行时框架，只删除 `.dSYM` 调试符号。
- 签名状态：`WebDriverAgentRunner-Runner.app`、内层 `WebDriverAgentRunner.xctest`、`WebDriverAgentLib.framework` 都是未签名状态，需要安装前自行重签。
- 配套文件：同目录保留 `WebDriverAgentRunner_iphoneos26.5-arm64.xctestrun`，用于 `xcodebuild test-without-building` 或 XCTest 启动排查；标准 IPA 内只保留 `Payload/`。
- 稳定性补丁：`UITestingUITests.m` 中加入了 `UIApplication.sharedApplication.idleTimerDisabled = YES`，并在前台恢复和定时器里重复设置，尽量减少自动熄屏导致 WDA 断连。
- 权限补充：Runner app / xctest 包内包含常见隐私权限描述；但 iOS 私有 entitlement 不能靠打包硬塞，最终可用权限仍取决于你的签名证书和 provisioning profile。
- 启动方式：建议通过 `xctest`/`runwda` 启动 WDA。单纯点击桌面图标只是启动 XCTest Runner 外壳，不等价于启动 WDA 测试服务。

## 当前包验证结论

- `WDA.ipa` 已重新打包为干净的未签名基包，SHA256：`ddd20b426521863e97cee4a6ad8d4884bdfd8ef5fe61e29ca1e6fa66e1f09c81`。
- `WebDriverAgentRunner_iphoneos26.5-arm64.xctestrun` SHA256：`e5519fa03a1cd135d39f8cc65493a8e1723bcb2b6d3abac8b89ecca42ef44839`。
- 解包后只有标准 `Payload/WebDriverAgentRunner-Runner.app` 结构，保留 XCTest 运行时框架，外层 app、内层 `.xctest`、`WebDriverAgentLib.framework` 均无旧签名残留。
- 本机设备 `Tommy (2)` 上已安装的 WDA bundle id 为 `com.facebook.WebDriverAgentRunner.xctrunner.BR4JH5QHF7`，普通 launch 后 `8100/status` 不可访问，说明已安装 app 没有把 WDA 服务跑起来。
- 本机 `xcodebuild test` 已能识别真机 destination，但因为缺少 `com.facebook.WebDriverAgentRunner.xctrunner` 的 iOS Development provisioning profile，未完成运行态验证。签名后需要再次通过 XCTest 启动并验证 `8100/status`。

重签时尽量保留原始 `CFBundleIdentifier`：`com.facebook.WebDriverAgentRunner.xctrunner`。如果签名工具会追加后缀，启动 XCTest 时必须使用实际安装后的 `CFBundleIdentifier`，而不是原始 bundle id。

建议重签覆盖全部嵌套可执行内容，顺序从内到外：`WebDriverAgentLib.framework` -> `WebDriverAgentRunner.xctest` -> `WebDriverAgentRunner-Runner.app`。只签外层 app 容易安装成功但 XCTest 启动失败。

## 运行检查

macOS 上 iOS 投屏已切换为 ReplayKit USB 流，不再使用 WDA MJPEG 或 AVFoundation 作为屏幕采集来源。iPhone 端启动 `ReplayUSB Broadcast` 后，服务端会自动绑定 USB mux 端口并读取 raw Annex-B H.264：

```bash
iproxy -u <UDID> -s 127.0.0.1 <LOCAL_REPLAYKIT_PORT>:27777
```

默认第 1 台 iOS 使用本机 `27777 -> 设备 27777`，第 2 台使用本机 `27778 -> 设备 27777`，以此类推。端口池可通过 `ios.replaykit.device_port` 和 `ios.replaykit.local_port_base` 覆盖。

不要直接点击桌面上的 WDA 图标作为服务启动方式。需要用 XCTest 启动测试 bundle，启动成功后设备屏幕通常会出现系统自动化控制提示。

WDA 对外表现是 HTTP 服务，但默认不要求设备和服务端走外部网络。USB 连接下会通过 usbmux/tunnel 转发到本机 `127.0.0.1:<LOCAL_PORT>`；Wi-Fi/network WDA 只作为后续可选连接方式。

如果使用 tidevice：
```bash
tidevice xctest -B com.facebook.WebDriverAgentRunner.xctrunner
```

另开终端转发 WDA 控制端口和 MJPEG 端口。单台设备可直接使用 `8100/9100`，多台设备必须为每台设备使用不同的本机端口：
```bash
tidevice relay 8100 8100
tidevice relay 9100 9100
```

如果使用 pymobiledevice3 转发端口：
```bash
pymobiledevice3 usbmux forward 8100 8100
pymobiledevice3 usbmux forward 9100 9100
```

服务端会按 iOS 设备 UDID 自动分配本机转发端口，默认从 `18100/19100` 开始：

- 第 1 台 iOS：本机 `18100 -> 设备 8100`，本机 `19100 -> 设备 9100`
- 第 2 台 iOS：本机 `18101 -> 设备 8100`，本机 `19101 -> 设备 9100`
- 以此类推，已被占用的本机端口会自动跳过。

如果对应本机端口没有监听，服务端会自动使用当前 Python 环境启动：
```bash
python -m pymobiledevice3 usbmux forward <LOCAL_WDA_PORT> 8100 --serial <UDID> --host 127.0.0.1
python -m pymobiledevice3 usbmux forward <LOCAL_MJPEG_PORT> 9100 --serial <UDID> --host 127.0.0.1
```

验证：
```bash
curl http://127.0.0.1:<LOCAL_WDA_PORT>/status
curl -I http://127.0.0.1:<LOCAL_MJPEG_PORT>
```

控制端口不通表示 WDA 没通过 XCTest 跑起来或控制端口没转发；控制端口通但 MJPEG 端口不通表示 MJPEG 端口没转发或 WDA 没启用 MJPEG 服务。

默认端口池可通过配置覆盖：

- `ios.wda.device_port`: 设备上的 WDA 控制端口，默认 `8100`
- `ios.wda.local_port_base`: 本机 WDA 转发起始端口，默认 `18100`
- `ios.mjpeg.device_port`: 设备上的 MJPEG 端口，默认 `9100`
- `ios.mjpeg.local_port_base`: 本机 MJPEG 转发起始端口，默认 `19100`
- `ios.replaykit.device_port`: 设备上的 ReplayKit H.264 流端口，默认 `27777`
- `ios.replaykit.local_port_base`: 本机 ReplayKit 转发起始端口，默认 `27777`

## 1. 编译项目（iOS 17+ 必须添加 `ARCHS=arm64` 参数）
```bash
xcodebuild build-for-testing -scheme WebDriverAgentRunner -sdk iphoneos -configuration Release -derivedDataPath /tmp/wda_build ARCHS=arm64
```

## 2. 进入编译产物目录并创建 Payload 文件夹
```bash
cd /tmp/wda_build/Build/Products/Release-iphoneos
mkdir Payload && cp -r *.app Payload
```

## 3. 删除调试符号，不删除 XC 开头框架
```bash
rm -rf Payload/*.app/PlugIns/*.dSYM
```

## 4. 打包成 IPA（这样打包会自动包含正确的 Payload 结构）
```bash
zip -r WDA.ipa Payload/
```

## 具体看：
https://zhuanlan.zhihu.com/p/673319266
