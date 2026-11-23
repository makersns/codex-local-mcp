# Codex Local MCP 应用图标

作者：Sugar。更新日期：2026-11-23。

![Codex Local MCP 应用图标](images/app-icon-0.6.0.png)

## 设计

图标为本项目原创路径绘制：白色的开放窗口形成字母 C，指向 Codex 与本机工作空间；一条薄荷色连接从窗口中延伸到独立端点，表达 MCP 将本地能力连接到会话。上下两端长度不同，使轮廓保持自身特征。

靛紫色底板延续原生界面的强调色，采用轻微渐变、内缘和阴影。图形不使用字体字形、系统符号、外部商标或下载素材，不依赖联网资源。

## 小尺寸与系统接入

- 使用 AppKit 路径直接渲染，每种尺寸单独生成。
- 16 / 32 像素加粗主体及连接线，去掉端点内孔和细节阴影。
- 导出 10 个标准 iconset 文件，覆盖 16–1024 像素及 Retina 变体。
- 构建时通过 macOS `iconutil` 生成 `AppIcon.icns`，由 `Info.plist` 的 `CFBundleIconFile` 引用。
- 透明外缘保留 macOS 图标留白，底板内部保持不透明。

## 源文件

- [原生绘制代码](../tools/generate-app-icon.swift)
- [1024 像素 PNG](images/app-icon-0.6.0.png)

图标随应用构建，不需要用户手动设置 Finder 图标。PNG 是预览与展示素材；正式客户端使用包含各尺寸的 ICNS。
