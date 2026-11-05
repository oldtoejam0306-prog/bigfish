const assert=require('node:assert/strict'),StickerChat=require('./bili_workbench/sticker_chat.js'),{Document,nodes}=require('./test_report_view.cjs');
const id='a'.repeat(24),other='b'.repeat(24),png='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC';
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const message={id:7,role:'user',content:'[表情：示例]',sticker:{id,name:'示例',available:true}};
const data={turn_id:7,character_id:'default',id,name:'示例',available:true,png_base64:png};
function target(){return new Document().createElement('span')}
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
async function run(){
 {
  let calls=0;const chat=new StickerChat({request:async()=>{calls++;return data}});chat.setCharacter('default');
  for(const value of [{role:'user',id:1,content:'[表情：示例]'},{...message,role:'assistant'}]){const root=target();chat.render(root,value,'default');assert.equal(root.textContent,value.content);assert.equal(nodes(root).filter(x=>x.tagName==='img').length,0)}assert.equal(calls,0);
 }
 {
  const paths=[],chat=new StickerChat({request:async(path,options)=>{paths.push([path,options.method]);return data}});chat.setCharacter('default');const root=target();chat.render(root,message,'default');await tick();const image=nodes(root).find(x=>x.tagName==='img');assert.equal(image.src,'data:image/png;base64,'+png);assert.equal(image.hidden,true);image.listeners.load[0]();assert.equal(image.hidden,false);assert.equal(image.alt,'示例');assert.equal(root.textContent.trim(),'示例');assert.deepEqual(paths,[['/api/local-chat/sticker-image/default/7','GET']]);
 }
 {
  let calls=0;const chat=new StickerChat({request:async()=>{calls++}});chat.setCharacter('default');const root=target();chat.render(root,{...message,sticker:{...message.sticker,available:false}},'default');assert.equal(calls,0);assert(root.textContent.includes('已删除'));assert(!nodes(root).find(x=>x.tagName==='img').src);
 }
 {
  const pending=deferred();let signal;const chat=new StickerChat({request:(path,options)=>{signal=options.signal;return pending.promise}});chat.setCharacter('default');const root=target();chat.render(root,message,'default');const image=nodes(root).find(x=>x.tagName==='img');chat.setCharacter(other);assert.equal(signal.aborted,true);pending.resolve(data);await tick();assert(!image.src);image.listeners.load[0]();assert.equal(image.hidden,true);
 }
 {
  for(const invalid of [{...data,id:other},{...data,turn_id:8},{...data,character_id:other},{...data,png_base64:'https://example.test/image.png'},{...data,png_base64:'iVBORw0KGgo'+'a'.repeat(533336)}]){
   const chat=new StickerChat({request:async()=>invalid});chat.setCharacter('default');const root=target();chat.render(root,message,'default');await tick();assert(!nodes(root).find(x=>x.tagName==='img').src);assert(root.textContent.includes('暂不可用'));
  }
 }
 {
  const chat=new StickerChat({request:async()=>({...data,available:false})});chat.setCharacter('default');const root=target();chat.render(root,message,'default');await tick();assert(root.textContent.includes('已删除'));assert(!nodes(root).find(x=>x.tagName==='img').src);
 }
 {
  const pending=deferred(),calls=[],busy=[],feedback=[];let changed=0;const chat=new StickerChat({request:(path,options)=>{calls.push([path,options]);return pending.promise},onChanged:()=>changed++,onBusy:value=>busy.push(value),onFeedback:value=>feedback.push(value)});chat.setCharacter('default');const first=chat.send(id,'request-fixture-01');await chat.send(id,'request-fixture-02');assert.equal(calls.length,1);assert.equal(calls[0][0],'/api/local-chat/sticker');assert.deepEqual(calls[0][1].body,{request_id:'request-fixture-01',sticker_id:id,character_id:'default'});pending.resolve({status:'completed',character_id:'default',turn_id:7,model_called:false,platform_sent:false});await first;assert.equal(changed,1);assert.deepEqual(busy,[true,false]);assert(feedback.at(-1).includes('未调用模型'));
 }
 {
  const feedback=[];let calls=0;const chat=new StickerChat({request:async()=>{calls++;throw Error('do not display raw secret')},onFeedback:x=>feedback.push(x)});await chat.send(id,'request-fixture-01');assert.equal(calls,0);chat.setCharacter('default');await chat.send(id,'request-fixture-01');assert.equal(calls,1);assert(feedback.at(-1).includes('不会自动重发'));assert(!feedback.at(-1).includes('secret'));assert.equal(chat.sending,false);
 }
 {
  const pending=deferred(),feedback=[];let changed=0;const chat=new StickerChat({request:()=>pending.promise,onFeedback:x=>feedback.push(x),onChanged:()=>changed++});chat.setCharacter('default');const send=chat.send(id,'request-fixture-01');chat.setCharacter(other);pending.resolve({status:'completed',character_id:'default',turn_id:7,model_called:false,platform_sent:false});await send;assert.equal(changed,0);assert(feedback.at(-1).includes('发送时角色'));
 }
 {
  const name='🐋'.repeat(40),chat=new StickerChat({request:async()=>({...data,name})});chat.setCharacter('default');const root=target();chat.render(root,{...message,sticker:{...message.sticker,name}},'default');await tick();assert.equal(nodes(root).find(x=>x.tagName==='img').alt,name);
  const image=nodes(root).find(x=>x.tagName==='img');image.listeners.error[0]();assert.equal(image.hidden,true);assert(root.textContent.includes('暂不可用'));
 }
 {
  const feedback=[];let changed=0;const chat=new StickerChat({request:async()=>({status:'completed',character_id:'default',turn_id:7,model_called:true,platform_sent:false}),onFeedback:x=>feedback.push(x),onChanged:()=>changed++});chat.setCharacter('default');await chat.send(id,'request-fixture-01');assert.equal(changed,1);assert(feedback.at(-1).includes('未确认'));assert(!feedback.at(-1).includes('未调用模型'));
 }
 console.log('11 sticker fixture scenarios passed');
}
run().catch(error=>{console.error(error);process.exitCode=1});
