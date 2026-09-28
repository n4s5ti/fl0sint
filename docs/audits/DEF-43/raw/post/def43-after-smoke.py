import pathlib,sys,importlib.util,importlib,threading,json,asyncio
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
wt=pathlib.Path('/home/n4s5ti/Documents/dev/fl0sint-def43-s03')
for p in wt.glob('*-*/src'):sys.path.insert(0,str(p))
p=next(wt.glob('*-enrichers/tests/enrichers/test_website_to_text.py'));spec=importlib.util.spec_from_file_location('fixture',p);f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
m=importlib.import_module(f.WebsiteToText.__module__);m.Logger=f._SilentLogger;importlib.import_module(f.WebsiteToText.__mro__[1].__module__).Logger=f._SilentLogger
hits=[]
class Target(BaseHTTPRequestHandler):
 def do_GET(self):
  hits.append('outside-origin');self.send_response(200);self.end_headers();self.wfile.write(b'outside scope')
 def log_message(self,*a):pass
second=ThreadingHTTPServer(('127.0.0.1',0),Target)
class Origin(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(302);self.send_header('Location',f'http://127.0.0.1:{second.server_port}/target');self.send_header('Content-Length','0');self.end_headers()
 def log_message(self,*a):pass
first=ThreadingHTTPServer(('127.0.0.1',0),Origin)
for s in [first,second]:threading.Thread(target=s.serve_forever,daemon=True).start()
async def main():
 graph=f._RecordingGraph();e=f.WebsiteToText(sketch_id='def43-after',params_schema=[],params={},graph_service=graph)
 try:await e.execute([f.Website(url=f'http://127.0.0.1:{first.server_port}/start')])
 except m.WebsiteFetchError as error:failure=type(error).__name__
 else:raise AssertionError('unsafe redirect did not fail')
 assert hits==[];assert len(graph.relationships)==0
 print(json.dumps({'after_defect_blocked':True,'source':m.__file__,'outside_origin_dispatched':len(hits),'failure':failure,'graph_edges':len(graph.relationships)}))
try:asyncio.run(main())
finally:
 for s in [first,second]:s.shutdown();s.server_close()
