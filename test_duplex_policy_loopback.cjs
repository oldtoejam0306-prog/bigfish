const assert=require('node:assert/strict'),vm=require('node:vm');
const base=process.argv[2],token=process.argv[3];let microphones=0;
const post=async(op,payload)=>{const response=await fetch(base+'/api/duplex/'+op,{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token,'Origin':base},body:JSON.stringify(payload)});const data=await response.json();if(!response.ok){const error=Error(data.error);error.definite=response.status===400||response.status===403;throw error}return data};
(async()=>{
 const context={Uint8Array,Float32Array,DataView,btoa,atob,setInterval,clearInterval};context.globalThis=context;vm.createContext(context);
 for(const path of ['/duplex-client.js','/duplex-audio.js'])vm.runInContext(await (await fetch(base+path)).text(),context);
 const session=new context.BigFishDuplexAudioSession({post,requestMicrophone:async()=>{microphones++;throw Error('must not open')},createAudioContext:()=>{throw Error('must not create audio')},Client:context.BigFishDuplexClient,onState:()=>{}});
 await session.start({headphones:true,includeMemory:false,includeKnowledge:false});assert.equal(session.phase,'idle');assert.equal(microphones,0);console.log('policy denial before microphone passed');
})().catch(e=>{console.error(e);process.exitCode=1});
