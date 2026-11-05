const assert = require('node:assert/strict');
const DuplexClient=require('./bili_workbench/duplex_client.js');
if(!globalThis.btoa)globalThis.btoa=x=>Buffer.from(x,'binary').toString('base64');
if(!globalThis.atob)globalThis.atob=x=>Buffer.from(x,'base64').toString('binary');
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const id='a'.repeat(32);
function harness() {
  const state={calls:[],played:[],resets:[],errors:[],gen:0,output:null,remoteState:'listening'};
  const post=async(op,data)=>{state.calls.push([op,data]);if(op==='start')return {id,state:'listening'};if(op==='feed')return {accepted:true,sequence:data.sequence};if(op==='poll')return {state:state.remoteState,generation:state.gen,output:state.output};if(op==='stop')return {state:'stopped',automatic_retry:false};throw Error('bad op')};
  const client=new DuplexClient((...args)=>post(...args),{onFrame:async(bytes,seq)=>state.played.push([Array.from(bytes),seq]),onReset:gen=>state.resets.push(gen),onError:message=>state.errors.push(message)});
  return {state,client};
}
(async()=>{
  {
    const h=harness();await h.client.start({confirmation:'fixture'});h.client.push(new Uint8Array([1,0]));h.client.push(new Uint8Array([2,0]));await tick();
    assert.deepEqual(h.state.calls.filter(x=>x[0]==='feed').map(x=>x[1].sequence),[1,2]);await h.client.stop();
  }
  {
    const h=harness();await h.client.start({});h.state.output={sequence:1,pcm_base64:btoa('\x01\x00')};await h.client.poll();await h.client.poll();
    assert.equal(h.state.played.length,1);assert.equal(h.client.ack,1);h.state.gen=1;h.state.output=null;await h.client.poll();assert.deepEqual(h.state.resets,[0,1]);await h.client.stop();
  }
  {
    const h=harness(),pending=deferred();const original=h.client.post;h.client.post=(op,data)=>op==='feed'?pending.promise:original(op,data);
    await h.client.start({});h.client.push(new Uint8Array([1,0]));h.client.push(new Uint8Array([2,0]));pending.reject(Error('fixture disconnect'));await tick();await tick();
    assert.equal(h.client.stopped,true);assert.equal(h.state.calls.filter(x=>x[0]==='stop').length,1);assert.equal(h.state.errors.length,1);assert.equal(h.client.sequence,1);
  }
  {
    const h=harness(),pending=deferred();const original=h.client.post;h.client.post=(op,data)=>op==='start'?pending.promise:original(op,data);
    const starting=h.client.start({});await tick();assert.equal((await h.client.stop()).state,'stopping');pending.resolve({id,state:'listening'});
    await assert.rejects(starting);assert.equal(h.client.stopped,true);assert.equal(h.state.calls.filter(x=>x[0]==='stop').length,1);
  }
  {
    const h=harness();await h.client.start({});h.state.remoteState='error';await h.client.poll();await tick();assert.equal(h.client.stopped,true);assert.equal(h.state.calls.filter(x=>x[0]==='stop').length,1);
  }
  {
    const h=harness();await h.client.start({});h.state.output={sequence:1,pcm_base64:btoa('x')};await h.client.poll();await tick();assert.equal(h.client.stopped,true);assert.equal(h.state.played.length,0);
  }
  {
    const h=harness(),pending=deferred();const original=h.client.post;h.client.post=(op,data)=>op==='feed'?pending.promise:original(op,data);
    h.client.maxQueued=1;await h.client.start({});h.client.push(new Uint8Array([1,0]));assert.equal(h.client.push(new Uint8Array([2,0])),false);pending.resolve({accepted:true,sequence:1});await tick();assert.equal(h.client.stopped,true);assert.equal(h.state.calls.filter(x=>x[0]==='stop').length,1);
  }
  {
    const h=harness();await h.client.start({});await h.client.poll({statusOnly:true});assert.equal(h.state.calls.filter(x=>x[0]==='poll')[0][1].status_only,true);await h.client.stop();
  }
  console.log('browser duplex client: 8 simulated scenarios passed');
})().catch(e=>{console.error(e);process.exitCode=1});
