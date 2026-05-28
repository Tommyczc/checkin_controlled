# WDA 打包教程（ios 已弃用）：
# 1. 编译项目（iOS 17+ 必须添加 `ARCHS=arm64` 参数）
```bash
xcodebuild build-for-testing -scheme WebDriverAgentRunner -sdk iphoneos -configuration Release -derivedDataPath /tmp/wda_build ARCHS=arm64
```

# 2. 进入编译产物目录并创建 Payload 文件夹
```bash
cd /tmp/wda_build/Build/Products/Release-iphoneos
mkdir Payload && cp -r *.app Payload
```

# 3. 关键步骤：删除会引起崩溃的 XC 开头框架
rm -rf Payload/*.app/Frameworks/XC*

# 4. 打包成 IPA（这样打包会自动包含正确的 Payload 结构）
zip -r WDA.ipa Payload/

# 具体看：
https://zhuanlan.zhihu.com/p/673319266