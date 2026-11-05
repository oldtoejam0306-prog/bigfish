"""Social authorization is independent of transport readiness.
Parses offline OneBot-shaped fixtures, never authenticates a QQ connection.
"""
from dataclasses import dataclass
from .runtime import Fault

from bigfish_persona import PUBLIC_PERSONA
SOCIAL_DEFAULTS={'social_mode':'account_social','friend_allowlist':[],'group_allowlist':[],'blocked_users':[],'blocked_groups':[],'groups_enabled':True,'group_reply_mode':'mentioned','per_peer_daily_limit':5,'per_peer_interval_s':30,'public_persona':PUBLIC_PERSONA}

def validate_policy(c):
    if c['social_mode'] not in ('owner_only','allowlist','account_social'):raise Fault('社交模式无效')
    if type(c['groups_enabled']) is not bool or c['group_reply_mode'] not in ('mentioned','all','off'):raise Fault('群聊策略无效')
    for key in ('friend_allowlist','group_allowlist','blocked_users','blocked_groups'):
        values=c[key]
        if not isinstance(values,list) or len(values)>1000 or any(not isinstance(x,str) or not x.isdigit() or not 0<int(x)<2**63 for x in values):raise Fault('社交名单须为正整数字符串')
    if type(c['per_peer_daily_limit']) is not int or not 0<=c['per_peer_daily_limit']<=100 or type(c['per_peer_interval_s']) is not int or not 1<=c['per_peer_interval_s']<=86400:raise Fault('会话限频无效')
    if not isinstance(c['public_persona'],str) or not c['public_persona'].strip() or len(c['public_persona'].encode())>2000:raise Fault('公开人格格式无效')

@dataclass(frozen=True)
class SocialFixture:
    bot: int
    sender: int
    message_id: int
    kind: str
    peer: int
    text: str
    owner_private: bool


def parse_fixture(event,c):
    if not isinstance(event,dict) or event.get('post_type')!='message':raise Fault('仅接受消息fixture')
    for key in ('self_id','user_id'):
        if type(event.get(key)) is not int or not 0<event[key]<2**63:raise Fault('fixture身份无效')
    bot,sender=event['self_id'],event['user_id']
    if bot!=c['bot_id'] or sender==bot:raise Fault('机器人身份不匹配或自身消息')
    mid=event.get('message_id')
    if type(mid) is not int or not -(2**31)<=mid<2**31:raise Fault('消息ID无效')
    kind=event.get('message_type')
    if kind=='private' and event.get('sub_type')=='friend':kind='friend';peer=sender
    elif kind=='group' and event.get('sub_type')=='normal':
        kind='group';peer=event.get('group_id')
        if type(peer) is not int or not 0<peer<2**63:raise Fault('群ID无效')
    else:raise Fault('不支持的会话类型')
    if str(sender) in c['blocked_users'] or (kind=='group' and str(peer) in c['blocked_groups']):raise Fault('会话已暂停社交')
    owner_private=kind=='friend' and sender==c['owner_id']
    mode=c['social_mode']
    if mode=='owner_only' and not owner_private:raise Fault('当前选择仅主人模式')
    if kind=='group':
        if not c['groups_enabled'] or c['group_reply_mode']=='off':raise Fault('群聊当前关闭')
        if mode=='allowlist' and str(peer) not in c['group_allowlist']:raise Fault('群不在社交名单')
    elif mode=='allowlist' and not owner_private and str(peer) not in c['friend_allowlist']:raise Fault('好友不在社交名单')
    segments=event.get('message')
    if not isinstance(segments,list) or not 0<len(segments)<=100:raise Fault('仅接受文本消息段')
    texts=[];mentioned=False
    for segment in segments:
        if not isinstance(segment,dict) or not isinstance(segment.get('data'),dict):raise Fault('消息段无效')
        if segment.get('type')=='text' and isinstance(segment['data'].get('text'),str):texts.append(segment['data']['text'])
        elif kind=='group' and segment.get('type')=='at':
            target=segment['data'].get('qq')
            if not isinstance(target,(str,int)) or isinstance(target,bool):raise Fault('at段无效')
            mentioned=mentioned or str(target)==str(bot)
        else:raise Fault('当前仅支持文本及群at消息段')
    if kind=='group' and c['group_reply_mode']=='mentioned' and not mentioned:raise Fault('群聊当前选择被提及时回应')
    text=''.join(texts)
    if not text.strip() or len(text.encode())>16000:raise Fault('文本为空或过长')
    return SocialFixture(bot,sender,mid,kind,peer,text,owner_private)


def validate_reply(text,*,public,conversation_style=False):
    """Conservative candidate checks, not a complete semantic safety classifier.
    No sender/payment tool exists, so rejected/accepted text never acts remotely.
    """
    import re
    if not isinstance(text,str) or not text.strip() or len(text.encode())>6000:raise Fault('回复草稿无效或过长')
    dangerous=(r'(?:我|大肥鱼)(?:已经|会|将|马上|替你|帮你|保证|承诺).{0,12}(?:转账|付款|支付|下单|购物|购买)',r'(?:替|代表)主人.{0,6}(?:保证|承诺|答应)',r'(?:我|大肥鱼)(?:已经|已)(?:帮你|为你)?(?:转账|付款|支付|下单|购买)')
    if any(re.search(pattern,text) for pattern in dangerous):raise Fault('草稿疑似承诺付款或代主人承诺，需人工检查')
    if re.search(r'我是(?:真正的|真实的)?(?:人类|真人)|我(?:拥有|有)(?:真实的)?(?:生命|意识)',text):raise Fault('草稿疑似冒充人类或声称真实生命意识，需人工检查')
    result=text if not public or conversation_style or text.startswith('【AI·大肥鱼】') else '【AI·大肥鱼】'+text
    if len(result.encode())>6000:raise Fault('加上AI身份后超过草稿长度上限')
    return result
