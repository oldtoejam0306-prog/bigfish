/* Explicitly started browser audio session. Importing this file has no side
 * effects. The backend and microphone may be unavailable; no fallback sends
 * audio to another service. */
(function(global){
 class DuplexAudioSession {
  constructor({post,requestMicrophone,createAudioContext,Client,onState}){
   if(typeof post!=='function'||typeof requestMicrophone!=='function'||typeof createAudioContext!=='function'||typeof Client!=='function'||typeof onState!=='function')throw Error('语音设备配置无效');
   Object.assign(this,{post,requestMicrophone,createAudioContext,Client,onState});this.phase='idle';this.epoch=0;this.lease=null;this.activationUnknown=false;this.stream=null;this.context=null;this.source=null;this.processor=null;this.gain=null;this.client=null;this.timer=null;this.players=new Set();this.nextPlay=0;this.pending=[];this.sum=0;this.count=0;this.resamplePhase=0;this.startPromise=null;this.recovering=false;
  }
  report(phase,message){this.phase=phase;this.onState(phase,message)}
  live(id){return id===this.epoch}
  async deactivate(){
   if(!this.lease)return {state:'stopped'};
   const result=await this.post('deactivate',{lease:this.lease});
   if(result&&result.state==='stopped')this.lease=null;
   return result;
  }
  async finishCanceled(){
   this.localCleanup();
   try{const result=await this.deactivate();if(result&&result.state==='stopped'){this.client=null;this.report('idle','语音会话已停止。')}else this.report('stopping','语音组件仍在停止，请稍后检查状态。')}
   catch(error){this.report('unknown','语音关闭状态不明，请检查工作台。')}
  }
  stopPlayback(){
   for(const item of this.players){try{item.stop()}catch(ignore){}}this.players.clear();this.nextPlay=this.context?this.context.currentTime:0;
  }
  localCleanup(){
   if(this.timer!==null){clearInterval(this.timer);this.timer=null}this.stopPlayback();
   if(this.processor){this.processor.onaudioprocess=null;try{this.processor.disconnect()}catch(ignore){}this.processor=null}
   if(this.source){try{this.source.disconnect()}catch(ignore){}this.source=null}
   if(this.gain){try{this.gain.disconnect()}catch(ignore){}this.gain=null}
   if(this.stream){for(const track of this.stream.getTracks())track.stop();this.stream=null}
   if(this.context){const old=this.context;this.context=null;try{Promise.resolve(old.close()).catch(()=>{})}catch(ignore){}}
   this.pending=[];this.sum=0;this.count=0;this.resamplePhase=0;
  }
  async start(options){
   if(this.phase!=='idle'||this.startPromise||this.recovering)throw Error('语音会话正在启动或尚未关闭');
   const epoch=++this.epoch;this.report('starting','正在检查语音与预算条件…');
   const run=async()=>{
    let activated=false;
    try{
     const activation=await this.post('activate',{speech_confirmation:'确认使用本机语音识别和合成',model_confirmation:'确认语音会话按预算自动调用模型',echo_mode:options.headphones?'headphones':'browser-echo-cancellation',include_memory:!!options.includeMemory,include_knowledge:!!options.includeKnowledge,context_confirmation:options.includeMemory||options.includeKnowledge?'确认将所选记忆或知识发送模型':''});
     if(!activation||activation.activated!==true||typeof activation.lease!=='string'||!/^[0-9a-f]{32}$/.test(activation.lease))throw Error('语音启动状态不明，请检查工作台');
     this.lease=activation.lease;activated=true;
     if(!this.live(epoch)){await this.finishCanceled();return}
     const acquired=await this.requestMicrophone();
     if(!this.live(epoch)){for(const track of acquired.getTracks())track.stop();await this.finishCanceled();return}
     this.stream=acquired;const track=acquired.getAudioTracks()[0];if(!track)throw Error('未取得麦克风音轨');
     const settings=typeof track.getSettings==='function'?track.getSettings():{};const echo=settings.echoCancellation===true;
     if(!options.headphones&&!echo)throw Error('浏览器未报告启用回声消除，请确认使用耳机后重试');
     this.context=this.createAudioContext();if(!this.context||this.context.sampleRate<16000||typeof this.context.createScriptProcessor!=='function')throw Error('浏览器不支持当前音频采集方式');
     if(typeof this.context.resume==='function')await this.context.resume();
     if(!this.live(epoch)){await this.finishCanceled();return}
     const ctx=this.context;
     this.client=new this.Client(this.post,{onFrame:async(bytes)=>this.play(bytes,epoch),onReset:()=>this.stopPlayback(),onError:message=>{if(this.live(epoch)){++this.epoch;this.localCleanup();this.report('unknown',message)}}});
     await this.client.start({confirmation:'确认开始语音会话',headphones_confirmed:!!options.headphones,browser_echo_reported:echo,lease:this.lease});
     if(!this.live(epoch)){await this.client.stop();await this.finishCanceled();return}
     this.source=ctx.createMediaStreamSource(this.stream);this.processor=ctx.createScriptProcessor(2048,1,1);this.gain=ctx.createGain();this.gain.gain.value=0;
     this.processor.onaudioprocess=event=>this.input(event.inputBuffer.getChannelData(0),epoch);this.source.connect(this.processor);this.processor.connect(this.gain);this.gain.connect(ctx.destination);
     this.report('active','语音会话已开始；说话前请确认预算。');
     this.timer=setInterval(()=>this.pollPlayback(epoch),30);
    }catch(error){
     this.localCleanup();
     if(activated&&this.lease){
      try{const result=await this.deactivate();if(result.state!=='stopped'){this.report('stopping','语音组件仍在停止，请检查状态。');return}}
      catch(ignore){this.report('unknown','语音关闭状态不明，请检查工作台。');return}
     }
     if(!activated&&(!error.definite||error.recoverable))this.activationUnknown=true;
     if(!this.live(epoch)&&this.phase==='stopping'&&!this.activationUnknown){this.report('idle','语音会话已停止。');return}
     if(this.live(epoch))this.report((activated||error.definite)&&!error.recoverable?'idle':'unknown',error.message||'语音启动未完成');
     else if(this.activationUnknown)this.report('unknown','语音启动结果不明，请检查工作台，不要重复启动。');
    }
   };
   this.startPromise=run();try{await this.startPromise}finally{this.startPromise=null}
  }
  async pollPlayback(epoch){
   if(!this.live(epoch)||!this.client||!this.context)return;
   const state=await this.client.poll({statusOnly:this.nextPlay-this.context.currentTime>=.75});
   if(state==='stopped'&&this.live(epoch)){
    ++this.epoch;this.report('stopping','服务端语音会话已结束，正在关闭本地音频…');await this.finishCanceled();
   }
  }
  input(samples,epoch){
   if(!this.live(epoch)||this.phase!=='active'||!this.context||!this.client)return;
   const rate=this.context.sampleRate;
   for(const sample of samples){
    this.sum+=sample;this.count++;this.resamplePhase+=16000;
    if(this.resamplePhase>=rate){
     const value=Math.max(-1,Math.min(1,this.sum/this.count));this.pending.push(value<0?Math.round(value*32768):Math.round(value*32767));this.sum=0;this.count=0;this.resamplePhase-=rate;
     if(this.pending.length===1600){const bytes=new Uint8Array(3200),view=new DataView(bytes.buffer);for(let i=0;i<1600;i++)view.setInt16(i*2,this.pending[i],true);this.pending=[];try{if(!this.client.push(bytes))return}catch(error){this.failLocal('音频传输已停止，请检查会话状态。');return}}
    }
   }
  }
  async play(bytes,epoch){
   if(!this.live(epoch)||this.phase!=='active'||!this.context)return;
   const ctx=this.context;if(this.nextPlay-ctx.currentTime>3)throw Error('播放缓冲过多，已停止语音会话');
   const buffer=ctx.createBuffer(1,bytes.length/2,16000),samples=buffer.getChannelData(0),view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
   for(let i=0;i<samples.length;i++)samples[i]=view.getInt16(i*2,true)/32768;
   const player=ctx.createBufferSource();player.buffer=buffer;player.connect(ctx.destination);this.players.add(player);player.onended=()=>this.players.delete(player);
   this.nextPlay=Math.max(ctx.currentTime+.04,this.nextPlay);player.start(this.nextPlay);this.nextPlay+=buffer.duration;
  }
  failLocal(message){++this.epoch;this.localCleanup();this.report('unknown',message)}
  async recover(confirmation){
   if(confirmation!=='确认关闭遗留语音会话')throw Error('需要明确确认关闭遗留语音会话');
   if(this.recovering)return {state:'stopping'};
   if(this.startPromise){++this.epoch;this.localCleanup();this.report('stopping','启动请求或麦克风授权尚未返回，请先结束浏览器授权提示，勿重复启动。');return {state:'stopping'}}
   this.recovering=true;
   ++this.epoch;this.localCleanup();
   try{
    const result=await this.post('recover',{confirmation});
    if(result&&result.state==='stopped'){this.lease=null;this.client=null;this.activationUnknown=false;this.report('idle','遗留语音会话已关闭。');return result}
    this.report('stopping','语音组件仍在停止，请稍后检查状态。');return {state:'stopping'};
   }catch(error){this.report('unknown','语音关闭状态不明，请检查工作台。');return {state:'unknown'}}
   finally{this.recovering=false}
  }
  async stop(){
   ++this.epoch;const prior=this.phase;this.localCleanup();
   if(prior==='idle')return {state:'stopped'};
   if(this.activationUnknown){this.report('unknown','语音启动结果不明，请检查工作台，不要重复启动。');return {state:'unknown'}}
   if(prior==='starting'){
    if(this.client)try{await this.client.stop()}catch(ignore){}
    this.report('stopping','正在结束启动中的语音会话…');return {state:'stopping'};
   }
   if(prior==='active'&&this.client){
    try{const result=await this.client.stop();if(!result||result.state!=='stopped'){this.report('stopping','语音组件仍在停止，请稍后检查状态。');return {state:'stopping'}}}
    catch(error){this.report('unknown','语音停止状态不明，请检查工作台。');return {state:'unknown'}}
   }
   try{
    const result=await this.deactivate();
    if(result&&result.state==='stopped'){this.client=null;this.report('idle','语音会话已停止。');return result}
    this.report('stopping','语音组件仍在停止，请稍后检查状态。');return {state:'stopping'};
   }catch(error){this.report('unknown','语音停止状态不明，请检查工作台。');return {state:'unknown'}}
  }
 }
 if(typeof module!=='undefined'&&module.exports)module.exports=DuplexAudioSession;else global.BigFishDuplexAudioSession=DuplexAudioSession;
})(globalThis);
