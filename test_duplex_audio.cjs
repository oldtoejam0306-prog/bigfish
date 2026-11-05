const assert=require('node:assert/strict'),DuplexAudioSession=require('./bili_workbench/duplex_audio.js');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function harness({echo=true}={}){
 const state={posts:[],mic:0,trackStops:0,contextCloses:0,clientStarts:0,clientStops:0,frames:[],polls:[],statuses:[],processor:null,players:[]};
 const track={stop(){state.trackStops++},getSettings(){return {echoCancellation:echo}}},stream={getTracks:()=>[track],getAudioTracks:()=>[track]};
 const node=()=>({connect(){},disconnect(){}});
 const ctx={sampleRate:48000,currentTime:0,destination:{},async resume(){},async close(){state.contextCloses++},createMediaStreamSource:()=>node(),createScriptProcessor(){state.processor=node();return state.processor},createGain(){return {...node(),gain:{value:1}}},createBuffer(ch,n,rate){const samples=new Float32Array(n);return {duration:n/rate,getChannelData:()=>samples}},createBufferSource(){const player={...node(),start(){},stop(){player.stopped=true}};state.players.push(player);return player}};
 const post=async(op,payload)=>{state.posts.push([op,payload]);if(op==='activate')return {activated:true,lease:'a'.repeat(32)};if(op==='deactivate')return {state:'stopped'};throw Error('unexpected post')};
 class Client {constructor(post,handlers){this.handlers=handlers;this.post=post}async start(options){state.clientStarts++;this.options=options}push(bytes){state.frames.push(bytes);return true}async poll(options){state.polls.push(options)}async stop(){state.clientStops++;return {state:'stopped'}}}
 const deps={post,requestMicrophone:async()=>{state.mic++;return stream},createAudioContext:()=>ctx,Client,onState:(phase,message)=>state.statuses.push([phase,message])};
 return {state,track,stream,ctx,deps,session:new DuplexAudioSession(deps)};
}
(async()=>{
 {
  const h=harness();await h.session.start({headphones:true});assert.equal(h.session.phase,'active');assert.equal(h.state.mic,1);assert.equal(h.state.clientStarts,1);
  h.state.processor.onaudioprocess({inputBuffer:{getChannelData:()=>new Float32Array(4800).fill(.5)}});assert.equal(h.state.frames.length,1);assert.equal(h.state.frames[0].length,3200);
  await h.session.stop();assert.equal(h.session.phase,'idle');assert.equal(h.state.trackStops,1);assert.equal(h.state.clientStops,1);assert.equal(h.state.posts.filter(x=>x[0]==='deactivate').length,1);
 }
 {
  const h=harness();h.deps.post=async()=>{const e=Error('策略限制');e.definite=true;throw e};h.session=new DuplexAudioSession(h.deps);await h.session.start({headphones:true});assert.equal(h.state.mic,0);assert.equal(h.session.phase,'idle');
 }
 {
  const h=harness();h.deps.post=async()=>{throw Error('连接中断')};h.session=new DuplexAudioSession(h.deps);await h.session.start({headphones:true});assert.equal(h.state.mic,0);assert.equal(h.session.phase,'unknown');assert.equal((await h.session.stop()).state,'unknown');await assert.rejects(h.session.start({headphones:true}));
 }
 {
  const h=harness(),pending=deferred();h.deps.requestMicrophone=()=>pending.promise;h.session=new DuplexAudioSession(h.deps);const starting=h.session.start({headphones:true});await tick();assert.equal((await h.session.stop()).state,'stopping');pending.resolve(h.stream);await starting;assert.equal(h.state.trackStops,1);assert.equal(h.state.clientStarts,0);assert.equal(h.session.phase,'idle');assert.equal(h.state.posts.filter(x=>x[0]==='deactivate').length,1);
 }
 {
  const h=harness({echo:false});await h.session.start({headphones:false});assert.equal(h.state.trackStops,1);assert.equal(h.state.clientStarts,0);assert.equal(h.session.phase,'idle');
 }
 {
  const h=harness();await h.session.start({headphones:true});await h.session.play(new Uint8Array([1,0]),h.session.epoch);assert.equal(h.state.players.length,1);h.session.stopPlayback();assert.equal(h.state.players[0].stopped,true);await h.session.stop();
 }
 {
  const h=harness(),pending=deferred();let stopRequested=false;h.deps.Client=class {constructor(){}async start(){await pending.promise;if(stopRequested)throw Error('cancelled')}async stop(){stopRequested=true;return {state:'stopping'}}};h.session=new DuplexAudioSession(h.deps);const starting=h.session.start({headphones:true});await tick();assert.equal((await h.session.stop()).state,'stopping');pending.resolve();await starting;assert.equal(h.state.trackStops,1);assert.equal(h.session.phase,'idle');assert.equal(h.state.posts.filter(x=>x[0]==='deactivate').length,1);
 }
 {
  const h=harness();let attempts=0;h.deps.post=async(op)=>{if(op==='activate'){attempts++;throw Error('连接中断')}if(op==='recover')return {state:'stopped'};throw Error('unexpected')};h.session=new DuplexAudioSession(h.deps);
  await h.session.start({headphones:true});assert.equal(h.session.phase,'unknown');assert.equal((await h.session.stop()).state,'unknown');assert.equal(attempts,1);
  await assert.rejects(h.session.recover(''));assert.equal((await h.session.recover('确认关闭遗留语音会话')).state,'stopped');assert.equal(h.session.phase,'idle');
 }
 {
  const h=harness();h.deps.post=async(op)=>{if(op==='activate'){const e=Error('已有会话');e.definite=true;e.recoverable=true;throw e}if(op==='recover')return {state:'stopped'};throw Error('unexpected')};h.session=new DuplexAudioSession(h.deps);
  await h.session.start({headphones:true});assert.equal(h.session.phase,'unknown');assert.equal(h.state.mic,0);await h.session.recover('确认关闭遗留语音会话');assert.equal(h.session.phase,'idle');
 }
 {
  const h=harness();h.ctx.sampleRate=44100;await h.session.start({headphones:true});h.state.processor.onaudioprocess({inputBuffer:{getChannelData:()=>new Float32Array(4410).fill(.2)}});assert.equal(h.state.frames.length,1);assert.equal(h.state.frames[0].length,3200);await h.session.stop();
 }
 {
  const h=harness();await h.session.start({headphones:true});h.session.nextPlay=1;h.session.pollPlayback(h.session.epoch);assert.equal(h.state.polls.at(-1).statusOnly,true);h.session.nextPlay=0;h.session.pollPlayback(h.session.epoch);assert.equal(h.state.polls.at(-1).statusOnly,false);await h.session.stop();
 }
 {
  const h=harness();await h.session.start({headphones:true});h.session.client.poll=async()=> 'stopped';await h.session.pollPlayback(h.session.epoch);
  assert.equal(h.session.phase,'idle');assert.equal(h.state.trackStops,1);assert.equal(h.state.contextCloses,1);assert.equal(h.state.clientStops,0);assert.equal(h.state.posts.filter(x=>x[0]==='deactivate').length,1);assert.equal(h.session.timer,null);
 }
 {
  const h=harness(),pending=deferred();await h.session.start({headphones:true});h.session.client.poll=()=>pending.promise;const polling=h.session.pollPlayback(h.session.epoch);await h.session.stop();await h.session.start({headphones:true});pending.resolve('stopped');await polling;
  assert.equal(h.session.phase,'active');assert.equal(h.state.trackStops,1);await h.session.stop();
 }
 {
  const h=harness(),pending=deferred();h.deps.requestMicrophone=()=>pending.promise;h.deps.post=async(op,payload)=>{h.state.posts.push([op,payload]);if(op==='activate')return {activated:true,lease:'a'.repeat(32)};return {state:'stopped'}};h.session=new DuplexAudioSession(h.deps);
  const starting=h.session.start({headphones:true});await tick();await h.session.stop();
  const recovery=await h.session.recover('确认关闭遗留语音会话');
  try{
   assert.equal(recovery.state,'stopping');assert.equal(h.session.phase,'stopping');assert.equal(h.state.posts.filter(x=>x[0]==='recover').length,0);await assert.rejects(h.session.start({headphones:true}));
  }finally{pending.resolve(h.stream);await starting;if(h.session.phase!=='idle')await h.session.stop()}
  assert.equal(h.state.trackStops,1);assert.equal(h.session.phase,'idle');
 }
 console.log('duplex audio controls: 14 simulated scenarios passed');
})().catch(error=>{console.error(error);process.exitCode=1});
