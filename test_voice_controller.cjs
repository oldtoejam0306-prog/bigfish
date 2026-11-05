const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const page = fs.readFileSync(process.argv[2], 'utf8');
const match = page.match(/\/\* VOICE_CONTROLLER_BEGIN \*\/[\s\S]*?\/\* VOICE_CONTROLLER_END \*\//);
assert(match, 'voice controller is missing from the actual workbench');
const context = { Blob };vm.createContext(context);vm.runInContext(match[0]+'\nthis.createVoiceController=createVoiceController;', context);
const deferred=()=>{let resolve;const promise=new Promise(r=>{resolve=r});return {promise,resolve}};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function harness() {
  const state={allowed:true,stops:0,recorders:[],texts:[],status:[],busy:[],interrupts:0,timer:null};
  const stream={getTracks:()=>[{stop:()=>state.stops++}]};
  const deps={canStart:()=>state.allowed,getStream:async()=>stream,
    makeRecorder:()=>{const r={state:'inactive',mimeType:'audio/test',start(){this.state='recording'},stop(){this.state='inactive';queueMicrotask(()=>this.onstop())}};state.recorders.push(r);return r},
    interruptReply:()=>state.interrupts++,setTimer:fn=>{state.timer=fn;return 1},clearTimer:()=>{state.timer=null},
    setBusy:x=>state.busy.push(x),recording:()=>{},status:x=>state.status.push(x),
    transcribe:async()=>({recognized:true,text:'fixture'}),transcript:x=>state.texts.push(x.text)};
  return {state,stream,deps,session:context.createVoiceController(deps)};
}
(async()=>{
  {
    const h=harness(), pending=deferred();h.deps.getStream=()=>pending.promise;
    const running=h.session.start();await tick();h.session.cancel();pending.resolve(h.stream);await running;
    assert.equal(h.state.stops,1);assert.equal(h.state.recorders.length,0);assert.deepEqual(h.state.texts,[]);assert.equal(h.state.busy.at(-1),false);
  }
  {
    const h=harness();await h.session.start();await h.session.start();assert.equal(h.state.recorders.length,1);assert.equal(h.state.interrupts,1);
    h.state.recorders[0].ondataavailable({data:new Blob(['fixture'])});h.session.stop();await tick();
    assert.deepEqual(h.state.texts,['fixture']);assert.equal(h.state.stops,1);assert.equal(h.state.busy.at(-1),false);
  }
  {
    const h=harness(), pending=deferred();h.deps.transcribe=()=>pending.promise;await h.session.start();h.session.stop();await tick();h.session.cancel();pending.resolve({recognized:true,text:'stale'});await tick();
    assert.deepEqual(h.state.texts,[]);assert.equal(h.state.busy.at(-1),false);
  }
  {
    const h=harness();h.deps.makeRecorder=()=>{throw Error('fixture')};await h.session.start();assert.equal(h.state.stops,1);assert.equal(h.state.busy.at(-1),false);
  }
  {
    const h=harness();await h.session.start();h.state.allowed=false;h.session.cancel();await tick();assert.equal(h.state.stops,1);assert.deepEqual(h.state.texts,[]);
  }
  {
    const h=harness();await h.session.start();h.state.timer();await tick();assert.equal(h.state.stops,1);assert.deepEqual(h.state.texts,['fixture']);
  }
  {
    const h=harness();await h.session.start();const old=h.state.recorders[0];old.onerror();assert.equal(h.state.stops,1);assert.equal(h.state.busy.at(-1),false);
    await h.session.start();await old.onstop();assert.equal(h.state.stops,1);assert.equal(h.state.busy.at(-1),true);h.session.cancel();await tick();assert.equal(h.state.stops,2);
  }
  {
    const h=harness();await h.session.start();h.state.recorders[0].stop=()=>{throw Error('stop failure')};h.session.stop();assert.equal(h.state.stops,1);assert.equal(h.state.busy.at(-1),false);
  }
  {
    const setup=page.match(/let voiceReady=false,replyAudioUrl=null,replyAudioEpoch=0(?:,duplexController=null)?;[\s\S]*?function interruptVoiceReply\(\)\{[^\n]*\}/);
    const speak=page.match(/async function speakChatReply\(text\)\{[^\n]*\}/);
    assert(setup&&speak,'reply interruption is missing from workbench');
    const pending=deferred(),events={played:0,revoked:0,created:0};
    const player={pause(){},load(){},removeAttribute(){},async play(){events.played++}};
    const feedback={textContent:''};
    const env={voicePanel:{open:true},localChat:{classList:{contains:()=>false}},document:{hidden:false,getElementById:id=>id==='voice_reply_player'?player:feedback},token:'fixture',URL:{createObjectURL:()=>{events.created++;return 'blob:fixture'},revokeObjectURL:()=>events.revoked++},fetch:()=>pending.promise};
    vm.createContext(env);vm.runInContext(setup[0]+'\n'+speak[0]+'\nthis.speak=speakChatReply;this.interrupt=interruptVoiceReply;',env);
    const first=env.speak('fixture');env.interrupt();pending.resolve({ok:true,blob:async()=>new Blob(['fixture'])});await first;
    assert.equal(events.played,0);assert.equal(events.created,0);
    env.fetch=async()=>({ok:true,blob:async()=>new Blob(['fixture'])});await env.speak('fixture');assert.equal(events.played,1);env.interrupt();assert.equal(events.revoked,1);
  }
  console.log('voice controller: 9 simulated scenarios passed');
})().catch(error=>{console.error(error);process.exitCode=1});
