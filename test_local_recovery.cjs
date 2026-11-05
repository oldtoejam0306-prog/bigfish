const assert=require('node:assert/strict'),Recovery=require('./bili_workbench/local_recovery.js'),{Document}=require('./test_report_view.cjs');
const KEY='bigfish-local-pending-v1',other='b'.repeat(24),meta={kind:'chat',request_id:'fixture-request-01',character_id:'default'};
const status=(item=meta,state='uncertain',reviewed=false)=>({...item,state,reviewed,automatic_retry:false,operation_replayed:false});
const pending=(items=[],char='default')=>({character_id:char,items,has_more:false,automatic_retry:false});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}};
function storage(saved=[]){const values=new Map([[KEY,JSON.stringify(saved)]]);return {getItem:k=>values.get(k),setItem:(k,v)=>values.set(k,v),values}}
function harness(request,options={}){const root=new Document().createElement('div'),store=options.storage||storage(),view=new Recovery({root,request,storage:store,newId:()=>meta.request_id,...options});return {root,store,view}}
async function ready(view){for(let i=0;i<100&&!view.ready;i++)await tick();assert(view.ready);for(let i=0;i<100&&view.loading;i++)await tick();assert.equal(view.loading,false)}
async function run(){
 {
  const calls=[];let resolved=0;const h=harness(async(path,options)=>{calls.push([path,options]);return path.endsWith('/pending')?pending():{status:'completed',reply:'fixture answer'}},{onResolved:()=>resolved++});assert.equal(calls.length,0);h.view.setCharacter('default');await ready(h.view);const result=await h.view.perform('chat',{text:'private draft',include_memory:true});assert.equal(result.outcome,'completed');assert.equal(resolved,1);assert(h.view.canStart('chat'));assert.equal(calls.filter(x=>x[1].method==='POST').length,1);assert.equal(h.store.getItem(KEY),'[]');assert(!JSON.stringify([...h.store.values]).includes('private draft'));
 }
 {
  const calls=[];const h=harness(async(path,options)=>{calls.push([path,options]);if(path.endsWith('/pending'))return pending();if(options.method==='POST')throw Error('lost response');return status(meta,'completed')});h.view.setCharacter('default');await ready(h.view);const result=await h.view.perform('chat',{text:'private draft'});assert.equal(result.outcome,'recovered');assert.equal(calls.filter(x=>x[1].method==='POST').length,1);assert(h.view.canStart('chat'));assert.equal(h.store.getItem(KEY),'[]');
 }
 {
  const store=storage([meta]),calls=[];let resolved=0;const h=harness(async(path,options)=>{calls.push([path,options]);return path.endsWith('/pending')?pending():status(meta,'completed')},{storage:store,onResolved:()=>resolved++});h.view.setCharacter('default');await ready(h.view);assert.equal(resolved,1);assert.equal(store.getItem(KEY),'[]');assert(calls.every(x=>x[1].method==='GET'));
 }
 {
  const h=harness(async(path)=>path.endsWith('/pending')?pending():status(meta,'not_found'),{storage:storage([meta])});h.view.setCharacter('default');await ready(h.view);assert.equal(h.view.canStart('chat'),false);assert(h.root.textContent.includes('尚未找到'));assert.equal((await h.view.perform('chat',{text:'must not send'})).outcome,'blocked');
 }
 {
  const calls=[];let reviewed=false,ask=false;const h=harness(async(path,options)=>{calls.push([path,options]);if(path.endsWith('/pending'))return pending([status()]);if(options.method==='POST'){reviewed=true;throw Error('ack response lost')}return status(meta,'uncertain',reviewed)},{confirm:()=>ask});h.view.setCharacter('default');await ready(h.view);const item=h.view.pending.values().next().value;await h.view.acknowledge(item);assert.equal(calls.filter(x=>x[1].method==='POST').length,0);ask=true;await h.view.acknowledge(item);assert.equal(calls.filter(x=>x[1].method==='POST').length,1);assert(calls.find(x=>x[1].method==='POST')[0].endsWith('/acknowledge'));assert.equal(h.view.pending.size,0);
 }
 {
  const old=deferred();const h=harness(path=>path.includes('/default/')?old.promise:Promise.resolve(pending([],other)));h.view.setCharacter('default');h.view.setCharacter(other);await ready(h.view);old.resolve(pending([status()]));await tick();assert.equal(h.view.character,other);assert.equal(h.view.pending.size,0);assert(h.view.canStart('chat'));assert.equal(h.view.canStart('memory'),false);
 }
 {
  let calls=0,fail=false;const store=storage(),h=harness(async(path,options)=>{calls++;return pending()},{storage:store});h.view.setCharacter('default');await ready(h.view);store.setItem=()=>{throw Error('storage denied')};const before=calls;const result=await h.view.perform('chat',{text:'private'});assert.equal(result.outcome,'blocked');assert.equal(calls,before);assert.equal(h.view.canStart('chat'),false);assert(h.root.textContent.includes('无法保留'));
 }
 {
  const h=harness(async()=>{const error=Error('raw secret must not appear');error.status=403;throw error});h.view.setCharacter('default');for(let i=0;i<6;i++)await tick();assert.equal(h.view.canStart('chat'),false);assert(h.root.textContent.includes('会话已失效'));assert(!h.root.textContent.includes('secret'));
 }
 {
  const request=deferred();let resolved=0;const h=harness(async(path,options)=>path.endsWith('/pending')?pending():options.method==='POST'?request.promise:status(meta,'completed'),{onResolved:()=>resolved++});h.view.setCharacter('default');await ready(h.view);const run=h.view.perform('chat',{text:'private'});await tick();await h.view.check(h.view.pending.values().next().value);request.resolve({status:'completed',reply:'late reply'});assert.equal((await run).outcome,'recovered');assert.equal(resolved,1);
 }
 {
  const memory={...meta,kind:'memory'},h=harness(async(path,options)=>{if(path.endsWith('/pending'))return pending();if(options.method==='POST')throw Error('lost');return {...status(memory,'completed'),memory_id:3}});h.view.setCharacter('default');await ready(h.view);const result=await h.view.perform('memory',{turn_id:1,content:'private memory',confirmation:'确认保存为主人记忆'});assert.equal(result.outcome,'recovered');assert(!h.store.getItem(KEY).includes('private memory'));
 }
 {
  const h=harness(async(path,options)=>{if(path.endsWith('/pending'))return pending();if(options.method==='POST'){const error=Error('rejected');error.status=400;throw error}return status(meta,'not_found')});h.view.setCharacter('default');await ready(h.view);await h.view.perform('chat',{text:'draft'});assert(h.view.canStart('chat'));assert.equal(h.view.pending.size,0);
 }
 {
  assert.throws(()=>harness(async()=>pending(),{storage:storage([{...meta,text:'must not store'}])}));
  const store=storage([{...meta,character_id:other}]),h=harness(async()=>pending(),{storage:store});h.view.setCharacter('default');await ready(h.view);assert(h.view.canStart('chat'));assert.equal(JSON.parse(store.getItem(KEY))[0].character_id,other);
 }
 {
  const fs=require('node:fs'),vm=require('node:vm'),line=fs.readFileSync(require('node:path').join(__dirname,'bili_workbench/index.html'),'utf8').split(/\r?\n/).find(x=>x.startsWith(' async function loadLocalChat(){')),history=deferred(),elements=new Map(),doc=new Document(),calls=[];
  const context={localChatEpoch:0,stickerChat:null,localRecovery:{character:null,setCharacter(value){this.character=value}},updateStickerButtons:()=>{},reviewedTurn:null,token:'fixture',localChat:{querySelector:()=>doc.createElement('h2')},document:{documentElement:{dataset:{preview:'false'}},getElementById:id=>{if(!elements.has(id))elements.set(id,doc.createElement('div'));return elements.get(id)}},fetch:async path=>{calls.push(path);return path.endsWith('/current')?{ok:true,json:async()=>({character_id:'default'})}:history.promise}};
  vm.runInNewContext(line+'\nthis.run=loadLocalChat',context);const run=context.run();await tick();assert.equal(context.localRecovery.character,'default');assert.deepEqual(calls,['/api/local-chat/recovery/current','/api/local-chat/history']);history.resolve({ok:true,json:async()=>({character_id:'default',character_name:'fixture',messages:[]})});await run;assert(elements.get('local_chat_history').textContent.includes('还没有'));
 }
 console.log('13 recovery fixture scenarios passed');
}
run().catch(error=>{console.error(error);process.exitCode=1});
