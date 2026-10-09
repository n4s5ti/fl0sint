import runpy,sys,importlib,json,tempfile,pathlib,threading,hashlib,dataclasses
from datetime import datetime,timedelta,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
m=runpy.run_path('/tmp/def45-host.py',run_name='unused') if False else None
w=pathlib.Path('/home/n4s5ti/Documents/dev/fl0sint-def45-extraction')
sys.path[:0]=[str(p) for p in w.glob('*-*/src')]
pkg=next(w.glob('*-core/src/*_execution')).name
ar=importlib.import_module(pkg+'.artifact_runtime');ex=importlib.import_module(pkg+'.observed_extraction');models=importlib.import_module(pkg+'.models')
import os,subprocess
out=pathlib.Path('/tmp/def45-parent-smoke');out.mkdir(exist_ok=True)
body=pathlib.Path('/tmp/def45-live-fixture.html').read_bytes()
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  if self.path=='/seed':self.send_response(302);self.send_header('Location','/people/team.html?edition=2026');self.send_header('Content-Length','0');self.end_headers();return
  self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
 def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
clean={'PATH':os.environ['PATH'],'PYTHONPATH':os.pathsep.join(str(p) for p in w.glob('*-*/src'))}
cli=[sys.executable,str(next(w.glob('*-core/examples/observed_extraction.py')))]
def run(name,args):
 r=subprocess.run(cli+args,env=clean,capture_output=True,text=True,timeout=30)
 (out/(name+'.stdout')).write_text(r.stdout);(out/(name+'.stderr')).write_text(r.stderr)
 assert r.returncode==0,(name,r.returncode,r.stderr)
 return json.loads(r.stdout)
try:
 with tempfile.TemporaryDirectory(prefix='def45-parent-') as root:
  now=datetime.now(timezone.utc);policy=dict(issuer_id='fixture-owner',reviewer_id='fixture-reviewer',policy_id='fixture-retention',caller_id='website-to-text',scope='local-web-fetch',source_family='http',issued_at=(now-timedelta(minutes=1)).isoformat(),expires_at=(now+timedelta(hours=1)).isoformat(),retain_normalized_text=True)
  policy['content_digest']=hashlib.sha256(json.dumps(policy,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode()).hexdigest()
  config=pathlib.Path(root)/'policy.json';config.write_text(json.dumps(dict(format_version='1.0',store_root=str(pathlib.Path(root)/'store'),policies=[policy])))
  live=run('live',['--url',f'http://127.0.0.1:{server.server_port}/seed','--runtime-config',str(config)])
  assert live['status']=='success',live
  proof=ar.decode_source_proof(live['source_proof']);metadata=models.PersistedMetadata.model_validate(live['metadata']);result=ex.parse_observed_extraction_metadata(metadata)
  args=dict(caller_id='website-to-text',scope='local-web-fetch',source_family='http',operation_id=proof.context.operation_id,occurrence_id=proof.context.occurrence_id,config_path=config)
  saved=run('saved',['--proof',live['source_proof'],'--runtime-config',str(config),'--operation',proof.context.operation_id,'--occurrence',proof.context.occurrence_id])
  assert saved['result']==live['metadata'], 'saved/live mismatch'
  receipts=[]
  for obs in result.observations:
   assert obs.source_url.endswith('/people/team.html?edition=2026'),obs
   resolved=ar.resolve_persisted_observation(live['source_proof'],metadata,obs.observation_id,**args)
   assert resolved.state.value=='available',(obs,resolved)
   assert resolved.raw==body[obs.raw_span.start_byte:obs.raw_span.end_byte]
   if obs.context_span:assert resolved.context_raw==body[obs.context_span.start_byte:obs.context_span.end_byte]
   assert obs.execution_state.value=='not_executable'
   receipts.append({'id':obs.observation_id,'kind':obs.kind.value,'value':obs.value,'person':obs.person_name,'raw':resolved.raw.decode(),'source':obs.source_url})
  contacts=[o for o in result.observations if '@' in o.value and o.kind.value!='observed_general_text']
  assert not any(o.value=='hidden@directory.test' for o in contacts)
  assert all(o.person_name is None for o in contacts if o.value=='info@directory.test')
  assert any(o.person_name=='Ada Example' for o in contacts)
  assert any(o.person_name=='Bo Example' for o in contacts)
  ada=[o for o in contacts if o.value=='ada@directory.test'];assert len({o.raw_span.start_byte for o in ada})>=2
  links=[o.value for o in result.observations if o.kind.value=='observed_link'];assert any('/directory?page=1' in v for v in links) and any('/directory?page=2' in v for v in links)
  first=result.observations[0]
  for key,value in [('operation_id','wrong-operation'),('scope','wrong-scope')]:
   denied=ar.resolve_persisted_observation(live['source_proof'],metadata,first.observation_id,**{**args,key:value});assert denied.state.value!='available'
  altered=dataclasses.replace(first,value='forged-value');changed=dataclasses.replace(result,observations=(altered,)+result.observations[1:]);bad=ex.serialize_observed_extraction_metadata(changed)
  assert ar.resolve_persisted_observation(live['source_proof'],bad,first.observation_id,**args).state.value!='available'
  assert live['actual_resources']['requests']==2,live['actual_resources']
  (out/'resolved.json').write_text(json.dumps(receipts,indent=2));print(json.dumps({'status':'PASS','observations':len(receipts),'resources':live['actual_resources'],'checks':['flag-only clean live CLI','saved equality','all exact raw/context spans','wrong scope/operation denied','tampered value denied','actual final URL','distinct duplicate spans','generic and person contexts','meaningful queries','non-executable observations']}))
finally:server.shutdown();server.server_close()
