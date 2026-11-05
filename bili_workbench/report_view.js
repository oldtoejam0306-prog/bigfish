/* Read-only local reports. No request is made until a period is selected.
 * Rendering uses text nodes only; unexpected fields are never displayed. */
(function(global){
 const categories=new Set(['浏览与交流','工作','创作','其他','无法识别']);
 const periods={day:'今日',week:'本周'};
 const number=value=>new Intl.NumberFormat('zh-CN').format(value);
 const count=value=>Number.isSafeInteger(value)&&value>=0;
 function duration(seconds){
  if(!count(seconds))throw Error('report-schema');
  if(seconds<60)return (seconds?'约 ':'')+number(seconds)+' 秒';
  const hours=Math.floor(seconds/3600),minutes=Math.floor(seconds%3600/60),rest=seconds%60;
  const parts=[];if(hours)parts.push(number(hours)+' 小时');if(minutes)parts.push(number(minutes)+' 分钟');if(!hours&&rest)parts.push(number(rest)+' 秒');return '约 '+parts.join(' ');
 }
 function date(value){
  if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}(?:T[^\s]{1,45})?$/.test(value)||!Number.isFinite(Date.parse(value)))throw Error('report-schema');
  const options={timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'};
  if(value.includes('T'))Object.assign(options,{hour:'2-digit',minute:'2-digit',hour12:false});
  return new Intl.DateTimeFormat('zh-CN',options).format(new Date(value));
 }
 function snapshot(kind,result,period){
  const data=result&&result.data;
  if(!data||data.period!==period)throw Error('report-schema');
  const range=date(data.start)+' 至 '+date(data.through)+'（北京时间）';
  if(kind==='local'){
   if(data.timezone!=='UTC+08:00'||!count(data.local_chat_completed)||!count(data.bili_manual_read_records))throw Error('report-schema');
   return {period,range,chats:data.local_chat_completed,reads:data.bili_manual_read_records};
  }
  if(!Array.isArray(data.categories)||data.categories.length>categories.size)throw Error('report-schema');
  const seen=new Set(),rows=data.categories.map(row=>{
   if(!row||!categories.has(row.category)||seen.has(row.category)||!count(row.seconds))throw Error('report-schema');
   seen.add(row.category);return {category:row.category,seconds:row.seconds};
  });
  let sampling=null;const status=result.monitoring;
  if(status&&typeof status.running==='boolean'){
   if(!status.running)sampling=false;
   else if(status.options&&typeof status.options.activity==='boolean')sampling=status.options.activity;
  }
  return {period,range,rows,sampling};
 }
 class ReportView{
  constructor({kind,root,buttons,request,now=Date.now}){
   if(!['local','system'].includes(kind)||!root||typeof request!=='function'||!Array.isArray(buttons)||buttons.length!==2)throw Error('报告配置无效');
   Object.assign(this,{kind,root,buttons,request,now});this.epoch=0;this.selected=null;this.pending=false;this.abort=null;this.cache=new Map();
   this.document=root.ownerDocument||global.document;root.classList.add('report-view');root.setAttribute('aria-busy','false');
   this.feedback=this.element('p','选择今天或本周，读取本机已有记录。','report-feedback');this.feedback.setAttribute('role','status');this.feedback.setAttribute('aria-live','polite');
   this.body=this.element('div');this.refresh=this.element('button','重新读取');this.refresh.type='button';this.refresh.hidden=true;
   this.refresh.addEventListener('click',()=>{if(this.selected)void this.load(this.selected)});root.replaceChildren(this.feedback,this.body,this.refresh);
   for(const item of buttons){
    if(!periods[item.period]||!item.element)throw Error('报告周期配置无效');
    item.element.setAttribute('aria-pressed','false');item.element.addEventListener('click',()=>void this.load(item.period));
   }
  }
  element(tag,text,className){const node=this.document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node}
  render(data){
   const nodes=[this.element('h3',periods[data.period]+(this.kind==='local'?'本机互动':'电脑活动统计')),this.element('p',data.range,'report-meta')];
   if(this.kind==='local'){
    const list=this.element('dl',undefined,'report-stats');
    for(const [label,value] of [['已完成本机对话',data.chats],['新增 B站读取记录',data.reads]]){
     const row=this.element('div');row.append(this.element('dt',label),this.element('dd',number(value)+(label.includes('对话')?' 次':' 条')));list.append(row);
    }
    nodes.push(list);
    if(!data.chats&&!data.reads)nodes.push(this.element('p','此周期还没有已完成的本机对话或新增读取记录。可以先在上方聊天，或到 B站阅读页读取视频。'));
    nodes.push(this.element('p','对话按本机各角色的已完成请求计数；B站仅统计当前配置账号的新增读取记录，不代表观看次数或时长。查看报告不会调用模型。','report-meta'));
   }else{
    nodes.push(this.element('p',data.sampling===true?'本次读取时，电脑活动采样已开启。':data.sampling===false?'电脑活动采样未开启；以下仅包含已保存的统计。':'采样状态暂时无法确认；以下仅包含已保存的统计。','report-meta'));
    if(data.rows.length){
     const table=this.element('table',undefined,'report-table');table.append(this.element('caption','前台程序粗分类与估计时长'));
     const head=this.element('thead'),heading=this.element('tr');for(const label of ['分类','估计时长']){const cell=this.element('th',label);cell.setAttribute('scope','col');heading.append(cell)}head.append(heading);table.append(head);
     const body=this.element('tbody');for(const row of data.rows){const tr=this.element('tr');tr.append(this.element('td',row.category),this.element('td',duration(row.seconds)));body.append(tr)}table.append(body);nodes.push(table);
    }else nodes.push(this.element('p','此周期没有已保存的电脑活动统计。若需要记录，请在“电脑活动与主动互动”中自行选择并开启活动采样。'));
    nodes.push(this.element('p','只统计获准采样期间保存的粗分类；估计值不代表实际工作或在线时长。查看不会开启采样，也不会读取窗口、按键或屏幕。','report-meta'));
   }
   this.body.replaceChildren(...nodes);
  }
  async load(period){
   if(!periods[period]||(this.pending&&this.selected===period))return;
   if(this.abort)this.abort.abort();this.abort=typeof global.AbortController==='function'?new global.AbortController():null;
   const epoch=++this.epoch;this.selected=period;this.pending=true;this.refresh.hidden=false;this.refresh.disabled=true;this.root.setAttribute('aria-busy','true');
   for(const item of this.buttons)item.element.setAttribute('aria-pressed',String(item.period===period));
   const cached=this.cache.get(period);if(cached)this.render(cached.data);else this.body.replaceChildren();
   this.feedback.textContent='正在读取'+periods[period]+'报告…'+(cached?'仍展示上次成功读取的内容。':'');
   try{
    const result=await this.request(period,this.abort&&this.abort.signal);
    if(epoch!==this.epoch)return;
    const data=snapshot(this.kind,result,period),stamp=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(this.now()));
    this.cache.set(period,{data,stamp});this.render(data);this.feedback.textContent=periods[period]+'报告已读取 · '+stamp+'（北京时间）';
   }catch(error){
    if(epoch!==this.epoch)return;
    const recovery=error&&error.status===403?'页面会话已失效，请重新打开工作台。':error&&error.message==='report-schema'?'报告数据不完整，请检查服务版本后重新读取。':'未能取得本机报告，请检查服务后重新读取。';
    this.feedback.textContent=periods[period]+'报告读取未完成。'+recovery+(cached?' 保留上次 '+cached.stamp+' 读取的内容。':' 没有将失败显示为零记录。');
   }finally{if(epoch===this.epoch){this.pending=false;this.refresh.disabled=false;this.root.setAttribute('aria-busy','false')}}
  }
  invalidate(message='统计已变化，请重新读取。'){
   ++this.epoch;if(this.abort)this.abort.abort();this.pending=false;this.cache.clear();this.body.replaceChildren();this.feedback.textContent=message;this.refresh.disabled=false;this.root.setAttribute('aria-busy','false');
  }
 }
 ReportView.duration=duration;
 if(typeof module!=='undefined'&&module.exports)module.exports=ReportView;else global.BigFishReportView=ReportView;
})(globalThis);
