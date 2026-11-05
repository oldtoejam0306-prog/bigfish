# 持续流式与中断：能力审查和复现

## 结论

本次没有把现有分段系统标为真正实时双工。会话核心能消费异步文本和 PCM 增量；模型新增受控 SSE 接收和取消，但回复完整通过隐私检查后才交给语音。默认识别和合成仍等待完整片段，尚未实现边生成边朗读。浏览器与后端传输不能自行补足提供者能力。

本轮进一步实现可选的 Windows 原生 PCM 输出管道和完整回复合成闸门。默认后端仍为缓冲按句合成；原生后端只供明确指定的开发参数，未在普通界面启用，Windows 引擎自身仍未实测。

随后补齐了可选持续识别宿主、事件协议和取消接线。C# 源码仅用本机已有编译器编译为临时库进行检查，未加载生成的库、未调用 Run；PowerShell 包装脚本也未执行。默认识别后端仍为分段转写。

没有安装依赖、加载本地模型、打开麦克风或扬声器、读取真实凭据、调用付费接口、运行受拒脚本或修改策略。没有改动已验收工作台。

## 已检查的具体入口

| 层 | 当前源码证据 | 结论及剩余条件 |
| --- | --- | --- |
| 采集/播放 | `duplex_audio.js` 连续分帧及本地缓冲；代次改变停止旧播放 | 模拟设备与真实本机 HTTP 测试通过，不是实机声学验证；仍使用 ScriptProcessorNode |
| 默认识别 | `EnergySegmentRecognizer.events` 在静音后等待整段转写；`windows_speech_input.ps1` 使用 MemoryStream 和同步 Recognize | 默认仍分段，转写等待会阻塞后续输入；没有静默切换到未经实测的新宿主 |
| 可选持续识别 | `native_recognizer.py`、`native_speech_host.cs`、`windows_speech_continuous.ps1` | 宿主源码和有序协议已实现，C# 库编译通过，子进程/浏览器链路经过替身测试；实际引擎与中文准确率未验收 |
| 模型 | `model_stream.py` 增量读取 SSE；`Model.complete` 共用原预算预留、上下文检查与账本结算 | 已接入官方 DeepSeek Flash/Pro 的本机语音路径；未验证的地址或型号仍走原普通适配，不伪称支持 SSE。完整回复先检查，尚未向语音释放 token 增量 |
| 系统合成 | `windows_speech_output.ps1` 仍同步写完整 WAV；`system_voice._cancellable_process` 轮询本次子进程并等待退出 | 新增受控取消，未增加策略绕过或换宿主；按句合成仍缓冲，未实测真实进程或改为连续 PCM 合成 |
| 可选原生PCM | `native_voice_stream.py` 从自建子进程标准输出按块读取；`windows_speech_stream.ps1` 使用 System.Speech 的 SetOutputToAudioStream | 实现及子进程替身测试已完成；脚本仅静态解析，未执行。须实测中文声音、格式和首帧时延，不能仅凭 API 名称宣称引擎实时流式 |
| 已装 Qwen TTS | 只读检查 `D:\DafeiyuVoice\venv\Lib\site-packages\qwen_tts\inference\qwen3_tts_model.py` | 安装包为 qwen_tts 0.1.1；接口返回完整音频数组，non_streaming_mode=false 也不等于真流式生成。没有加载模型或调用服务 |

Qwen 被检查源码 SHA-256：`e1da450732857c1f5fe3e36ebab85db2f6dc6a48caaff6973f463384e30275e4`。同一虚拟环境可见 torch 2.9.1+cpu、onnxruntime 1.31.1；它们的存在不证明可用的实时识别模型、模型权重或流式合成器已经安装。在这一环境的包清单未观察到 sherpa-onnx、vosk、faster-whisper 或 openai-whisper；没有据此推断整台机器不存在其他环境或服务。

## 本轮发现并修复的集成问题

1. 原先工作台停止路由持有数据库锁再等待语音关闭；语音工作线程可能正等待该锁完成账本工作，导致无法结束，并与激活路径的锁顺序相反。停止路由现在在共享数据库锁外等待语音组件，未确认停止则仍保留组件和状态。回归使用等待数据库锁的测试线程，旧代码产生失败，修复后可正常结束。
2. 启动中的麦克风授权或激活请求尚未返回时，遗留恢复不能先宣告空闲并允许新一轮启动，否则旧请求迟到会清理新的采集。现在等待旧启动明确收尾，阻止重入；恢复操作自身也限制为一个在途请求。测试不申请真实权限。
3. 会话在等待播放队列时取消，原先可能只结束响应任务，而把异步生成器清理留给后台，导致语音子进程尚未收尾就报告停止。已明确持有并关闭模型和音频生成器，等待其清理完成；清理未确认时保持停止中。新增测试重现了旧代码误报 stopped 的问题并验证修复；后续自定义生成器须实现 aclose。
4. 持续识别也明确持有并关闭事件生成器和 PCM 源，避免在处理打断时取消而把识别宿主清理留在后台；取消完成前要等待读写任务和自有子进程退出。

