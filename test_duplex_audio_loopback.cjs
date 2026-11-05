const assert=require('node:assert/strict'),vm=require('node:vm');
const base=process.argv[2],token=process.argv[3],state={trackStops:0,played:0,processor:null};
const post=async(operation,payload)=>{const response=await fetch(base+'/api/duplex/'+operation,{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token,'Origin':base},body:JSON.stringify(payload)});const data=await response.json();if(!response.ok){const e=Error(data.error);e.definite=response.status===400||response.status===403;throw e}return data};
(async()=>{
 const context={Uint8Array,Float32Array,DataView,btoa,atob,setInterval,clearInterval};context.globalThis=context;vm.createContext(context);
 for(const path of ['/duplex-client.js','/duplex-audio.js'])vm.runInContext(await (await fetch(base+path)).text(),context);
 const track={stop(){state.trackStops++},getSettings(){return {echoCancellation:true}}},stream={getTracks:()=>[track],getAudioTracks:()=>[track]};
 const node=()=>({connect(){},disconnect(){}}),ctx={sampleRate:48000,currentTime:0,destination:{},async resume(){},async close(){},createMediaStreamSource:()=>node(),createScriptProcessor(){state.processor=node();return state.processor},createGain(){return {...node(),gain:{value:1}}},createBuffer(ch,n,rate){const samples=new Float32Array(n);return {duration:n/rate,getChannelData:()=>samples}},createBufferSource(){return {...node(),start(){state.played++},stop(){}}}};
 const session=new context.BigFishDuplexAudioSession({post,requestMicrophone:async()=>stream,createAudioContext:()=>ctx,Client:context.BigFishDuplexClient,onState:()=>{}});
 try{
  await session.start({headphones:true,includeMemory:false,includeKnowledge:false});assert.equal(session.phase,'active');
  for(const amplitude of [.08,.08,0,0,0,0,0,0]){
   state.processor.onaudioprocess({inputBuffer:{getChannelData:()=>new Float32Array(4800).fill(amplitude)}});
   while(session.client.sending||session.client.queue.length)await new Promise(resolve=>setTimeout(resolve,2));
  }
  for(let i=0;i<300&&!state.played;i++)await new Promise(resolve=>setTimeout(resolve,10));
  assert(state.played>0,'no fixture playback');assert.equal((await session.stop()).state,'stopped');assert.equal(state.trackStops,1);
  console.log('browser audio to workbench fixture passed');
 } finally {if(session.phase!=='idle')await session.stop()}
})().catch(e=>{console.error(e);process.exitCode=1});
