const assert=require('node:assert/strict'),vm=require('node:vm'),{Document,nodes}=require('./test_report_view.cjs');
async function main(){
 const [base,token,stickerId]=process.argv.slice(2),paths=[];
 async function request(path,options={method:'GET'}){
  paths.push(path);const init={method:options.method,headers:{'X-Local-Token':token},signal:options.signal};if(options.body){init.headers['Content-Type']='application/json';init.body=JSON.stringify(options.body)}
  const response=await fetch(base+path,init);assert.equal(response.status,200,path);return response.json();
 }
 const response=await fetch(base+'/sticker-chat.js');assert.equal(response.status,200);assert.equal(response.headers.get('cache-control'),'no-store');assert(response.headers.get('content-type').includes('javascript'));const code=await response.text(),sandbox={AbortController};vm.runInNewContext(code,sandbox);
 let changed=0;const chat=new sandbox.BigFishStickerChat({request,onChanged:()=>changed++});chat.setCharacter('default');await chat.send(stickerId,'loopback-sticker-01');assert.equal(changed,1);
 const history=await request('/api/local-chat/history');assert.equal(history.messages.length,1);assert.equal(history.messages[0].sticker.id,stickerId);assert(!JSON.stringify(history).includes('png_base64'));
 const root=new Document().createElement('span');chat.render(root,history.messages[0],history.character_id);const image=nodes(root).find(x=>x.tagName==='img');
 const deadline=Date.now()+3000;while(!image.src&&Date.now()<deadline)await new Promise(resolve=>setTimeout(resolve,5));assert(image.src&&image.src.startsWith('data:image/png;base64,iVBORw0KGgo'));image.listeners.load[0]();assert.equal(image.hidden,false);assert.equal(image.alt,'本机示例');
 const repeat=await request('/api/local-chat/sticker',{method:'POST',body:{request_id:'loopback-sticker-01',sticker_id:stickerId,character_id:'default'}});assert.equal(repeat.status,'already_recorded');assert.equal((await request('/api/local-chat/history')).messages.length,1);
 const page=await (await fetch(base+'/')).text();for(const marker of ['src="/sticker-chat.js"','stickerChat.render(text,message,data.character_id)','stickerChat.send(item.id,crypto.randomUUID())','id="local_chat_refresh"'])assert(page.includes(marker),marker);
 assert(!paths.some(path=>/model|onebot|send$|system-activity\/start/.test(path)));console.log('actual local sticker routes passed with fixture DOM; no model or platform sends');
}
main().catch(error=>{console.error(error);process.exitCode=1});
