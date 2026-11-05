"""Offline-only WebSocket event shape adapter; never authenticates or connects.

WebSocket event frames carry no HTTP X-Signature. This module must not feed
the authenticated ingress, owner commands, background processing, or sender.
"""
import json

def parse_preview(raw, bot_id, allowed_users):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= 65536:
        raise ValueError('invalid frame')
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    try:
        event=json.loads(raw.decode('utf-8'), object_pairs_hook=no_duplicates)
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError('invalid JSON') from None
    if not isinstance(event,dict) or type(bot_id) is not int or bot_id <= 0:
        raise ValueError('invalid event or bot')
    if not isinstance(allowed_users, (set, frozenset)) or not allowed_users or any(type(x) is not int or x <= 0 for x in allowed_users):
        raise ValueError('explicit allowlist required')
    if event.get('self_id') != bot_id or type(event.get('self_id')) is not int or event.get('post_type') != 'message' or event.get('message_type') != 'private':
        raise ValueError('event outside scope')
    if type(event.get('user_id')) is not int or event['user_id'] not in allowed_users:
        raise ValueError('sender outside scope')
    if type(event.get('message_id')) not in (int,str) or not str(event['message_id']):
        raise ValueError('invalid message id')
    parts=event.get('message')
    if not isinstance(parts,list) or not 1 <= len(parts) <= 100 or any(not isinstance(x,dict) or x.get('type') != 'text' or not isinstance(x.get('data'),dict) or not isinstance(x['data'].get('text'),str) for x in parts):
        raise ValueError('unsupported message')
    if len(''.join(x['data']['text'] for x in parts).encode('utf-8')) > 16000:
        raise ValueError('message too large')
    return {'event':event, 'authenticated':False, 'dispatch_allowed':False, 'send_allowed':False}


def compatibility_preview(raw, bot_id, allowed_users, *, now, max_age_s=300):
    """Compare legacy private-text frames with the new HTTP event shape.

    This offline helper never authenticates, signs, dispatches or sends an event.
    It cannot determine whether a bridge is genuine or whether real IDs are stable.
    """
    import re
    if type(now) is not int or type(max_age_s) is not int or not 1<=max_age_s<=3600:
        raise ValueError('invalid comparison clock')
    event=parse_preview(raw,bot_id,allowed_users)['event']
    reasons=[]
    if not 0<bot_id<2**63 or not 0<event['user_id']<2**63 or event['user_id']==bot_id:
        reasons.append('identity_out_of_range')
    if event.get('sub_type')!='friend':
        reasons.append('friend_subtype_required')
    stamp=event.get('time')
    if type(stamp) is not int or abs(now-stamp)>max_age_s:
        reasons.append('valid_event_time_required')
    mid=event['message_id']
    if type(mid) is str:
        if len(mid)>11:
            reasons.append('message_id_out_of_range')
        elif re.fullmatch(r'(?:0|[1-9][0-9]*|-[1-9][0-9]*)',mid,flags=re.ASCII):
            number=int(mid)
            if str(number)==mid and -(2**31)<=number<2**31:mid=number
            else:reasons.append('message_id_out_of_range')
        else:reasons.append('message_id_not_canonical_integer')
    elif type(mid) is not int or not -(2**31)<=mid<2**31:
        reasons.append('message_id_out_of_range')
    parts=event['message'];text=''.join(x['data']['text'] for x in parts)
    if not text.strip():reasons.append('text_required')
    # The transport performs additional checks, including credential detection.
    candidate=None
    if not reasons:
        candidate={'time':stamp,'self_id':event['self_id'],'user_id':event['user_id'],
                   'post_type':'message','message_type':'private','sub_type':'friend',
                   'message_id':mid,'message':[{'type':'text','data':{'text':x['data']['text']}} for x in parts]}
    return {'shape_compatible':not reasons,'reasons':reasons,'candidate':candidate,
            'authenticated':False,'dispatch_allowed':False,'send_allowed':False,
            'actual_bridge_compatibility_verified':False,
            'note':'仅比较私聊文本字段；不能代替传输认证、密钥检查或真实桥验收'}
