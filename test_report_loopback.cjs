const assert=require('node:assert/strict'),vm=require('node:vm'),{Document}=require('./test_report_view.cjs');
const base=process.argv[2],token=process.argv[3],calls=[];
async function get(path,signal){calls.push(path);const response=await fetch(base+path,{headers:{'X-Local-Token':token},signal});if(!response.ok){const error=Error('report-request');error.status=response.status;throw error}return response.json()}
(async()=>{
 const response=await fetch(base+'/report-view.js');assert.equal(response.headers.get('cache-control'),'no-store');assert(response.headers.get('content-type').includes('javascript'));
 const context={Intl,Date,Map,Set,Number,Error,AbortController};context.globalThis=context;vm.createContext(context);vm.runInContext(await response.text(),context);
 const doc=new Document(),make=(kind,request)=>{const root=doc.createElement('div'),buttons=['day','week'].map(period=>({period,element:doc.createElement('button')}));return {root,view:new context.BigFishReportView({kind,root,buttons,request})}};
 const local=make('local',async(period,signal)=>({data:await get('/api/activity-report/'+period,signal)}));
 const system=make('system',async(period,signal)=>{const result=await Promise.all([get('/api/system-activity/'+period,signal),get('/api/system-activity/status',signal)]);return {data:result[0],monitoring:result[1]}});
 assert.equal(calls.length,0);await local.view.load('day');assert(local.root.textContent.includes('2 次'));assert(local.root.textContent.includes('1 条'));await system.view.load('day');assert(system.root.textContent.includes('约 59 秒'));assert(system.root.textContent.includes('采样未开启'));assert(calls.every(path=>!path.includes('/start')&&!path.includes('/model')));
 const page=await(await fetch(base+'/')).text();assert(page.includes('<script src="/report-view.js"></script>'));assert(page.includes('const localReportView=createReportView'));assert(page.includes('const systemReportView=createReportView'));console.log('report panels to actual local GET endpoints passed');
})().catch(error=>{console.error(error);process.exitCode=1});
