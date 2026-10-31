"""Experimental Rust transport behind established Python ingestion contracts.

Default/auto select Python. No signing, networking outside the approved boundary,
backend fallback after evidence errors, native program decoding or native storage.
"""
import asyncio
import json
from data_policy import SourceAccessBlocked, require_permitted_url
from onchain_ingest import IndexRpc, READ_METHODS
from wallet_watch import PublicRpcError


def native_module():
    try:
        import _solana_binary as native
        if not hasattr(native,'NativeHttp'): raise ImportError
        return native
    except ImportError:
        raise RuntimeError('Build the optional rpc-engine extension') from None


def check_boundary(endpoint):
    import os
    from solana_network_budget import Budget
    policy=endpoint.get('policy',{})
    if policy.get('storage_verified') is not True or not policy.get('evidence'):
        raise SourceAccessBlocked('Reviewed source storage policy required')
    import time
    from pathlib import Path
    path=Path(endpoint.get('network_budget',{}).get('status_file','/run/solana-budget-status.json'))
    try:
        stat=path.stat()
        if path.is_symlink() or stat.st_uid!=0 or stat.st_mode & 0o022 or time.time()-stat.st_mtime>5:
            raise ValueError('Untrusted/stale session status')
        with path.open('rb') as stream: raw=stream.read(4097)
        if len(raw)>4096: raise ValueError('Oversized session status')
        status=json.loads(raw)
        inode=status['namespace_inode']
        if type(inode) is not int or inode<=0 or os.stat('/proc/self/ns/net').st_ino!=inode:
            raise ValueError('Namespace mismatch')
        if type(status['expires']) not in (int,float) or not __import__('math').isfinite(status['expires']) or not time.time()<status['expires']<=time.time()+60:
            raise ValueError('Expired session')
        if status['optional_allowed'] is not True: raise ValueError('Normal allocation exhausted')
        if endpoint.get('approved_namespace_inode',inode)!=inode: raise ValueError('Configured namespace mismatch')
    except Exception:
        raise SourceAccessBlocked('Network session unavailable or unauthorized; no native connection') from None
    require_permitted_url(endpoint['url'])
    return inode



class RustIndexRpc(IndexRpc):
    def __init__(self,endpoint,budget=None):
        super().__init__(endpoint,budget)
        self.endpoint=endpoint
        self._transport=None

    async def call_async(self,method,params):
        if method not in READ_METHODS: raise SourceAccessBlocked('Read-only RPC methods only')
        inode=check_boundary(self.endpoint)
        await asyncio.to_thread(self.budget.reserve,self.url,self.interval)
        if self._transport is None:
            native=native_module()
            self._transport=native.NativeHttp(self.url,inode,self.endpoint.get('allow_loopback_http',False))
        self.requests+=1
        try:
            status,retry,text=await self._transport.request(method,json.dumps(params,allow_nan=False))
        except ConnectionError:
            self.budget.failure(self.url,503)
            raise
        except ValueError:
            raise SourceAccessBlocked('Malformed native transport evidence; no backend retry') from None
        if status!=200:
            self.budget.failure(self.url,status,{'Retry-After':retry} if retry else {})
            if status in (401,403): raise SourceAccessBlocked('RPC access denied')
            if status in (500,502,503,504): raise ConnectionError('RPC provider unavailable')
            raise ValueError('RPC HTTP error '+str(status))
        self.wire_bytes+=len(text.encode()); self.decoded_bytes+=len(text.encode())
        try:
            result=json.loads(text)
            if not isinstance(result,dict) or result.get('jsonrpc')!='2.0' or type(result.get('id')) is not int or result['id']!=1:
                raise ValueError('Invalid RPC envelope')
            if ('result' in result)==('error' in result): raise ValueError('Ambiguous RPC result')
            if 'error' in result:
                error=result['error']
                if not isinstance(error,dict) or type(error.get('code')) is not int: raise ValueError('Invalid RPC error')
            else:
                self.budget.success(self.url)
                return result['result']
        except (ValueError,TypeError):
            raise SourceAccessBlocked('Malformed RPC evidence; no backend retry') from None
        code=result['error']['code']
        if code in (401,403):
            self.budget.failure(self.url,code)
            raise SourceAccessBlocked('RPC source access denied')
        if code in (429,-32005): self.budget.failure(self.url,429 if code==429 else 503)
        raise PublicRpcError(code)

    def call(self,method,params):
        try: asyncio.get_running_loop()
        except RuntimeError: return asyncio.run(self.call_async(method,params))
        raise RuntimeError('Use await call_async() inside an asyncio loop')

    def decode_binary(self,response,*,tx_index,commitment):
        from solana_binary_ingest import ingest_rpc_response
        return ingest_rpc_response(response,source_id=self.source,policy=self.endpoint['policy'],
                                   tx_index=tx_index,commitment=commitment,backend='rust')