## SSE 与合成取消的实际改造

- SSE 启用范围固定为官方 `https://api.deepseek.com`（含 `/v1`）的 `deepseek-flash` 与 `deepseek-v4-pro`。自动装配只在该范围选择流式接收；没有为未知兼容服务猜协议，没有远端配置探测。直接请求不支持的流式地址会在读 Key 和发送前拒绝。
- 流式请求复用 Model 和 LocalChat 原有每轮预算、低谷闸门、请求去重、人设、所选记忆/知识和私人不缓存路径；请求设置 stream/include_usage。以不超过 4096 字节的小块读取、重组 UTF-8 与 SSE，限制总量、行与事件尺寸，并要求 stop、usage 和 DONE 齐全。工具调用、推理内容、候选混流、异常终止均不发布。
- 取消在网络读取边界生效；单次读取配置 15 秒网络超时，总处理限额 120 秒在读取边界检查。连接建立/DNS 和操作系统调度仍可能额外等待，因此不是立即取消保证。工作线程未返回时，界面保持停止中。发送前取消不发请求；发送后取消或缺终止用量保留不确定费用预留，不自动回退、重试或换请求号。
- 为保留跨块凭据检测和原回复规则，完整文本检查后才返回，故这一阶段是传输流式、文本整段释放。不能称为边生成边说话，也不宣称 HTTP 关闭会取消提供方费用。
- 系统合成适配器将取消信号传入相同的本机脚本路径；先检查已有 readiness，不改变执行策略。取消/超时仅终止本次自行创建的子进程并等待退出，不查询系统进程，不按名称结束其他程序。等待期间任务保持未完成，不返回部分 WAV。旧的无取消试听调用保持原接口。
- 新测试覆盖真实本机 SSE socket、跨网络块 UTF-8、预算阻止、低谷阻止、请求去重、用量结算、跨块凭据拦截、中断保留预留、浏览器至受控 SSE 再返回模拟声音，以及合成子进程的替身取消。没有运行系统语音脚本。

## 完整校验与播放时机

若要求“整条回复全部检查通过以前没有任何声音”，就不能在未知后续文本的情况下提前播放首句。后续流可能出现跨块凭据、违规承诺、异常结束或无 usage；提前播出的前缀无法撤回。当前保留完整校验要求，不以句号或某一块单独通过规则作为整条回复已经通过的证明。

新增 `_validated_speech_text` 位于缓冲和原生合成入口前：有界收集完整回复，确认生产者成功结束，再运行完整回复规则，之后才创建合成器。内容/字节/片段数量均有限额；后续非法内容、源错误或校验阶段取消都不会合成首句。这个闸门不读取 Key，不取代 Model 对真实凭据的跨块检查；生产者的隐私校验和计费守卫仍必须先完成。

## 可选原生 PCM 后端的具体实现

- `windows_speech_stream.ps1` 使用已有 Windows PowerShell 5.1 和 System.Speech，按原脚本的 `Microsoft Huihui Desktop - Chinese (Simplified)` 描述筛选已启用的 zh-CN 声音，再用其实际 VoiceInfo.Name 选择并复核，避免混用 COM 描述和 .NET 名称。将已审文本合成为指定 16 kHz/PCM16/单声道标准输出；未选择默认扬声器，未创建 WAV 文件，没有网络端点或凭据参数。未运行该脚本。
- `native_voice_stream.stream_pcm` 先检查原有 readiness，再以相同宿主的正常 `-File` 方式创建自有子进程。没有 Bypass、EncodedCommand、安装、策略修改、替代宿主或系统进程查询。文本只走 stdin，不进命令行；输出有界、重组奇数分块、拒绝明显容器头，错误不切其他服务或重试。
- 创建阶段收到取消时仍等待取得子进程句柄后清理；读取/播放取消时只停止该句柄并等待退出。确认退出前任务不结束。模拟测试包含迟到创建、读取取消、首帧先于模拟子进程退出、正常完成、异常退出、分块/格式错误以及完整校验前不启动。
- 工厂支持显式 `speech_backend='windows-pcm-stream'`，默认仍是 `buffered`；普通浏览器激活接口不接受这个新字段。集成模拟以明确指定的工厂参数将原生适配器接到真实本机 HTTP 与浏览器模块，原生提供者为替身，没有真实播音。
- 准备了 `check-native-pcm.py --confirm-local-pcm`：在获得对应执行许可后检查固定测试句的 PCM，只统计帧数、字节数和时间，不开麦、不播放、不接模型、不保存音频。它明确输出 `streaming_behavior_proven=false`；帧计数和首帧时间本身不等同于证明引擎持续生成。

