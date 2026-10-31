"""Offline binary ingestion boundary. No networking, persistence or trade authority.

RPC/local assertions remain assertions. Existing finalized/program-specific
validation is still required; parsed packets are never market events by themselves.
"""
import base64
import hashlib
import json
from solana_binary import parse_transaction


def ingest_binary(raw, *, source_id, policy, slot, tx_index, commitment, metadata, backend='auto'):
    envelope = {'source_id':source_id,'slot':slot,'tx_index':tx_index,'commitment':commitment,
                'raw':raw,'metadata':metadata,'status':'quarantined','reason':None,
                'execution_verified':False,'consensus_verified':False,'market_eligible':False}
    try:
        if not isinstance(raw,bytes) or not 1<=len(raw)<=1232:
            raise ValueError('Invalid bounded wire transaction')
        envelope['raw_sha256']=hashlib.sha256(raw).hexdigest()
        if not isinstance(source_id,str) or not source_id or len(source_id)>256:
            raise ValueError('Explicit source identity required')
        if not isinstance(policy,dict) or policy.get('storage_verified') is not True or policy.get('binary_ingestion_verified') is not True or not isinstance(policy.get('evidence'),str) or not policy['evidence']:
            raise ValueError('Reviewed binary source/storage provenance required')
        if type(slot) is not int or slot<0 or type(tx_index) is not int or not 0<=tx_index<100000:
            raise ValueError('Explicit slot and canonical transaction index required')
        if commitment not in ('confirmed','finalized'):
            raise ValueError('Unsupported/missing source commitment')
        if not isinstance(metadata,dict) or 'err' not in metadata or metadata['err'] is not None and not isinstance(metadata['err'],(dict,str)):
            raise ValueError('Incomplete execution status metadata')
        encoded=json.dumps(metadata,allow_nan=False)
        if len(encoded.encode())>262144: raise ValueError('Execution metadata exceeds limit')
        envelope['metadata']=json.loads(encoded)
        envelope['policy']=dict(policy)
        parsed=parse_transaction(raw,backend)
        envelope['parsed']=parsed
        count=len(parsed['account_keys'])
        loaded=metadata.get('loadedAddresses')
        lookups=parsed['address_table_lookups']
        if lookups:
            from solders.pubkey import Pubkey
            if not isinstance(loaded,dict): raise ValueError('Address lookups unresolved')
            for field,index in [('writable',1),('readonly',2)]:
                values=loaded.get(field)
                expected=sum(len(lookup[index]) for lookup in lookups)
                if not isinstance(values,list) or len(values)!=expected: raise ValueError('Loaded address count mismatch')
                for value in values: Pubkey.from_string(value)
                count+=len(values)
        for field in ('preBalances','postBalances'):
            values=metadata.get(field)
            if not isinstance(values,list) or len(values)!=count or any(type(x) is not int or x<0 or x>2**64-1 for x in values):
                raise ValueError('Incomplete execution balance metadata')
        envelope.update(status='parsed',source_reports_success=metadata['err'] is None,
                        addresses_resolved_from_source=bool(lookups),reason=None)
    except (ValueError,TypeError,RuntimeError,OverflowError) as error:
        envelope['reason']=str(error)
    return envelope


def ingest_rpc_response(response, **context):
    """Accept genuine getTransaction encoding=base64 output; never encode JSON txs."""
    if not isinstance(response,dict): raise ValueError('RPC response must be an object')
    result=response.get('result',response)
    if not isinstance(result,dict): raise ValueError('Missing RPC transaction result')
    value=result.get('transaction')
    if not isinstance(value,list) or len(value)!=2 or value[1]!='base64' or not isinstance(value[0],str) or len(value[0])>1644:
        raise ValueError('Original bounded base64 wire transaction required')
    raw=base64.b64decode(value[0],validate=True)
    return ingest_binary(raw,slot=result.get('slot'),metadata=result.get('meta'),**context)
