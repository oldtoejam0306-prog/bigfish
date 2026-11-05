const assert=require('node:assert/strict'),vm=require('node:vm'),{Document,nodes}=require('./test_report_view.cjs');
async function main(){
 const [base,token]=process.argv.slice(2);
 async function request(path,options={method:'GET'}){const init={method:options.method,headers:{'X-Local-Token':token},signal:options.signal};if(options.body){init.headers['Content-Type']='application/json';init.body=JSON.stringify(options.body)}const response=await fetch(base+path,init);assert.equal(response.status,200,path);return response.json()}
 let reply;for(let i=0;i<4;i++)reply=await request('/api/local-chat/send',{method:'POST',body:{request_id:'browser-whale-'+i,text:'谢谢，你真可爱',character_id:'default'}});assert(reply.expression.sticker);assert.equal(reply.expression.voice_due,false);
 const history=await request('/api/local-chat/history'),message=history.messages.filter(x=>x.role==='assistant').at(-1);assert(message.expression.sticker);assert(!JSON.stringify(history).includes('png_base64'));
 const source=await (await fetch(base+'/sticker-chat.js')).text(),sandbox={AbortController};vm.runInNewContext(source,sandbox);const chat=new sandbox.BigFishStickerChat({request}),root=new Document().createElement('span');chat.setCharacter('default');chat.render(root,message,'default');const image=nodes(root).find(x=>x.tagName==='img');assert(image);const until=Date.now()+3000;while(!image.src&&Date.now()<until)await new Promise(resolve=>setTimeout(resolve,5));assert(image.src.startsWith('data:image/png;base64,iVBORw0KGgo'));image.listeners.load[0]();assert.equal(image.hidden,false);assert(root.textContent.includes('才没有'));assert(root.textContent.includes('开心鲸鱼'));
 const page=await (await fetch(base+'/')).text();assert(page.includes('outcome.result.expression.voice_due'));assert(page.includes('文字已保存；语音暂不可用'));console.log('assistant sticker HTTP fixture passed; no native voice or provider used');
}
main().catch(error=>{console.error(error);process.exitCode=1});