## 持续识别宿主：代码与验证边界

原分段脚本不变。新增 C# 宿主以 zh-CN SpeechRecognitionEngine、DictationGrammar、SetInputToAudioStream 和 RecognizeAsync(RecognizeMode.Multiple) 接收持续原始 PCM，使用 SpeechDetected、SpeechRecognized、SpeechRecognitionRejected、RecognizeCompleted 产生有序事件。不选择默认音频设备，不打开麦克风，不写音频或文本文件，也不向模型发请求。InitialSilenceTimeout=0 依文档关闭起始静音检查；单个宿主至多运行 10 分钟。

Python 适配器在原 readiness 和明确确认后才使用相同 Windows PowerShell 的正常 -File 入口。握手只表示可以接收 PCM，不证明引擎或设备验收。输入泵和事件读取并行，输入/事件大小与时限有界；序号必须严格递增，每条最终结果必须匹配当前语句。重复、缺口、跨语句、未知/部分文本事件及缺少结束回执均停止，不发布为最终转写，不自动重启或回退。语句拒绝只计数，不调用模型。

创建阶段取消仍要等取得迟到的子进程句柄后清理；写入、读取、生成器关闭阶段取消均只处理自有子进程，并等其退出及读写任务结束后才完成。正常有限输入还要求输入已结束、completed.ok=true、输出 EOF 和进程退出码 0 同时成立，提前完成不能当作成功。长期开启和中文事件实际顺序仍需实机验证，异常顺序当前选择停止，不猜测配对。

工厂只在显式开发参数 `recognition_backend='windows-continuous'` 下选择该实现；普通浏览器激活接口未放开新字段，默认仍为 segmented。模拟覆盖同一宿主内两条语句在输入关闭前产生最终结果、非法输入、协议乱序、缺少/过早完成、迟到创建取消、等待退出回执，以及浏览器 → 实际本机 HTTP → 持续识别管道替身 → 模型替身 → 完整校验 → 模拟合成和播放。没有实际中文识别或模型付费调用。

本机现有 csc.exe 将源码编译为临时 /target:library，使用已安装 System.Speech 程序集，编译结果为 0。记录在工作区根的 BIGFISH-ASR-COMPILE-CHECK-20261104.json，包含输入和程序集哈希；临时输出删除，生成库没有加载或执行。这验证编译和引用，不验证音频流、识别精度或运行权限。PowerShell 包装入口仅做语法解析，没有运行 Add-Type；未来正式运行包装脚本会使用本机编译器，可能产生系统临时构建文件。

仍未观察到可直接复用且已验收的持续 ASR 服务；已查虚拟环境仅能说明上述特定库未安装，不能排除其他实现。System.Speech 中文识别器及非可寻址管道兼容性未核实。若它不可用，需先明确语言组件或其他引擎/模型的来源、许可、大小、哈希及资源需求再决定安装，不把已有 onnxruntime 当作识别服务。

## 可复现的跨层模拟

`test_duplex_http.DuplexHttpTests.test_incremental_fixture_output_and_barge_in_cross_browser_and_http` 启动临时本机 HTTP 服务，Node 从服务取得实际浏览器模块，并使用模拟音频设备：

1. 输入第一段合成 PCM；模拟模型只产生第一块内容，之后保持未完成。
2. 第一块 PCM 已穿过会话、路由和浏览器模块，交给模拟播放设备，证明传输无需等待模拟模型完成。
3. 第二段输入触发说话开始事件；第一模型生成器取消，旧播放停止；随后只允许第二轮 PCM。
4. 关闭会话后确认采集、播放上下文及后端会话均结束；真实识别、合成、模型及凭据读取函数被测试替身封锁。

这是对流式核心和传输的模拟证明，不是对真实识别、模型、合成、声音延迟、AEC 或付费账本的实测。

已有完整验证脚本 `verify_bigfish_voice_snapshot.py`（位于工作区根）会核对 v11 清单并在临时副本应用整合补丁后运行相关测试。单独复现以上测试可在源码目录执行 Python unittest 对应名称；不需要启动工作台、输入 Key、安装软件或申请麦克风权限。

## 明早最小待决事项

