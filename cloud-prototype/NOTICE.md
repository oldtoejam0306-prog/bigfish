# 第三方来源和许可

本包实际复用 webvtt-py 0.5.1，官方仓库 https://github.com/glut23/webvtt-py ，commit `8c7552053b89b198fa4249a178d05f70e0649456`。复制了 `webvtt` 下解析所需的 9 个 Python 文件，原始版权及 MIT 许可证在 `vendor/WEBVTT-LICENSE`。`vendor/UPSTREAM-GIT-BLOBS.txt` 记录来源 Git blob。仅解析已提供的本地字幕文本，不安装系统包、不接入任何网络功能。

待审候选：MaiBot v1.3.19（commit `c6429547af711555e3d4d1b8d04b413572cb616c`，GPL-3.0），dsh-bili-scroll v2.4.0（commit `398e4724a481b09b6383a950bcd08723c632da59`，MIT）。尚未安装、调用或复制这些项目代码。接入前须审查其全部依赖、许可、隐私与工具权限。

固定上游静态契约来源：
- https://github.com/c1overzz/dsh-bili-scroll/blob/398e4724a481b09b6383a950bcd08723c632da59/vendor/bilibili-mcp/mcp_server.py （工具参数、返回 JSON、空名单全放行）
- https://github.com/c1overzz/dsh-bili-scroll/blob/398e4724a481b09b6383a950bcd08723c632da59/lib/mcp.mjs （MCP tools/call 和 text content）
本包未复制或运行这两个上游文件；fixture 是根据公开合同手写的模拟数据。
