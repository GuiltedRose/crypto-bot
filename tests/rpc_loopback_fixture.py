"""Bounded local HTTP fixture shared by unexecuted socket tests/benchmarks.

Construction starts nothing; context entry binds only 127.0.0.1. No external I/O.
"""
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from wallet_watch import MAINNET

RESULTS={'getGenesisHash':MAINNET, 'getSlot':7,'getBlocks':[5,6,7],
         'getBlock':{'transactions':[],'parentSlot':6},'getAccountInfo':{'value':None},
         'getFirstAvailableBlock':0,'minimumLedgerSlot':0,'getHealth':'ok','getVersion':{'solana-core':'fixture'},
         'getBlockTime':1000,'getSignaturesForAddress':[],'getTransaction':None}


class LocalRpcServer:
    def __init__(self):
        self.lock=threading.Lock();self.requests=[];self.connections=0;self.sockets=set();self.script=[]
    def __enter__(self):
        owner=self
        class Server(ThreadingHTTPServer):
            daemon_threads=True
            def get_request(self):
                connection,address=super().get_request();connection.settimeout(3)
                with owner.lock:owner.connections+=1;owner.sockets.add(connection)
                return connection,address
            def shutdown_request(self,request):
                with owner.lock:owner.sockets.discard(request)
                super().shutdown_request(request)
            def handle_error(self,*_):pass # fixture fault scripts deliberately close connections
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def log_message(self,*_):pass
            def do_POST(self):
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=65536:self.send_error(413);return
                value=json.loads(self.rfile.read(size))
                with owner.lock:
                    owner.requests.append(value)
                    step=owner.script.pop(0) if owner.script else {}
                if step.get('disconnect'):
                    self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
                payload=step.get('body',json.dumps({'jsonrpc':'2.0','id':1,'result':RESULTS.get(value['method'])}))
                body=payload.encode() if isinstance(payload,str) else payload
                self.send_response(step.get('status',200))
                self.send_header('Retry-After','60')
                self.send_header('Content-Type','application/json')
                if step.get('encoding'):self.send_header('Content-Encoding',step['encoding'])
                if step.get('chunked'):self.send_header('Transfer-Encoding','chunked')
                else:self.send_header('Content-Length',str(step.get('declared_length',len(body))))
                if step.get('redirect'):self.send_header('Location','http://127.0.0.1:1/never')
                self.end_headers()
                if step.get('declared_length',0)>64000000:return
                if step.get('chunked'):
                    for chunk in (body[:8],body[8:]):
                        if chunk:self.wfile.write(f'{len(chunk):x}\r\n'.encode()+chunk+b'\r\n')
                    self.wfile.write(b'0\r\n\r\n')
                else:self.wfile.write(body)
                self.wfile.flush()
        self.server=Server(('127.0.0.1',0),Handler)
        self.url='http://127.0.0.1:'+str(self.server.server_port)
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':0.05},daemon=True)
        self.thread.start();return self
    def __exit__(self,*_):
        self.server.shutdown()
        with self.lock:sockets=list(self.sockets)
        for connection in sockets:
            try:connection.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            connection.close()
        self.server.server_close();self.thread.join(timeout=3)
        if self.thread.is_alive():raise RuntimeError('Loopback fixture failed to shut down')
