"""Read-only observer RPC result/error contract; no network or trading authority."""
from data_policy import SourceAccessBlocked
from wallet_watch import PublicRpcError


class RpcEvidenceError(SourceAccessBlocked):
    category='malformed_evidence'


class RpcHistoryUnavailable(ConnectionError):
    category='history_unavailable'
    def __init__(self,source_id,code=None):
        self.source_id=source_id;self.rpc_code=code
        super().__init__('Provider cannot supply required finalized history; checkpoint preserved')


class RpcRemoteError(PublicRpcError):
    def __init__(self,code,source_id):
        super().__init__(code)
        self.source_id=source_id
        self.category='throttled' if code in (429,-32005) else 'remote_rpc_error'


def http_error(status,source_id):
    kind=SourceAccessBlocked if status in (401,403) else ConnectionError if status in (500,502,503,504) else ValueError
    error=kind('RPC HTTP error '+str(status))
    error.http_status=status;error.source_id=source_id
    error.category='authorization_denied' if status in (401,403) else 'throttled' if status==429 else 'provider_unavailable' if kind is ConnectionError else 'http_error'
    return error


def response_value(method,value,source_id):
    if method in ('getSlot','getFirstAvailableBlock','minimumLedgerSlot'):
        if type(value) is not int or not 0<=value<2**63:raise RpcEvidenceError('Invalid slot response')
    elif method=='getBlocks':
        if not isinstance(value,list) or any(type(slot) is not int or not 0<=slot<2**63 for slot in value) or value!=sorted(set(value)):
            raise RpcEvidenceError('Invalid produced-slot response')
    elif method=='getBlock':
        if value is None:raise RpcHistoryUnavailable(source_id)
        if not isinstance(value,dict) or not isinstance(value.get('transactions'),list):raise RpcEvidenceError('Invalid block response')
    elif method=='getGenesisHash':
        if not isinstance(value,str) or not value:raise RpcEvidenceError('Invalid genesis response')
    elif method=='getBlockTime':
        if value is not None and (type(value) is not int or value<0):raise RpcEvidenceError('Invalid block time')
    elif method=='getAccountInfo':
        if not isinstance(value,dict) or 'value' not in value or value['value'] is not None and not isinstance(value['value'],dict):raise RpcEvidenceError('Invalid account response')
    elif method=='getTransaction':
        if value is not None and not isinstance(value,dict):raise RpcEvidenceError('Invalid transaction response')
    elif method=='getSignaturesForAddress':
        if not isinstance(value,list) or any(not isinstance(item,dict) for item in value):raise RpcEvidenceError('Invalid signature-history response')
    elif method=='getHealth':
        if value!='ok':raise RpcEvidenceError('Invalid health response')
    elif method=='getVersion':
        if not isinstance(value,dict) or not isinstance(value.get('solana-core'),str):raise RpcEvidenceError('Invalid version response')
    return value


def remote_error(code,source_id):
    if code in (-32001,-32004,-32009,-32011,-32014):return RpcHistoryUnavailable(source_id,code)
    if code in (-32600,-32602,-32015):return RpcEvidenceError('Unsupported or invalid RPC request/evidence; operator review required')
    return RpcRemoteError(code,source_id)


def rpc_result(text,method,source_id,budget,url):
    """Identical strict JSON-RPC contract for Python and Rust response bodies."""
    import json
    def invalid_constant(_):raise RpcEvidenceError('Nonfinite JSON value')
    def object_pairs(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise RpcEvidenceError('Duplicate JSON object key')
            result[key]=value
        return result
    try:
        payload=json.loads(text,object_pairs_hook=object_pairs,parse_constant=invalid_constant)
        if not isinstance(payload,dict) or payload.get('jsonrpc')!='2.0' or type(payload.get('id')) is not int or payload['id']!=1:
            raise RpcEvidenceError('Invalid JSON-RPC identity/version')
        if ('result' in payload)==('error' in payload):raise RpcEvidenceError('Ambiguous JSON-RPC envelope')
        if 'error' in payload:
            error=payload['error']
            if not isinstance(error,dict) or type(error.get('code')) is not int:raise RpcEvidenceError('Invalid JSON-RPC error')
        else:
            value=response_value(method,payload['result'],source_id)
            budget.success(url)
            return value
    except RpcEvidenceError:raise
    except (ValueError,TypeError,RecursionError,UnicodeError):
        raise RpcEvidenceError('Malformed JSON-RPC response') from None
    code=payload['error']['code']
    if code in (401,403):
        budget.failure(url,code)
        raise SourceAccessBlocked('RPC source access denied')
    if code in (429,-32005):budget.failure(url,429 if code==429 else 503)
    raise remote_error(code,source_id)
