/* Local image messages use server-owned turn metadata, never text markers.
 * The model only receives the stored name text; PNG bytes stay on localhost. */
(function(global){
 const validId=value=>typeof value==='string'&&/^[0-9a-f]{24}$/.test(value);
 const validCharacter=value=>value==='default'||validId(value);
 const validName=value=>typeof value==='string'&&value.trim().length>0&&Array.from(value).length<=40&&!/[\x00-\x1f]/.test(value);
 class StickerChat{
  constructor({request,onChanged=()=>{},onFeedback=()=>{},onBusy=()=>{}}){
   if(typeof request!=='function')throw Error('sticker-config');
   Object.assign(this,{request,onChanged,onFeedback,onBusy});this.character=null;this.epoch=0;this.sending=false;this.readers=new Set();
  }
  setCharacter(character){
   this.character=validCharacter(character)?character:null;++this.epoch;
   for(const controller of this.readers)controller.abort();this.readers.clear();
  }
  async send(stickerId,requestId){
   if(this.sending)return;
   if(!this.character){this.onFeedback('请先重新读取当前角色的对话。');return}
   if(!validId(stickerId)||typeof requestId!=='string'||!/^[-_A-Za-z0-9]{8,64}$/.test(requestId)){this.onFeedback('表情选择无效，请重新读取列表。');return}
   const character=this.character;this.sending=true;this.onBusy(true);this.onFeedback('正在保存到本机对话…');
   try{
    const result=await this.request('/api/local-chat/sticker',{method:'POST',body:{request_id:requestId,sticker_id:stickerId,character_id:character}});
    const completed=result&&result.status==='completed'&&result.character_id===character&&Number.isSafeInteger(result.turn_id)&&result.turn_id>0&&result.model_called===false&&result.platform_sent===false;
    if(character!==this.character){this.onFeedback(completed?'表情已保存到发送时角色的本机对话，请回到该角色重新读取查看。':'表情请求已有记录或结果未确认，请回到发送时的角色核对。');return}
    if(completed){this.onFeedback('表情已保存到本机对话；未调用模型，也没有发送到 QQ/B站。');await this.onChanged()}
    else{this.onFeedback('该请求已有记录或结果未确认，请重新读取对话核对；不会自动重发。');await this.onChanged()}
   }catch(error){this.onFeedback(error&&error.status===403?'页面会话已失效，请重新打开工作台。':'表情保存结果未能确认，请重新读取发送时角色的对话核对；不会自动重发。')}
   finally{this.sending=false;this.onBusy(false)}
  }
  render(target,message,character){
   target.textContent=typeof message.content==='string'?message.content:'';
   const assistant=message.role==='assistant'&&message.expression&&message.expression.eligible===true;
   const sticker=message.role==='user'?message.sticker:assistant?message.expression.sticker:null;
   if(!Number.isSafeInteger(message.id)||message.id<=0||!sticker||!validId(sticker.id)||!validName(sticker.name)||typeof sticker.available!=='boolean'||character!==this.character)return;
   const doc=target.ownerDocument||global.document,figure=doc.createElement('span'),image=doc.createElement('img'),caption=doc.createElement('span');
   figure.className='local-chat-sticker';caption.className='local-chat-sticker-caption';image.alt=sticker.name;image.hidden=true;image.decoding='async';caption.textContent=sticker.name+(sticker.available?' · 正在读取表情…':' · 表情已删除，仅保留名称');figure.append(image,caption);if(assistant){const text=doc.createElement('span');text.className='local-assistant-text';text.textContent=message.content;target.replaceChildren(text,figure)}else target.replaceChildren(figure);
   if(!sticker.available)return;
   const epoch=this.epoch,controller=typeof global.AbortController==='function'?new global.AbortController():null;
   if(controller)this.readers.add(controller);
   const current=()=>epoch===this.epoch&&character===this.character;
   const fail=()=>{if(current()){image.hidden=true;caption.textContent=sticker.name+' · 图片暂不可用，重新读取对话可再检查。'}};
   image.addEventListener('load',()=>{if(current()){image.hidden=false;caption.textContent=sticker.name}});image.addEventListener('error',fail);
   void this.request('/api/local-chat/sticker-image/'+character+'/'+message.id,{method:'GET',signal:controller&&controller.signal}).then(result=>{
    if(!current())return;
    if(!result||result.turn_id!==message.id||result.character_id!==character||result.id!==sticker.id||result.name!==sticker.name)throw Error('sticker-metadata');
    if(result.available===false){caption.textContent=sticker.name+' · 表情已删除，仅保留名称';return}
    const png=result.png_base64;
    if(result.available!==true||typeof png!=='string'||png.length>533336||!/^iVBORw0KGgo[A-Za-z0-9+/]*={0,2}$/.test(png)||png.length%4)throw Error('sticker-image');
    image.src='data:image/png;base64,'+png;
   }).catch(fail).finally(()=>{if(controller)this.readers.delete(controller)});
  }
 }
 if(typeof module!=='undefined'&&module.exports)module.exports=StickerChat;else global.BigFishStickerChat=StickerChat;
})(globalThis);
