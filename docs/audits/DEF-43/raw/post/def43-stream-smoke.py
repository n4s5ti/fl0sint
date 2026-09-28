import pathlib,sys,importlib.util,asyncio,threading,time,json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
w=pathlib.Path('/home/n4s5ti/Documents/dev/fl0sint-def43-s03')
for p in w.glob('*-*/src'):sys.path.insert(0,str(p))
p=next(w.glob('*-core/tests/acquisition/test_fetch.py'));s=importlib.util.spec_from_file_location('fetch_fixture',p);f=importlib.util.module_from_spec(s);s.loader.exec_module(f)
started=threading.Event()
class Slow(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200);self.send_header('Content-Length','100');self.end_headers();self.wfile.write(b'abc');self.wfile.flush();started.set();time.sleep(2)
 def log_message(self,*a):pass
server=ThreadingHTTPServer(('127.0.0.1',0),Slow);threading.Thread(target=server.serve_forever,daemon=True).start()
async def main():
 url=f'http://127.0.0.1:{server.server_port}/';origin=url.rstrip('/')
 operation=f.admit_fetch(f._request((url,),elapsed_seconds=1.0),f._policy(origin),f.FetchParameters())
 t=asyncio.create_task(f.execute_fetch(operation));await asyncio.to_thread(started.wait,1)
 await asyncio.sleep(.1);before=time.monotonic();t.cancel();result=await asyncio.wait_for(t,.75);elapsed=time.monotonic()-before
 outcome=result.outcomes[0]
 assert outcome.status is f.FetchStatus.CANCELLED,(outcome,elapsed)
 assert result.actual_resources.requests==1 and result.actual_resources.bytes==3,result
 assert outcome.actual_resources.requests==1 and outcome.actual_resources.bytes==3,outcome
 assert outcome.body is None and elapsed<.75
 print(json.dumps({'scenario':'real_http_cancel_during_receive','status':outcome.status.value,'requests':result.actual_resources.requests,'bytes':result.actual_resources.bytes,'cancel_seconds':elapsed}))
try:asyncio.run(main())
finally:server.shutdown();server.server_close()
