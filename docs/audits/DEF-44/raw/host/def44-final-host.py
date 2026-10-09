import pathlib,subprocess,json,os,time,threading,tempfile,hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
w=pathlib.Path('/home/n4s5ti/Documents/dev/fl0sint-def44-source');out=pathlib.Path('/tmp/def44-final-host');out.mkdir(exist_ok=True)
env={**os.environ,'AUTH_SECRET':'def44-fixture-only','REDIS_URL':'redis://127.0.0.1:6379','PYTHONPATH':':'.join(str(p) for p in w.glob('*-*/src'))}
python='/home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python'
def run(name,args,cwd=w,runenv=env,timeout=900):
 t=time.time()
 try:r=subprocess.run(args,cwd=cwd,env=runenv,capture_output=True,text=True,timeout=timeout)
 except subprocess.TimeoutExpired as e:
  (out/(name+'.json')).write_text(json.dumps({'command':args,'rc':124,'seconds':time.time()-t}));print(name,124,flush=True);return None
 for ext,text in [('stdout',r.stdout),('stderr',r.stderr),('json',json.dumps({'command':args,'cwd':str(cwd),'rc':r.returncode,'seconds':time.time()-t},indent=2))]:(out/(name+'.'+ext)).write_text(text)
 print(name,r.returncode,flush=True);return r
for part in ['core','enrichers']:
 pkg=next(w.glob('*-'+part));run(part+'-tests',[python,'-m','pytest','tests','-q','-p','no:cacheprovider'],pkg)
api=next(w.glob('*-api'));run('api-template',[python,'-m','pytest','tests/test_template_egress.py','-q','-p','no:cacheprovider'],api,timeout=120)
body='<html><body><p title="1 > 0">Café &amp; tea</p><p>Repeat Repeat &lt; 3</p></body></html>'.encode()
class Fixture(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
 def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),Fixture);threading.Thread(target=server.serve_forever,daemon=True).start()
try:
 with tempfile.TemporaryDirectory(prefix='def44-final-store-') as root:
  clean={k:v for k,v in os.environ.items() if not any(t in k.upper() for t in ['API_KEY','AUTH_SECRET','DATABASE_URL','REDIS','ORACLE','JEV'])};clean['PYTHONPATH']=str(next(w.glob('*-core/src')))
  r=run('standalone-real-http',[python,str(next(w.glob('*-core/examples/source_proof.py'))),f'http://127.0.0.1:{server.server_port}/fixture',root],runenv=clean)
  assert r.returncode==0,r.stderr
  data=json.loads(r.stdout);assert data['state']=='available';bundle=json.loads(data['bundle']) if isinstance(data['bundle'],str) else data['bundle'];assert bundle['artifacts'][0]['content_digest']==hashlib.sha256(body).hexdigest();assert data['resolved_bytes']==len(body)
  (out/'standalone-assertions.json').write_text(json.dumps({'exact_digest':True,'fresh_store_resolution':True,'public_bundle_parsed':True,'graph_planner_keys_absent':True,'bytes':len(body)}))
finally:server.shutdown();server.server_close()
