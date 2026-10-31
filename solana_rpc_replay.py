"""Offline RPC-to-binary summary reference; no transport/store or trust claims."""
import base64
import json
from solana_binary import parse_reference


def replay_binary_response(text):
    if len(text.encode())>64000000: raise ValueError('oversized replay')
    value=json.loads(text)
    if not isinstance(value,dict) or value.get('jsonrpc')!='2.0' or type(value.get('id')) is not int or value['id']!=1 or 'error' in value:
        raise ValueError('invalid replay envelope')
    result=value.get('result')
    if not isinstance(result,dict): raise ValueError('missing replay result')
    if isinstance(result.get('transactions'),list): items=result['transactions']
    elif 'transaction' in result: items=[result]
    else: raise ValueError('no replay transactions')
    if len(items)>100000: raise ValueError('too many replay transactions')
    rows=[]
    for index,item in enumerate(items):
        if not isinstance(item,dict): rows.append((index,'quarantined',None,None)); continue
        tx=item.get('transaction')
        if not isinstance(tx,list) or len(tx)!=2 or tx[1]!='base64':
            rows.append((index,'unsupported_json',None,None)); continue
        try:
            if not isinstance(tx[0],str) or len(tx[0])>1644: raise ValueError('bad packet')
            parsed=parse_reference(base64.b64decode(tx[0],validate=True))
            meta=item.get('meta')
            if not isinstance(meta,dict) or 'err' not in meta or meta['err'] is not None and not isinstance(meta['err'],(dict,str)):
                raise ValueError('missing execution status')
            rows.append((index,'parsed_unverified',meta['err'] is None,parsed[0]))
        except (ValueError,TypeError): rows.append((index,'quarantined',None,None))
    return rows
