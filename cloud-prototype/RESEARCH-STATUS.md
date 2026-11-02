# 只读研究交接（2026-11-02 快照）

研究不是安装或真实验收；以下路线必须按实际目标版本重新核对。

- QQ 产品意图：独立普通 QQ 号与主人互为好友，首版仅主人私聊。LLBot 已按用户实查的注册不可用反馈放弃；这不是本归档对公开注册状态的独立证明。
- SnowLuma：候选 Windows 客户端桥。v2.3.4 LICENSE 允许个人非商业运行，但不是 OSI 开源，原生组件专有；EULA 对自动化部署和衍生分发有额外限制。PRIVACY 声明没有联网激活授权。Windows 文档描述默认自动发现并注入 QQ；需防止接触主人主 QQ。公开资料不等于新用户实际登录或无风控的保证。本地最新 SnowLuma 集成另在本机，不在此目录。
- NapCat：v4.18.42 的 Windows 文档有 QQ 客户端启动流程；根 Limited Redistribution License 对未经作者明确许可的使用/修改等有限制，不能描述为 MIT 或自动推荐直接安装。
- 官方 QQ 机器人：可单聊，但为 AppID/AppSecret/OpenID 开放平台机器人体系，不等同于普通独立 QQ 号。
- MaiBot v1.3.19，commit c6429547af711555e3d4d1b8d04b413572cb616c，GPL-3.0；没有复制其代码。官方统一 QQ 连接器支持客户端路线，需检查版本、宿主名单默认值及组合许可。
- dsh-bili-scroll v2.4.0，commit 398e4724a481b09b6383a950bcd08723c632da59，只核对字幕/详情合同，未运行服务或复制其实现。空 BILI_MCP_TOOLS 放行全部，不应直接运行默认设置。fixture-only 不能称为已接入 B站。
- 新教程线索：BV1q2nW6DEaf 与 https://github.com/Alterssss/dsh-bili-whale ，由父线程另行只读研究；本归档未安装或验收该插件，不重复实现抓取器。

维护方来源：
- https://github.com/SnowLuma/SnowLuma/tree/v2.3.4
- https://github.com/SnowLuma/SnowLuma/blob/v2.3.4/LICENSE
- https://github.com/SnowLuma/SnowLuma/blob/v2.3.4/EULA.md
- https://github.com/SnowLuma/SnowLuma/blob/v2.3.4/PRIVACY.md
- https://snowluma.github.io/zh/docs/guide/deploy/windows
- https://github.com/NapNeko/NapCatQQ/blob/v4.18.42/LICENSE
- https://napneko.github.io/guide/boot/Shell
- https://bot.q.qq.com/wiki/develop/api-v2/
- https://github.com/Mai-with-u/MaiBot/tree/c6429547af711555e3d4d1b8d04b413572cb616c
- https://github.com/c1overzz/dsh-bili-scroll/blob/398e4724a481b09b6383a950bcd08723c632da59/vendor/bilibili-mcp/mcp_server.py
- https://github.com/c1overzz/dsh-bili-scroll/blob/398e4724a481b09b6383a950bcd08723c632da59/lib/mcp.mjs
