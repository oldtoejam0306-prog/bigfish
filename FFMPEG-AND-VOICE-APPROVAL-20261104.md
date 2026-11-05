# FFmpeg 与语音的待授权方案

用户已批准下述固定版本安装与合成视频测试。当前两次同源下载均超时，尚未完成包校验或安装；没有修改安全策略或重试被拒的语音脚本。

## FFmpeg：单用户、单应用方案

- 用途：`ffprobe` 查询视频时长，`ffmpeg` 从用户选定或明确同意下载的 B 站视频中抽取少量画面；不意味着理解完整视频或音轨。
- 来源：[FFmpeg 官方下载页](https://ffmpeg.org/download.html)列出第三方 Windows 构建；拟选 [Gyan Doshi 发布页](https://www.gyan.dev/ffmpeg/builds/)的 **FFmpeg 9.0.2 Windows x64 Essentials ZIP**，约 **109 MB**，包含 `ffmpeg` 和 `ffprobe`，构建发布方标注 GPLv3。具体文件：`ffmpeg-9.0.2-essentials_build.zip`。
- 发布方 [SHA-256 文件](https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.2-essentials_build.zip.sha256) 当前给出的值：`60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba`。下载完成后需对实际文件计算 SHA-256 并精确比较；不一致立即停止。该校验值和安装包来自同一发布方，可检查传输完整性，但不是独立签名认证。
- 计划位置：`%LOCALAPPDATA%\BigFish\tools\ffmpeg-9.0.2\bin`。只把审核后的 `ffmpeg.exe`、`ffprobe.exe` 及适用许可文件放入该独立目录，不使用管理员权限、不更改系统 PATH、不覆盖已有目录；目标已存在时停止并核查。ZIP 解压后实际大小尚未实测，应先读取归档目录并限制解压大小、拒绝绝对路径、`..` 与链接，再提取。
- 候选应用已支持通过仅对该进程生效的 `BIGFISH_FFMPEG_DIR` 查找成对程序；候选启动器会在上述独立目录存在两项程序时使用该目录。指定目录无效时不会静默改用别的程序。软件未安装；抽帧真实验收仍未完成。
- 较小的 [7z 构建](https://www.gyan.dev/ffmpeg/builds/)约 34 MB，[发布方 SHA-256](https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-9.0.2-essentials_build.7z.sha256)为 `4705843ccaaf54257c16ad90f3e952ece33c17df964ecf7bfdbb0f49c7171077`。本机有 bsdtar 3.7.7，但尚未验证它能解开该具体归档；ZIP 方案不需要增加解压软件。
- 已批准的具体动作：下载上述固定版本 ZIP，校验后只在指定单用户目录提取所需文件及许可信息，运行版本自检和合成视频的离线抽帧测试；不运行真实视频、模型或平台外发。首次 Python 下载读取超时；同地址 HEAD 返回 HTTP 200、长度 114,768,076 字节；同源 curl 重试在 600 秒后返回 28（超时）。最终安装目录经检查不存在。尝试只读查看下载临时文件时遇到 WinError 5 访问拒绝，已停止该检查，没有换接口或提权重试。不能据此声称已下载、已核验、已安装或测试通过；不再自动重复下载。
- 用户随后授权使用同一发布者的 GitHub 官方镜像（[镜像元数据](https://github.com/GyanD/codexffmpeg/releases/expanded_assets/9.0.2)显示相同 SHA-256）。安装进程启动后本机连接中断，原会话退出结果丢失。重连后的只读检查显示最终安装目录不存在，进程列表未发现断线前启动的安装进程；但不能确认镜像下载是否曾完成、为何停止或临时文件状态。没有访问此前被拒临时文件，也没有重复该结果不确定的下载。须先确认原操作结果，再决定下一步。
- 原镜像脚本的下载目标是 `%TEMP%\bigfish-ffmpeg-<随机值>\bundle.zip`，最终目标仍是上面的 `ffmpeg-9.0.2` 目录；脚本只向原执行会话输出进度，没有单独的持久下载回执。最终目录中的 `install-manifest.json` 原本会记录已核验包的哈希和提取文件；目录不存在，因此没有可读取的安装回执。由于原会话丢失且不得访问被拒临时文件，目前无法凭获准检查项判断 ZIP 是否曾下载完整或校验通过。

## Windows 本机语音：仍需先明确合规策略

- 被拒目标：`%USERPROFILE%\Documents\Codex\2026-11-03\task-2\new-release\bigfish\bili_workbench\windows_speech_input.ps1`，当前文件 SHA-256 为 `e9d12d5e678627ae1f5c07466a765003907d0b5b7671a81de293e2989f619780`。Windows PowerShell 的原始提示为“无法加载文件……windows_speech_input.ps1，因为在此系统上禁止运行脚本”，并指向 `about_Execution_Policies`。已停止该操作，没有改策略或更换执行方式重试。
- 只读检查显示 Windows PowerShell 5.1 的各作用域均为 `Undefined`；[Microsoft 文档](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_execution_policies?view=powershell-5.1)说明 Windows 客户端在全部未设置时有效默认值为 `Restricted`。另一项系统语音合成测试返回“没有安装声音或当前安全设置下不可用”，不能推断与脚本拒绝是同一原因。
- 需要用户或系统管理员审阅该脚本及其发布者，决定是否存在符合本机安全要求的签名、信任和策略安排，再确定具体作用域与修改。PowerShell 执行策略并非天然的单文件许可；例如放宽当前用户策略可能影响其他脚本，不能称为只授权本文件。尚未确定可实施的最小策略变更，因此不请求笼统的“同意修改安全设置”，也不建议绕过参数。
- 在合规策略和单次麦克风授权明确前，不运行脚本、不录音。即便脚本获准，还需在正常用户会话验证中文识别器和系统声音；它目前只是短录音转写，尚不是实时双工通话。