- **先审阅更新**：核对当前运行版与 v11 差异，保留数据，通过受支持流程合入本补丁。不能将本次对 v11 的验证视为已更新实际服务；不重复已被拒的进程查询。
- **系统语音许可是独立决定**：重新核对当前有效策略和完整语音脚本。此前拟议的 Windows PowerShell 5.1 CurrentUser RemoteSigned 会影响该用户的其他本地脚本，不是单文件许可，未经明确授权不修改。若用户不接受该范围，不能用换接口执行同一被拒操作来规避；应选择另行审阅的受支持部署方案。
- **先零费用、无设备验证**：审阅 PCM 脚本以及持续识别 PS/C# 宿主，获得正常执行许可后，可由代理分别运行 `check-native-pcm.py --confirm-local-pcm` 和 `check-native-asr.py --confirm-local-asr`。后者仅输入一秒合成静音，只输出计数，不记录识别正文。两者不需要 Key、麦克风或扬声器，也不证明准确率或实时效果。之后另行取得单次麦克风/播放许可，验证中文识别、音色、回声和关闭行为；不同时开启付费模型。语言组件或模型缺失时先报告准确来源与范围再决定安装。
- **付费会话另行批准**：旧的一次 DeepSeek 测试许可不等于连续语音许可。新的测试需明确次数/时长、单次与日上限、允许时段、所选记忆/知识是否可发送。结果不明或缺少 usage 不释放费用预留、不重试；停止不能承诺撤销已发生费用。
- **后续验收和界面接入**：持续识别宿主和原生 PCM 管道已有实现，但引擎、语句事件顺序、格式、延迟和设备关闭行为未验收；SSE 文本仍不在完整校验前释放，不能算真正实时双工。先做无设备检查，再决定界面接入和零费用设备联测，通过前不替换默认路径。

## 官方资料

- [DeepSeek Chat Completions：SSE 和末块 usage](https://api-docs.deepseek.com/api/create-chat-completion/)。提供者能力不代表当前账户或第三方兼容地址已验证，也不证明关闭 HTTP 能停止计费。
- [Microsoft SpeechRecognitionEngine.RecognizeAsync](https://learn.microsoft.com/en-us/dotnet/api/system.speech.recognition.speechrecognitionengine.recognizeasync?view=netframework-4.8.1)。异步多次识别与事件是 API 能力，不保证本机中文识别器已安装。
- [Microsoft SpeechSynthesizer.SpeakAsyncCancelAll](https://learn.microsoft.com/en-us/dotnet/api/system.speech.synthesis.speechsynthesizer.speakasynccancelall?view=netframework-4.8.1)。取消异步合成不是取消模型账单或撤回已经播放的声音。
- [Qwen 官方 Python 接口源码](https://github.com/QwenLM/Qwen3-TTS/blob/main/qwen_tts/inference/qwen3_tts_model.py)。以本机已检查版本为实施依据，不假定上游 main 和本机逐字节相同。
- [sherpa-onnx 官方项目](https://github.com/k2-fsa/sherpa-onnx)。存在可研究的流式识别实现；本轮未将它作为已安装或已选择的依赖。
- [Python Popen.communicate](https://docs.python.org/3/library/subprocess.html#subprocess.Popen.communicate)。子进程取消仅使用本次句柄；超时后继续收集时不重发输入，结束前需等待退出。
- [Microsoft SpeechSynthesizer.SetOutputToAudioStream](https://learn.microsoft.com/en-us/dotnet/api/system.speech.synthesis.speechsynthesizer.setoutputtoaudiostream?view=netframework-4.8.1)、[SpeechRecognitionEngine.SetInputToAudioStream](https://learn.microsoft.com/en-us/dotnet/api/system.speech.recognition.speechrecognitionengine.setinputtoaudiostream?view=netframework-4.8.1)。API 能力不能替代对本机引擎行为的测试。
- [Microsoft SelectVoice](https://learn.microsoft.com/en-us/dotnet/api/system.speech.synthesis.speechsynthesizer.selectvoice?view=netframework-4.8.1) 要求使用实际安装声音的 Name；不能把另一接口的描述字符串直接当名称。
- [RecognizeAsync](https://learn.microsoft.com/en-us/dotnet/api/system.speech.recognition.speechrecognitionengine.recognizeasync?view=netframework-4.8.1)、[RecognizeAsyncCancel](https://learn.microsoft.com/en-us/dotnet/api/system.speech.recognition.speechrecognitionengine.recognizeasynccancel?view=netframework-4.8.1)、[InitialSilenceTimeout](https://learn.microsoft.com/en-us/dotnet/api/system.speech.recognition.speechrecognitionengine.initialsilencetimeout?view=netframework-4.8.1) 是持续识别宿主的接口依据。
