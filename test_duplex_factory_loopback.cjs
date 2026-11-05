const assert=require('node:assert/strict'),vm=require('node:vm');
const base=process.argv[2],token=process.argv[3],played=[];
const post=async(operation,payload)=>{const response=await fetch(base+'/api/duplex/'+operation,{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token,'Origin':base},body:JSON.stringify(payload)});const data=await response.json();if(!response.ok)throw Error(data.error);return data};
(async()=>{
 const source=await (await fetch(base+'/duplex-client.js')).text(),context={Uint8Array,btoa,atob};context.globalThis=context;vm.createContext(context);vm.runInContext(source,context);
 const client=new context.BigFishDuplexClient(post,{onFrame:async(bytes)=>played.push(bytes),onReset:()=>{},onError:message=>{throw Error(message)}});
 await client.start({confirmation:'确认开始语音会话',headphones_confirmed:true});
 const voiced=new Uint8Array(640),view=new DataView(voiced.buffer);for(let i=0;i<320;i++)view.setInt16(i*2,2000,true);const silence=new Uint8Array(640);
 for(const frame of [voiced,voiced,...Array(30).fill(silence)]){assert.equal(client.push(frame),true);while(client.sending||client.queue.length)await new Promise(resolve=>setTimeout(resolve,2))}
 for(let i=0;i<250&&!played.length;i++){await client.poll();await new Promise(resolve=>setTimeout(resolve,10))}
 assert(played.length>0,'no fixture speech PCM returned');assert.equal((await client.stop()).state,'stopped');console.log('factory loopback pipeline passed');
})().catch(e=>{console.error(e);process.exitCode=1});