def make_rpc(endpoint,budget=None):
    backend=endpoint.get('backend','python')
    if backend in ('python','auto'): result=IndexRpc(endpoint,budget)
    elif backend=='rust': result=RustIndexRpc(endpoint,budget)
    else: raise ValueError('Unknown RPC backend')
    result.event_backend=endpoint.get('event_backend','python')
    if result.event_backend not in ('python','rust','compare'): raise ValueError('Unknown event backend')
    return result


def stream_connector(endpoint,python_connector):
    backend=endpoint.get('ws_backend',endpoint.get('backend','python'))
    if backend in ('python','auto'): return python_connector
    if backend!='rust': raise ValueError('Unknown WS backend')
    class Connector:
        def __init__(self,url,**_): self.url=url; self.socket=None
        async def __aenter__(self):
            inode=check_boundary(endpoint)
            require_permitted_url(self.url)
            try:
                self.socket=await native_module().connect_ws(self.url,inode,endpoint.get('allow_loopback_ws',False))
            except ValueError as error:
                try:
                    detail=json.loads(str(error)); status=detail.get('ws_http_status')
                    if type(status) is int:
                        from provider_limits import BUDGET
                        BUDGET.failure(self.url,status,{'Retry-After':detail.get('retry_after','')})
                except (ValueError,TypeError,AttributeError): pass
                raise SourceAccessBlocked('Native WebSocket handshake denied; review source before retry') from None
            return self
        async def __aexit__(self,*_):
            if self.socket is not None: await self.socket.close()
        async def send(self,text):
            check_boundary(endpoint)
            try: await self.socket.send(text)
            except ValueError: raise SourceAccessBlocked('Invalid native subscription evidence') from None
        async def recv(self):
            check_boundary(endpoint)
            try: return await self.socket.recv()
            except ValueError: raise SourceAccessBlocked('Invalid native stream evidence') from None
    return Connector


def extract_event_batch(transactions,*,slot,block_time,source_id,backend='python'):
    """Compact unverified projection; receipt/version checks remain Ingester-owned."""
    from onchain_decode import Decoder
    if not isinstance(transactions,list) or len(transactions)>64: raise ValueError('Event batch exceeds 64 transactions')
    if type(slot) is not int or slot<0 or type(block_time) is not int or not isinstance(source_id,str) or not source_id:
        raise ValueError('Explicit batch context required')
    decoder=Decoder(backend=backend)
    events=[];issues=[]
    for index,tx in enumerate(transactions):
        try: found,errors=decoder.transaction(tx,slot,index,block_time)
        except (ValueError,KeyError,TypeError,IndexError): found,errors=[],['malformed transaction evidence']
        # A transaction with any ambiguous evidence contributes no compact events.
        if errors: issues.append({'tx_index':index,'errors':errors})
        else: events.extend(found)
    columns=sorted({key for event in events for key in event})
    return {'columns':columns,'rows':[[event.get(key) for key in columns] for event in events],
            'presence':[[i for i,key in enumerate(columns) if key in event] for event in events],
            'issues':issues,'source_id':source_id,'market_eligible':False,'execution_verified':False,
            'requires_existing_ingester_validation':True}
