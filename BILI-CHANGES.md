# 本轮源码变更与审查清单

实际工作目录：`/workspace/bigfish-work`；保留此前49文件审查包 `/workspace/bigfish-bili-workbench-review.zip`。

新模块：
- bili_workbench/runtime.py：配置、凭据Vault、HTTP、SQLite状态、预算/缓存/限流基础。
- bili_workbench/bili.py：WBI与真实只读端点、独立账号扫码/Cookie入口、单次评论私信发送。
- bili_workbench/model.py：兼容模型请求、真实usage、预算/分段、缓存、人工账单核对。
- bili_workbench/service.py：工作流、素材/记忆、草稿审核、单次外发、防重、ffmpeg稀疏帧。
- bili_workbench/upload.py：绑定文件哈希的UPOS上传/投稿协议候选与取消检查。
- bili_workbench/queue.py：连续有界浏览、持久去重、暂停/错误/重启策略。
- bili_workbench/presets.py：官方提供方地址预设、首次设置检查，不猜模型/价格。
- bili_workbench/web.py、index.html：本机配置和审核界面、认证、停止、运行实例锁。
- bili_workbench/control.py：本机认证停止，不杀QQ或其他进程。
- start-bili.cmd、stop-bili.cmd、selftest-bili.cmd：Windows入口（未实机验收）。

新增测试：test_workbench.py、test_upload_queue.py。复用文件：vendor/qrcodegen.py（MIT）、vendor/BILI-WHALE-LICENSE；保留旧vendor/webvtt。README现在介绍本候选，原原型说明保留在PROTOTYPE-ARCHIVE-README.md。BILI-SOURCE-MANIFEST.json记录逐文件哈希。

原33文件原型的人格/记忆/QQ离线模块保持原实现；本机SnowLuma最新集成不在此目录。真实B站工作台独立目录运行，不把其素材自动送入本机QQ私聊数据库。

GitHub create_tree曾被自动审批拒绝；本轮后续没有重试、改用接口或更换通道。当前没有feature分支提交；此为本地可审查候选，等待后续有授权的统一交付。

阶段2复核：修改runtime.py、bili.py、service.py、web.py与test_workbench.py；新增MAC-PORTABILITY.md、BILI-REVIEW.md，更新本验收/运行文档与清单。修复中断状态、网络间取消、容器识别和默认口令日志；无Mac迁移或新平台功能。

QQ/模型整合准备：新增bili_workbench/qq.py、preflight.py、test_qq_model.py、qq-model-config.example.json和QQ-MODEL-INTEGRATION.md；修改model.py增加角色上下文聊天复用/Key回显拒绝，service.py/web.py/index.html增加QQ配置与缺项自检，selftest-bili.cmd串联离线闭环。旧qq_offline.py不变；真实传输与发送没有伪装成已接通，未触碰本机CQS。

社交策略修正：新增social.py和test_social_policy.py，QQ默认授权独立AI账号的日常好友/群交流；公开上下文与主人私密人格/记忆完全分开。传输未验收仍不发送，100测试通过。未上传GitHub、未改分享权限。

2026-11-03阅读闭环补强：bili.py增加账号状态/错号停止、字幕缓存和DASH画面；model.py增加零网络费用计划；service.py接入计划/帧缓存/取消/第1P来源；queue.py改为10条中去重最多3条、持久尝试次数、缺项暂停及无费用明确重试；web.py/index.html增加估算、视觉回退和恢复入口；runtime.py增加默认关闭的browse_visual_fallback；presets.py区分匿名路径/账号状态/计划。新增test_bili_readiness.py与本机验收说明，119测试通过。未重复上传Space或GitHub。

2026-11-03整合：新增growth/interests/creation/frame_selection/qq_transport/interaction/activity/deepseek_test/tariff模块及其测试，全部接入Workbench/网页；参照IMPLEMENTATION-20261103.md。读取Key附件和GitHub上传均未尝试。
