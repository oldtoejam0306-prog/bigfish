# B站工作台：运行与边界

## 启动、停止与首次设置

Windows运行 start-bili.cmd，默认数据目录 `%LOCALAPPDATA%\BigFish\BiliWorkbench`。stop-bili.cmd读取当前用户保护的本机服务会话，仅调用本工作台停止接口，不终止QQ或其他进程。Linux示例：`python3 -m bili_workbench.web --data-dir /绝对独立目录`。需Python3.10+；没有pip依赖。视觉需要可信已安装ffmpeg/ffprobe。Windows尚未实机验收。

启动只绑定127.0.0.1；随机会话口令防止其他网页修改配置，校验Host/Origin。正文不通过innerHTML渲染。一个数据目录只运行一个实例；异常退出后runtime.lock可能保留，先确认原进程已退出再由主人处理，不能自动猜测并抢占。不要分享带会话口令的页面链接。默认stdout不输出口令；仅在本人需要时显式加`--show-session-link`显示，不要保存到日志。

首次设置明确需要：
1. 用户选择提供方、模型及预算，填本机API Key。预设只提供官方接口地址，不猜模型名、单价/币种、地区和预算。阿里云预设需控制台工作空间ID，DeepSeek预设使用官方HTTPS兼容根地址。文本和视觉分别配置单价及版本。
2. 填大肥鱼独立B站账号MID，再扫码或手动导入独立账号登录态。页面本地显示B站官方登录URL编码的二维码；轮询和账号MID核验后才保存Cookie。扫码、账号正确性和权限尚未真实验收。
3. 按需授权只读访问、付费调用和外发；三者不等同。默认付费/外发关闭，日预算/单次上限为零。没有配置时，自检列出具体缺项。

Windows凭据用DPAPI绑定当前用户，Linux仅0600文件，不声称加密。凭据、登录态、数据库、用户媒体永不纳入源码交付。模型连接测试只GET模型列表，不推理、不证明模型能力或实际价格。

## 阅读与低成本理解

真实请求代码：WBI视频信息、字幕、评论、热门、搜索。公共端点可匿名读取的信息仍受平台限制；没有字幕时明确返回缺失，不能把网络错伪装为已看完。平台风控/412/429触发退避，不换接口绕过。所有HTTP保持TLS验证，不自动重定向或重试。

默认字幕优先；长字幕最多8段，每段预算后调用并缓存，必要时再综合。整体过长明确拒绝，不静默截断。手动选择视觉时可使用指定本地视频，或显式下载B站API提供的单段低清媒体（最大50MB、仅HTTPS允许的CDN）。支持小体积AVC DASH的纯画面轨；多段、其他不支持编码、会员/付费受限和过大媒体明确拒绝，可改为用户合法提供的本地视频。ffmpeg抽3个480px稀疏帧；无音轨理解、无桌面采集、不等于完整看过视频。

模型没有工具权限，素材仅作不可信文本/图片；结果及素材独立记录，主人明确点击保存才加入character.db，来源标为B站材料和模型推断，不能当主人事实/偏好。该character.db尚未与本机QQ记忆整合。

## 连续队列、费用、取消与恢复

主人明确开启后，从公开热门前10条跳过已处理内容，每轮最多处理3条；最短60秒轮询，默认300秒，每UTC日默认12条。持久去重绑定账号、BV、模型和计价版本；当前不是个性化关注推荐流。未授权收费时仅记录推荐元数据。队列不自动评论/私信/投稿，不以外部素材作为权限指令。

重启后队列保持暂停，running任务标uncertain且不重放。错误、风控、预算不足、不确定费用均暂停，主人核对后明确恢复。取消信号在等待运行锁前设置；尚未开始的步骤停止。已发出的请求不可撤回；最多需等待HTTP超时或当前分片结束，不能把取消当作远端零费用。

每次调用先原子预留配置的单次上限，在日预算内才请求。真实usage返回后按配置单价记账；与提供方实际账单可能不同，价格误填和未知视觉token不能保证绝对金融硬限，应同时在提供方后台设置额度。缺usage、断网或响应不确定保留预留并暂停新收费；可由主人在页面核对提供方账单后手动记入实际费用，来源明确是人工核对，不是自动usage；原请求仍禁止重放。每段/综合分别有独立记账；缓存显示历史usage，不能当作此次新增支出。

## 评论、私信、投稿工作流

草稿绑定独立账号；查看准确内容、批准，再逐次输入“确认发送”。私信收件人必须在显式白名单。24小时外发次数按尝试时刻计数，失败/未知同样占用；旧草稿不能绕过。正文/评论不会直接修改配置。

