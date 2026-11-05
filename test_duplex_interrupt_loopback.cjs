// Real loopback HTTP and shipped browser modules; all audio devices/providers fake.
const assert=require('node:assert/strict'),vm=require('node:vm');
const base=process.argv[2],token=process.argv[3],state={trackStops:0,closed:0,players:[],processor:null};
const waitFor=async(check)=>{const deadline=Date.now()+5000;while(!check()){if(Date.now()>deadline)throw Error('fixture condition timed out');await new Promise(resolve=>setTimeout(resolve,10))}};
const post=async(operation,payload)=>{
 const response=await fetch(base+'/api/duplex/'+operation,{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token,'Origin':base},body:JSON.stringify(payload)}),data=await response.json();
 if(!response.ok){const error=Error(data.error);error.definite=response.status===400||response.status===403;throw error}return data;
};
(async()=>{
 const context={Uint8Array,Float32Array,DataView,btoa,atob,setInterval,clearInterval};context.globalThis=context;vm.createContext(context);
 for(const path of ['/duplex-client.js','/duplex-audio.js'])vm.runInContext(await(await fetch(base+path)).text(),context);
 const track={stop(){state.trackStops++},getSettings(){return {echoCancellation:true}}},stream={getTracks:()=>[track],getAudioTracks:()=>[track]},node=()=>({connect(){},disconnect(){}});
 const audio={sampleRate:48000,currentTime:0,destination:{},async resume(){},async close(){state.closed++},createMediaStreamSource:()=>node(),createScriptProcessor(){state.processor=node();return state.processor},createGain(){return {...node(),gain:{value:1}}},createBuffer(ch,n,rate){const samples=new Float32Array(n);return {duration:n/rate,getChannelData:()=>samples}},createBufferSource(){const player={...node(),stopped:false,start(){player.sample=Math.round(player.buffer.getChannelData(0)[0]*32768);state.players.push(player)},stop(){player.stopped=true}};return player}};
 const session=new context.BigFishDuplexAudioSession({post,requestMicrophone:async()=>stream,createAudioContext:()=>audio,Client:context.BigFishDuplexClient,onState:()=>{}});
 const speak=async(amplitude)=>{state.processor.onaudioprocess({inputBuffer:{getChannelData:()=>new Float32Array(4800).fill(amplitude)}});await waitFor(()=>!session.client.sending&&!session.client.queue.length)};
 try{
  await session.start({headphones:true});assert.equal(session.phase,'active');
  await speak(.1);await waitFor(()=>state.players.some(player=>player.sample===111));
  // The first model generator remains blocked after its first chunk. Hearing
  // this fixture frame therefore proves incremental transport before completion.
  await speak(.2);await waitFor(()=>state.players.some(player=>player.sample===222));
  const firstSecond=state.players.findIndex(player=>player.sample===222);
  assert(state.players.slice(0,firstSecond).filter(player=>player.sample===111).every(player=>player.stopped),'old playback was not interrupted');
  assert(!state.players.slice(firstSecond).some(player=>player.sample===111),'stale output leaked after interruption');
  assert.equal((await session.stop()).state,'stopped');assert.equal(state.trackStops,1);assert.equal(state.closed,1);
  console.log('streaming fixture first chunk, barge-in and shutdown passed');
 }finally{if(session.phase!=='idle')await session.stop()}
})().catch(error=>{console.error(error);process.exitCode=1});
