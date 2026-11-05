const assert=require('node:assert/strict');
const vm=require('node:vm');
const base=process.argv[2],token=process.argv[3],played=[],resets=[];
const post=async(operation,payload)=>{const response=await fetch(base+'/api/duplex/'+operation,{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token,'Origin':base},body:JSON.stringify(payload)});const data=await response.json();if(!response.ok)throw Error(data.error);return data};
(async()=>{
 const source=await (await fetch(base+'/duplex-client.js')).text();const context={Uint8Array,btoa,atob};context.globalThis=context;vm.createContext(context);vm.runInContext(source,context);const DuplexClient=context.BigFishDuplexClient;
 const client=new DuplexClient(post,{onFrame:async(bytes,seq)=>played.push([Array.from(bytes),seq]),onReset:gen=>resets.push(gen),onError:message=>{throw Error(message)}});
 await client.start({confirmation:'确认开始语音会话',headphones_confirmed:true});
 client.push(new Uint8Array([1,0]));client.push(new Uint8Array([2,0]));
 for(let i=0;i<100&&played.length<2;i++){await client.poll();await new Promise(resolve=>setTimeout(resolve,10))}
 assert.deepEqual(played.map(x=>x[0]),[[1,0],[2,0]]);await client.poll();assert.equal(played.length,2);
 assert.equal((await client.stop()).state,'stopped');console.log('loopback browser transport passed');
})().catch(e=>{console.error(e);process.exitCode=1});
