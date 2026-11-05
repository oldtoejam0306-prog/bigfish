const assert=require('node:assert/strict'),ReportView=require('./bili_workbench/report_view.js');
class Element{
 constructor(tag,doc){this.tagName=tag;this.ownerDocument=doc;this.children=[];this.attributes={};this.listeners={};this.hidden=false;this.disabled=false;this.value='';this.own='';this.classList={add:()=>{}}}
 set textContent(text){this.own=String(text);this.children=[]}get textContent(){return this.own+this.children.map(x=>x.textContent).join(' ')}
 set innerHTML(value){throw Error('Report renderer must not use innerHTML')}
 setAttribute(name,value){this.attributes[name]=String(value)}getAttribute(name){return this.attributes[name]}
 append(...items){this.children.push(...items)}replaceChildren(...items){this.own='';this.children=[...items]}
 addEventListener(kind,fn){(this.listeners[kind]||=[]).push(fn)}click(){if(!this.disabled)for(const fn of this.listeners.click||[])fn()}
}
class Document{createElement(tag){return new Element(tag,this)}}
const nodes=root=>[root,...root.children.flatMap(nodes)];
function harness(kind,request){const doc=new Document(),root=doc.createElement('div'),buttons=['day','week'].map(period=>({period,element:doc.createElement('button')}));return {root,buttons,view:new ReportView({kind,root,buttons,request,now:()=>Date.UTC(2026,10,4,12)})}}
const local=(period,chats=2)=>({data:{period,start:'2026-11-04T00:00:00+08:00',through:'2026-11-04T20:00:00+08:00',timezone:'UTC+08:00',local_chat_completed:chats,bili_manual_read_records:1}});
const system=(period,rows=[],monitoring={running:false})=>({data:{period,start:'2026-11-04',through:'2026-11-04',categories:rows},monitoring});
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}};
async function run(){
 {
  let calls=0;const h=harness('local',async period=>{calls++;return local(period)});assert.equal(calls,0);await h.view.load('day');assert.equal(calls,1);assert(h.root.textContent.includes('已完成本机对话'));assert(h.root.textContent.includes('2 次'));assert(h.root.textContent.includes('北京时间'));assert.equal(h.buttons[0].element.getAttribute('aria-pressed'),'true');assert.equal(h.root.getAttribute('aria-busy'),'false');assert(nodes(h.root).some(x=>x.tagName==='dl'));assert(nodes(h.root).some(x=>x.getAttribute('role')==='status'));
 }
 {
  const h=harness('system',async period=>system(period,[{category:'工作',seconds:59},{category:'创作',seconds:3660}]));await h.view.load('day');assert(h.root.textContent.includes('约 59 秒'));assert(h.root.textContent.includes('约 1 小时 1 分钟'));assert(!h.root.textContent.includes('0 分钟'));assert(h.root.textContent.includes('采样未开启'));assert(nodes(h.root).some(x=>x.tagName==='caption'));assert.equal(nodes(h.root).filter(x=>x.getAttribute('scope')==='col').length,2);
 }
 {
  const h=harness('system',async period=>system(period,[],null));await h.view.load('week');assert(h.root.textContent.includes('暂时无法确认'));assert(h.root.textContent.includes('没有已保存'));assert(h.root.textContent.includes('查看不会开启采样'));
 }
 {
  const day=deferred(),week=deferred(),signals=[];const h=harness('local',(period,signal)=>{signals.push(signal);return period==='day'?day.promise:week.promise});const old=h.view.load('day'),fresh=h.view.load('week');assert.equal(signals[0].aborted,true);week.resolve(local('week',8));await fresh;day.resolve(local('day',1));await old;assert(h.view.body.textContent.includes('本周本机互动'));assert(h.view.body.textContent.includes('8 次'));assert(!h.view.body.textContent.includes('1 次'));
 }
 {
  let fail=false;const h=harness('local',async period=>{if(fail){const e=Error('private server content');e.status=403;throw e}return local(period,7)});await h.view.load('day');fail=true;await h.view.load('day');assert(h.view.body.textContent.includes('7 次'));assert(h.view.feedback.textContent.includes('会话已失效'));assert(h.view.feedback.textContent.includes('保留上次'));assert(!h.root.textContent.includes('private server content'));
 }
 {
  const pending=deferred();const h=harness('system',()=>pending.promise);const loading=h.view.load('day');h.view.invalidate('统计已删除');pending.resolve(system('day',[{category:'工作',seconds:99}]));await loading;assert.equal(h.view.feedback.textContent,'统计已删除');assert.equal(h.view.body.textContent,'');assert.equal(h.root.getAttribute('aria-busy'),'false');
 }
 {
  let calls=0;const pending=deferred();const h=harness('local',()=>{calls++;return pending.promise});const one=h.view.load('day');await h.view.load('day');assert.equal(calls,1);pending.resolve({data:{...local('day').data,local_chat_completed:'<img src=x onerror=alert(1)>'}});await one;assert(h.view.feedback.textContent.includes('数据不完整'));assert.equal(h.view.body.textContent,'');assert(!h.root.textContent.includes('<img'));assert(h.view.feedback.textContent.includes('没有将失败显示为零'));
 }
 {
  const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),html=fs.readFileSync(path.join(__dirname,'bili_workbench/index.html'),'utf8'),source=html.match(/function createReportView\(options\)\{[^\n]+\}/)[0];const context={window:{}};vm.createContext(context);vm.runInContext(source,context);
  const doc=new Document(),root=doc.createElement('div'),buttons=[{element:doc.createElement('button')},{element:doc.createElement('button')}];const view=context.createReportView({root,buttons});assert(root.textContent.includes('组件未能加载'));assert(buttons.every(item=>item.element.disabled));view.invalidate('统计已删除。');assert(root.textContent.includes('统计已删除'));
 }
 console.log('read-only report views: 8 simulated scenarios passed');
}
module.exports={Element,Document,nodes,harness};
if(require.main===module)run().catch(error=>{console.error(error);process.exitCode=1});
