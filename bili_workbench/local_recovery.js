/* Recovery stores only opaque operation metadata, never chat, memory or keys.
 * GET checks never resubmit a model request or a memory write. */
(function(global){
 const KEY='bigfish-local-pending-v1',states=new Set(['not_found','running','completed','blocked','uncertain']);
 const character=value=>value==='default'||typeof value==='string'&&/^[0-9a-f]{24}$/.test(value);
 const valid=item=>item&&character(item.character_id)&&['chat','memory'].includes(item.kind)&&typeof item.request_id==='string'&&/^[A-Za-z0-9_-]{8,64}$/.test(item.request_id)&&(item.kind!=='memory'||item.character_id==='default');
 const key=item=>item.character_id+'/'+item.kind+'/'+item.request_id;
 const labels={not_found:'尚未找到受理记录；不能据此判断可重发',running:'仍在处理，请等待后再次检查',completed:'已完成',blocked:'已阻止或未完成',uncertain:'结果不确定，需要核对原结果'};
 class RecoveryView{
  constructor({root,request,storage,newId=()=>global.crypto.randomUUID(),confirm=message=>global.confirm(message),onChange=()=>{},onResolved=()=>{}}){
   if(!root||typeof request!=='function'||!storage)throw Error('recovery-config');
   Object.assign(this,{root,request,storage,newId,confirm,onChange,onResolved});this.character=null;this.epoch=0;this.loading=false;this.ready=false;this.pending=new Map();this.resolved=new Map();this.inflight=new Set();this.storageBroken=false;
   const saved=JSON.parse(storage.getItem(KEY)||'[]');
   if(!Array.isArray(saved)||saved.length>100||saved.some(item=>!valid(item)||Object.keys(item).some(k=>!['kind','request_id','character_id'].includes(k))))throw Error('recovery-storage');
   for(const item of saved)this.pending.set(key(item),{...item,state:'not_found'});
   this.document=root.ownerDocument||global.document;this.feedback=this.element('p','重新读取对话后检查上次结果。');this.feedback.setAttribute('role','status');this.feedback.setAttribute('aria-live','polite');this.list=this.element('ul');this.refresh=this.element('button','重新检查上次结果');this.refresh.type='button';this.refresh.addEventListener('click',()=>void this.refreshStatus());root.classList.add('local-recovery');root.replaceChildren(this.feedback,this.list,this.refresh);this.render();
  }
  element(tag,text){const node=this.document.createElement(tag);if(text!==undefined)node.textContent=text;return node}
  save(){
   try{this.storage.setItem(KEY,JSON.stringify(Array.from(this.pending.values(),item=>({kind:item.kind,request_id:item.request_id,character_id:item.character_id}))));this.storageBroken=false;return true}
   catch(error){this.storageBroken=true;this.feedback.textContent='无法保留恢复编号，未开始新操作；请检查本机页面存储后重新检查。';return false}
  }
  canStart(kind){return this.ready&&!this.loading&&!this.storageBroken&&this.pending.size<100&&character(this.character)&&(kind!=='memory'||this.character==='default')&&!Array.from(this.pending.values()).some(item=>item.character_id===this.character&&item.kind===kind)}
  setCharacter(value){this.character=character(value)?value:null;++this.epoch;this.loading=false;this.ready=false;this.render();if(this.character)void this.refreshStatus()}
  render(){
   this.list.replaceChildren();this.refresh.disabled=!this.character||this.loading;this.root.setAttribute('aria-busy',String(this.loading));
   for(const item of this.pending.values()){
    if(item.character_id!==this.character)continue;
    const row=this.element('li'),label=this.element('p',(item.kind==='chat'?'对话':'记忆保存')+'：'+labels[item.state]),id=this.element('code',item.billing_request_id||item.request_id),check=this.element('button','检查结果');check.type='button';check.disabled=this.loading;check.addEventListener('click',()=>void this.check(item));row.append(label,id,check);
    if(item.state==='uncertain'&&!this.inflight.has(key(item))){const ack=this.element('button',item.kind==='chat'?'我已核对结果和账单':'我已核对记忆结果');ack.type='button';ack.disabled=this.loading;ack.addEventListener('click',()=>void this.acknowledge(item));row.append(ack)}
    this.list.append(row);
   }
   if(this.list.children.length&&this.root.parentElement&&this.root.parentElement.tagName==='DETAILS')this.root.parentElement.open=true;
   this.onChange();
  }
  statusResult(result,item){
   if(!valid(result)||key(result)!==key(item)||!states.has(result.state)||typeof result.reviewed!=='boolean'||result.automatic_retry!==false||result.operation_replayed!==false)throw Error('recovery-schema');
   return result;
  }
  async settle(item,status){
   const ident=key(item);if(!this.pending.has(ident))return false;
   this.pending.delete(ident);this.resolved.set(ident,status);if(this.resolved.size>100)this.resolved.delete(this.resolved.keys().next().value);this.save();this.render();await this.onResolved(item.kind,status);return true;
  }
  async check(item,{notify=true,finalRejection=false}={}){
   const epoch=this.epoch;if(item.character_id!==this.character)return;
   try{
    const value=this.statusResult(await this.request('/api/local-chat/recovery/'+item.character_id+'/'+item.kind+'/'+item.request_id,{method:'GET'}),item);
    if(epoch!==this.epoch)return;
    if(value.reviewed||['completed','blocked'].includes(value.state)||(value.state==='not_found'&&finalRejection)){
     await this.settle(item,value.reviewed?'reviewed':value.state);
     if(notify)this.feedback.textContent=value.state==='completed'?'上次操作已完成；没有重新执行。':value.reviewed?'已结束此次核对；账本和原操作状态未改变。':'本次未完成；输入内容保留，没有重新执行。';
    }else{this.pending.set(key(item),{...item,...value});this.save();if(notify)this.feedback.textContent=labels[value.state]+'。没有自动重发。'}
    this.render();return value;
   }catch(error){if(epoch===this.epoch){if(notify)this.feedback.textContent=error&&error.status===403?'本机页面会话已失效，请重新打开工作台。':'暂时无法核对，请保留编号后再次检查；没有自动重发。';this.render()}}
  }
  async refreshStatus(){
   if(!this.character||this.loading)return;
   const epoch=this.epoch,current=this.character;this.loading=true;this.ready=false;this.feedback.textContent='正在读取已有请求状态，不执行原操作…';this.render();
   try{
    const result=await this.request('/api/local-chat/recovery/'+current+'/pending',{method:'GET'});
    if(epoch!==this.epoch)return;
    if(!result||result.character_id!==current||!Array.isArray(result.items)||result.items.length>20||typeof result.has_more!=='boolean'||result.automatic_retry!==false)throw Error('recovery-schema');
    for(const item of result.items){this.statusResult(item,item);if(item.character_id!==current)throw Error('recovery-scope');this.pending.set(key(item),item)}
    if(this.pending.size>100)throw Error('recovery-capacity');
    this.save();this.ready=true;
    // Reopened tabs know only IDs; one read checks each saved operation.
    const locals=Array.from(this.pending.values()).filter(item=>item.character_id===current).slice(0,20);
    for(const item of locals){await this.check(item,{notify:false});if(epoch!==this.epoch)return}
    const count=Array.from(this.pending.values()).filter(item=>item.character_id===current).length;
    this.feedback.textContent=count?'有 '+count+' 笔操作需核对；检查结果不会重发请求，也不会修改账本。'+(result.has_more?'还有其他待核对记录，处理本批后可再次检查。':''):'没有待核对的操作；检查未调用模型。';
   }catch(error){if(epoch===this.epoch){this.ready=false;this.feedback.textContent=error&&error.status===403?'本机页面会话已失效，请重新打开工作台。':'上次结果暂时无法确认，请再次检查；新操作暂未开放。'}}
   finally{if(epoch===this.epoch){this.loading=false;this.render()}}
  }
  async perform(kind,payload){
   if(!this.canStart(kind))return {outcome:'blocked'};
   const item={kind,request_id:this.newId(),character_id:this.character,state:'running'};
   if(!valid(item))throw Error('recovery-request-id');
   this.pending.set(key(item),item);
   if(!this.save()){this.pending.delete(key(item));this.render();return {outcome:'blocked'}}
   this.inflight.add(key(item));this.feedback.textContent='正在处理，请勿重复提交…';this.render();
   try{
    const path=kind==='chat'?'/api/local-chat/send':'/api/local-chat/remember-turn';
    const result=await this.request(path,{method:'POST',body:{...payload,request_id:item.request_id,character_id:item.character_id}});
    if(!this.pending.has(key(item)))return {outcome:this.resolved.get(key(item))==='completed'?'recovered':'already_resolved',character_id:item.character_id};
    if(result&&result.status==='completed'&&(kind==='chat'?typeof result.reply==='string':Number.isSafeInteger(result.memory_id)&&result.memory_id>0)){
     const current=item.character_id===this.character;
     await this.settle(item,'completed');
     if(current)this.feedback.textContent='操作已完成并保存到本机。';
     return {outcome:'completed',result,character_id:item.character_id};
    }
    const status=await this.check(item);return {outcome:status&&status.state==='completed'?'recovered':'pending',character_id:item.character_id};
   }catch(error){const status=await this.check(item,{finalRejection:error&&error.status===400});return {outcome:status&&status.state==='completed'?'recovered':'pending',character_id:item.character_id}}
   finally{this.inflight.delete(key(item));this.render()}
  }
  async acknowledge(item){
   if(item.character_id!==this.character||item.state!=='uncertain'||this.inflight.has(key(item)))return;
   const isChat=item.kind==='chat',confirmation=isChat?'已核对本次对话结果和账单':'已核对本次记忆保存结果';
   if(!this.confirm(isChat?'请先核对对话记录与提供方实际账单。确认只结束此处核对，不会修正账本或重新发送。':'请先在记忆管理中核对保存结果。确认只结束此处核对，不会再次保存。'))return;
   this.inflight.add(key(item));this.render();
   try{
    const result=await this.request('/api/local-chat/recovery/acknowledge',{method:'POST',body:{kind:item.kind,request_id:item.request_id,character_id:item.character_id,confirmation}});
    if(!result||result.acknowledged!==true||result.kind!==item.kind||result.request_id!==item.request_id||result.character_id!==item.character_id||result.billing_changed!==false||result.operation_replayed!==false)throw Error('recovery-schema');
    await this.settle(item,'reviewed');this.feedback.textContent='已结束此次核对；原操作和账本未被修改。';
   }catch(error){await this.check(item)}finally{this.inflight.delete(key(item));this.render()}
  }
 }
 if(typeof module!=='undefined'&&module.exports)module.exports=RecoveryView;else global.BigFishLocalRecovery=RecoveryView;
})(globalThis);