视频草稿还绑定本地文件路径、长度、SHA-256、分区tid、标签及版权/来源。批准前复核文件，上传前再复核；UPOS串行分片校验回执，每片上传前核对已批准内容哈希，完成上传后才提交一次视频元数据。没有自动重试；取消、断网、回执缺失或投稿未知时草稿锁为uncertain。同一视频哈希已有未拒绝记录时禁止重复创建，防止重复投稿。不会读取或上传未明确选择的本地文件。

投稿为独立编写的Web/UPOS协议候选：preupload、创建会话、PUT分片、完成上传、web/add提交；已进行mock契约/错误测试，**尚未使用真实独立账号验收，可能因平台接口/权限变化失败**。当前维护方biliup同时包含需APP登录态的app/add路线，本工作台不复制APP私钥/应用秘密、不冒充该授权；若Web路线被平台拒绝，需要按合法授权进一步适配，不能承诺只靠Cookie必定投稿成功。B站接口返回成功不等于审核通过或公开发布；需在创作中心核对。投稿不自动发动态。

## 复用与接口出处

WBI算法及字幕/评论/私信端点流程改写自 ALTERSSSS 的 MIT dsh-bili-whale，固定 8b0009f8ce2ebdc421afcb2e727b19ac18912ec6；许可在vendor/BILI-WHALE-LICENSE。未安装或运行原插件，未采用其自动外发、全局凭据目录或监听默认值。

二维码复用Project Nayuki MIT QR Code generator，固定bcfe1b018cec5f1f91b5705eca5bb4cdd34e9f8d，vendor/qrcodegen.py保留完整许可；无需外部二维码服务。SeeNow（916354d36e58fdd843fff96284ff7c985bd00020，Apache-2.0）已核对但不复制/执行，因为本轮不需采集主人桌面。原webvtt-py保留MIT。

上传协议静态核对：
- https://github.com/biliup/biliup-rs/blob/56df872972f02c8ae55d47d364e7a1e534026dd5/crates/biliup/src/uploader/line/upos.rs
- https://github.com/biliup/biliup-rs/blob/56df872972f02c8ae55d47d364e7a1e534026dd5/crates/biliup/src/uploader/line.rs
- https://github.com/biliup/biliup-rs/blob/56df872972f02c8ae55d47d364e7a1e534026dd5/crates/biliup/src/uploader/bilibili.rs
- https://github.com/Ayppo/uploader/blob/master/bilibili.py （旧Web提交协议参考，不作为当前平台可用保证）
只读取接口形状，未复制/执行这些上传库或安装依赖。

提供方配置参考：
- https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope
- https://api-docs.deepseek.com/

## 剩余真实验收

缺少账号、Key和用户所选模型/预算时，不能验证真实登录、收费推理、视觉模型、评论私信和投稿；Windows DPAPI/脚本与浏览器也须本机验收。完整音视频理解、个性化关注流、私信收件箱及本机QQ记忆同步不在已完成能力中。没有安装任何未知插件或改动主人电脑服务。

## 当前本地验收的最小步骤

1. 将源码解压到独立目录。在该目录执行`py -3 -m unittest -q`（Windows），确认Python至少3.10；双击`start-bili.cmd`，默认浏览器自动打开本机设置页。停止使用`stop-bili.cmd`或原窗口Ctrl+C。Linux已验收的同等入口为`python3 -m bili_workbench.web --data-dir /绝对独立目录`。
2. 只读验收最小配置：勾选read_enabled，手动读取一个BV；队列还必须明确account_mid。匿名接口可能无字幕或受平台限制；不要据此开启外发。独立账号登录由主人自己扫码并验证MID。
3. 文本理解最小配置：provider_url、model、只在本地填写的API Key、price_version、currency、输入/输出每百万token单价、daily_budget_micro、request_cap_micro及paid_enabled。单位为该币种百万分之一，例如1元=1,000,000 micro；模型单价字段也是micro/百万token。先在提供方后台设额度，单次预留须覆盖配置的输入/输出最大用量。没有选定模型和真实价目，不可编造这些值。视觉还需独立vision_model、vision_price_version及视觉单价，并有可信ffmpeg/ffprobe。
4. 外发不是只填Key即启用：还需独立账号有效登录、账号MID一致、明确外发开关及逐条审核。私信另需收件人白名单；视频另需本人选定的媒体、分区tid、标签、版权来源。本轮只应验收dry-run，真实外发和平台端点兼容性尚待后续授权验收。

后续Mac目标和迁移边界见MAC-PORTABILITY.md；本轮未迁移。

2026-11-03更新：账号状态验证、字幕/帧缓存、可选视觉回退、费用计划和阻塞恢复的当前验收详见BILI-ACCEPTANCE-20261103.md；旧100测试Space包未更新，本轮仅保留工作区代码。
