/* Browser-side local duplex transport. No microphone or network is started
 * by importing this file. The caller supplies an authenticated same-origin
 * post function, audio capture and playback implementation. */
(function (global) {
  class DuplexClient {
    constructor(post, {onFrame, onReset, onError, maxQueued = 4}) {
      if (typeof post !== 'function' || typeof onFrame !== 'function' || typeof onReset !== 'function' || typeof onError !== 'function' || !Number.isInteger(maxQueued) || maxQueued < 1 || maxQueued > 16) throw Error('语音客户端配置无效');
      Object.assign(this,{post,onFrame,onReset,onError,maxQueued});
      this.id=null;this.starting=false;this.sequence=0;this.ack=0;this.generation=null;this.queue=[];this.sending=false;this.polling=false;this.stopped=true;this.stopRequested=false;
    }
    static encode(bytes) {let value='';for(let i=0;i<bytes.length;i+=4096)value+=String.fromCharCode(...bytes.subarray(i,i+4096));return btoa(value)}
    static decode(value) {
      if(typeof value!=='string'||value.length>42668)throw Error('播放音频编码无效');
      const raw=atob(value),bytes=new Uint8Array(raw.length);
      if(!bytes.length||bytes.length>32000||bytes.length%2)throw Error('播放音频格式无效');
      for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);
      return bytes;
    }
    async start(options) {
      if(this.id!==null||this.starting||this.stopRequested)throw Error('语音会话已开始或已停止');
      this.starting=true;
      try {
        const result=await this.post('start',options);
        if(!result||typeof result.id!=='string'||!/^[0-9a-f]{32}$/.test(result.id))throw Error('语音会话启动状态无效');
        this.id=result.id;
        if(this.stopRequested){
          try{await this.post('stop',{id:this.id})}catch(e){try{this.onError('启动后的停止结果不明，请检查会话状态。')}catch(ignore){}}
          throw Error('语音启动后已请求停止');
        }
        this.stopped=false;return result;
      } catch(error) {
        if(!this.id){this.stopRequested=true;try{this.onError('语音启动结果不明，请检查会话状态，不要重复启动。')}catch(ignore){}}
        throw error;
      } finally {this.starting=false}
    }
    push(bytes) {
      if(this.stopped||!(bytes instanceof Uint8Array)||!bytes.length||bytes.length>3200||bytes.length%2)throw Error('输入音频帧无效或会话已停止');
      if(this.queue.length+(this.sending?1:0)>=this.maxQueued){this.fail('音频发送跟不上，已停止接收；请检查会话状态。');return false}
      this.queue.push(bytes.slice());void this.pump();return true;
    }
    async pump() {
      if(this.sending||this.stopped)return;this.sending=true;
      try {
        while(this.queue.length&&!this.stopped){
          const bytes=this.queue.shift(),sequence=++this.sequence;
          const result=await this.post('feed',{id:this.id,sequence,pcm_base64:DuplexClient.encode(bytes)});
          if(!result||result.accepted!==true||result.sequence!==sequence)throw Error('音频接收状态不明');
        }
      } catch(e) {this.fail('音频发送结果不明，已停止本地传输；请检查会话是否已停止。')}
      finally {this.sending=false}
    }
    async poll({statusOnly=false}={}) {
      if(typeof statusOnly!=='boolean')throw Error('播放状态选项无效');
      if(this.stopped||this.polling)return;this.polling=true;
      try {
        const result=await this.post('poll',{id:this.id,ack:this.ack,status_only:statusOnly});
        if(this.stopped)return;
        if(!result||!Number.isInteger(result.generation)||result.generation<0||!['listening','responding','error','stopping','stopped'].includes(result.state))throw Error('语音播放状态无效');
        if(['error','stopping','stopped'].includes(result.state)){
          if(result.state==='error')this.fail('语音组件发生错误，已停止本地传输；请检查会话状态。');
          else{this.stopped=true;this.queue=[];try{this.onReset(null)}catch(ignore){}if(result.state==='stopping')try{this.onError('语音组件仍在停止，请检查会话状态。')}catch(ignore){}}
          return result.state;
        }
        if(this.generation!==result.generation){this.generation=result.generation;this.onReset(result.generation)}
        if(statusOnly&&result.output)throw Error('状态检查包含意外音频');
        if(result.output){
          const sequence=result.output.sequence;
          if(!Number.isSafeInteger(sequence)||sequence<1)throw Error('播放序号无效');
          if(sequence>this.ack){const pcm=DuplexClient.decode(result.output.pcm_base64);await this.onFrame(pcm,sequence);if(!this.stopped)this.ack=sequence}
        }
        return result.state;
      } catch(e) {this.fail('播放状态不明，已停止本地传输；请检查会话是否已停止。')}
      finally {this.polling=false}
    }
    fail(message) {
      if(this.stopped)return;
      this.stopped=true;this.queue=[];try{this.onReset(null)}catch(ignore){}try{this.onError(message)}catch(ignore){}
      if(this.id&&!this.stopRequested){this.stopRequested=true;try{Promise.resolve(this.post('stop',{id:this.id})).catch(()=>{})}catch(ignore){}}
    }
    async stop() {
      if(this.stopRequested)return {state:'unknown',automatic_retry:false};
      this.stopped=true;this.queue=[];try{this.onReset(null)}catch(ignore){}this.stopRequested=true;
      if(!this.id)return {state:this.starting?'stopping':'stopped',automatic_retry:false};
      return await this.post('stop',{id:this.id});
    }
  }
  if(typeof module!=='undefined'&&module.exports)module.exports=DuplexClient;
  else global.BigFishDuplexClient=DuplexClient;
})(globalThis);
