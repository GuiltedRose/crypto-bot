"""UNEXECUTED opt-in LOOPBACK ONLY native socket tests, no external RPC.

Default discovery skips these. Run on the monolith, not alongside production
traffic. Creates only ephemeral local servers; no namespace/firewall changes.
"""
import asyncio
import json
import os
import sys
import unittest
try:
    from _solana_binary import NativeHttp, connect_ws
except ImportError:
    NativeHttp=connect_ws=None

ENABLED=os.environ.get('SOLANA_NATIVE_LOOPBACK_STRESS')=='1' and sys.platform=='linux' and NativeHttp is not None and hasattr(NativeHttp,'request_sync') and hasattr(NativeHttp,'close')


@unittest.skipUnless(ENABLED,'Explicit monolith loopback stress and native rpc-engine required')
class NativeTransportStress(unittest.IsolatedAsyncioTestCase):
    async def test_http_format_errors_backpressure_and_cancellation(self):
        release=asyncio.Event();entered=asyncio.Event();requests=[];handlers=set()
        async def serve(reader,writer):
            task=asyncio.current_task();handlers.add(task)
            try:
                header=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),2)
                headers=dict(line.split(b':',1) for line in header.split(b'\r\n')[1:] if b':' in line)
                size=int(next(value for key,value in headers.items() if key.lower()==b'content-length'))
                self.assertLessEqual(size,65536)
                request=json.loads(await reader.readexactly(size));requests.append(request)
                entered.set();await release.wait()
                body=json.dumps({'jsonrpc':'2.0','id':1,'result':123}).encode()
                status=request['params'][0] if request['params'] and request['params'][0] in (429,503) else 200
                writer.write(b'HTTP/1.1 '+str(status).encode()+b' Fixture\r\nRetry-After: 60\r\nConnection: close\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
                await writer.drain()
            except (ConnectionError,asyncio.IncompleteReadError):
                pass # expected when a client cancels its response
            finally:
                writer.close()
                try: await writer.wait_closed()
                except ConnectionError: pass
                finally: handlers.discard(task)
        server=await asyncio.start_server(serve,'127.0.0.1',0,limit=65536)
        client=None
        try:
            client=NativeHttp('http://127.0.0.1:'+str(server.sockets[0].getsockname()[1]),os.stat('/proc/self/ns/net').st_ino,True)
            pending=asyncio.ensure_future(client.request('getSlot','[]'))
            await asyncio.wait_for(entered.wait(),2)
            with self.assertRaises(RuntimeError):client.request('getSlot','[]')
            release.set()
            status,_,text=await asyncio.wait_for(pending,3)
            self.assertEqual((status,json.loads(text)['result']),(200,123))
            self.assertEqual(requests[0],{'jsonrpc':'2.0','id':1,'method':'getSlot','params':[]})
            for status in (429,503):
                observed,retry,_=await asyncio.wait_for(client.request('getSlot',json.dumps([status])),3)
                self.assertEqual((observed,retry),(status,'60'))
            # Cancel a blocked body response; the per-client permit must recover.
            release.clear();entered.clear()
            pending=asyncio.ensure_future(client.request('getSlot','[]'))
            await asyncio.wait_for(entered.wait(),2);pending.cancel()
            with self.assertRaises(asyncio.CancelledError):await pending
            release.set()
            for _ in range(100):
                await asyncio.sleep(0.01)
                try: next_request=client.request('getSlot','[]');break
                except RuntimeError:continue
            else:self.fail('Cancelled HTTP operation did not release its permit')
            await asyncio.wait_for(next_request,3)
            release.clear();entered.clear()
            pending=asyncio.ensure_future(client.request('getSlot','[]'))
            await asyncio.wait_for(entered.wait(),2)
            client.close()
            with self.assertRaises(ConnectionError):await asyncio.wait_for(pending,3)
            with self.assertRaises(ConnectionError):client.request('getSlot','[]')
        finally:
            if client is not None:client.close()
            release.set();server.close();await server.wait_closed()
            if handlers:await asyncio.wait_for(asyncio.gather(*list(handlers),return_exceptions=True),3)

    async def test_ws_simultaneous_send_receive_close_and_bursts(self):
        from websockets.asyncio.server import serve
        gate=asyncio.Event();connections=[]
        async def handler(ws):
            connections.append(ws)
            await ws.recv() # send must work while native recv is already pending
            for index in range(32):await ws.send(json.dumps({'sequence':index}))
            await gate.wait()
        async with serve(handler,'127.0.0.1',0,max_queue=4,max_size=2000000) as server:
            endpoint='ws://127.0.0.1:'+str(server.sockets[0].getsockname()[1])
            try:
                for _ in range(3): # explicit bounded reconnection, no implicit fallback
                    ws=await asyncio.wait_for(connect_ws(endpoint,os.stat('/proc/self/ns/net').st_ino,True),3)
                    first=asyncio.ensure_future(ws.recv())
                    await ws.send(json.dumps({'jsonrpc':'2.0','id':1,'method':'logsSubscribe','params':[]}))
                    self.assertEqual(json.loads(await asyncio.wait_for(first,3))['sequence'],0)
                    for index in range(1,32):self.assertEqual(json.loads(await asyncio.wait_for(ws.recv(),3))['sequence'],index)
                    pending=asyncio.ensure_future(ws.recv())
                    await asyncio.wait_for(ws.close(),3)
                    with self.assertRaises(ConnectionError):await asyncio.wait_for(pending,3)
                self.assertEqual(len(connections),3)
            finally:
                gate.set()

    async def test_quiet_stream_survives_old_thirty_second_timeout(self):
        from websockets.asyncio.server import serve
        async def handler(ws):
            await ws.recv()
            await asyncio.sleep(31) # deliberately crosses the former idle deadline
            await ws.send('quiet-stream-alive')
        async with serve(handler,'127.0.0.1',0,ping_interval=None,close_timeout=2) as server:
            endpoint='ws://127.0.0.1:'+str(server.sockets[0].getsockname()[1])
            ws=await asyncio.wait_for(connect_ws(endpoint,os.stat('/proc/self/ns/net').st_ino,True),3)
            try:
                await ws.send(json.dumps({'jsonrpc':'2.0','id':1,'method':'logsSubscribe','params':[]}))
                self.assertEqual(await asyncio.wait_for(ws.recv(),36),'quiet-stream-alive')
            finally:await asyncio.wait_for(ws.close(),3)
