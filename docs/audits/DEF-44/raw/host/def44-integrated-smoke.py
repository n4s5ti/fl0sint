import asyncio,hashlib,importlib,importlib.util,json,os,pathlib,sys,tempfile,threading
from datetime import datetime,timedelta,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
w=pathlib.Path('/home/n4s5ti/Documents/dev/fl0sint-def44-source')
for p in w.glob('*-*/src'):sys.path.insert(0,str(p))
os.environ['AUTH_SECRET']='def44-fixture-only';os.environ['REDIS_URL']='redis://127.0.0.1:6379'
p=next(w.glob('*-enrichers/tests/enrichers/test_website_to_text.py'));spec=importlib.util.spec_from_file_location('fixture',p);f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
m=importlib.import_module(f.WebsiteToText.__module__);m.Logger=f._SilentLogger;importlib.import_module(f.WebsiteToText.__mro__[1].__module__).Logger=f._SilentLogger
pkg=next(w.glob('*-core/src/*_execution')).name;r=importlib.import_module(pkg+'.artifact_runtime');models=importlib.import_module(pkg+'.models')
body='<html><body><p title="1 > 0">Café &amp; tea</p><p>Repeat Repeat &lt; 3</p></body></html>'.encode();hits=[]
class Fixture(BaseHTTPRequestHandler):
 def do_GET(self):
  hits.append(self.path);self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
 def log_message(self,*args):pass
server=ThreadingHTTPServer(('127.0.0.1',0),Fixture);threading.Thread(target=server.serve_forever,daemon=True).start()
async def main(root):
 now=datetime.now(timezone.utc);policy=dict(issuer_id='fixture-owner',reviewer_id='fixture-reviewer',policy_id='fixture-retention',caller_id='website-to-text',scope='local-web-fetch',source_family='http',issued_at=(now-timedelta(minutes=1)).isoformat(),expires_at=(now+timedelta(hours=1)).isoformat(),retain_normalized_text=True)
 policy['content_digest']=hashlib.sha256(json.dumps(policy,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode()).hexdigest()
 config=root/'policy.json';config.write_text(json.dumps(dict(format_version='1.0',store_root=str(root/'store'),policies=[policy])));os.environ[r.RUNTIME_CONFIG_ENV]=str(config)
 e=f.WebsiteToText(sketch_id='source-proof-real',params_schema=[],params={},graph_service=f._RecordingGraph());url=f'http://127.0.0.1:{server.server_port}/fixture?token=do-not-retain'
 occurrences=await e.scan([f.Website(url=url),f.Website(url=url)]);assert len(occurrences)==2
 proofs=[];span_count=0
 for occurrence in occurrences:
  assert occurrence.status.value=='success',occurrence
  output=occurrence.to_input_outcome();ev=models.EvidenceEnvelope.model_validate_json(output.evidence[0].model_dump_json());encoded=ev.artifact_reference;proof=r.decode_source_proof(encoded);proofs.append(proof)
  assert 'do-not-retain' not in proof.model_dump_json()
  auth=dict(caller_id=proof.context.caller_id,scope=proof.context.scope,source_family=proof.context.source_family,operation_id=proof.context.operation_id,occurrence_id=proof.context.occurrence_id,config_path=config)
  resolved=r.resolve_persisted_source_proof(encoded,**auth);assert resolved.body==body,resolved
  assert proof.artifact.content_digest==hashlib.sha256(body).hexdigest()
  texts=[]
  for span in proof.spans:
   value=r.resolve_persisted_span(encoded,span.span_id,**auth);assert value.state.value=='available',value;texts.append(value.text);span_count+=1
  assert ''.join(texts)=='Café & tea Repeat Repeat < 3',texts
  assert r.resolve_persisted_source_proof(encoded,**{**auth,'operation_id':'wrong-operation'}).body is None
  assert r.resolve_persisted_source_proof(encoded,**{**auth,'scope':'other-investigation'}).body is None
 assert proofs[0].artifact.snapshot_id!=proofs[1].artifact.snapshot_id
 assert proofs[0].context.occurrence_id!=proofs[1].context.occurrence_id
 assert len(list((root/'store'/'objects').rglob('*.body')))==1
 config.write_text(json.dumps(dict(format_version='1.0',store_root=str(root/'store'),policies=[])))
 assert r.resolve_persisted_source_proof(encoded,**auth).state.value=='hold'
 held=await e.scan([f.Website(url=url)]);assert held[0].status.value=='hold';assert held[0].actual_resources.requests==1;assert held[0].actual_resources.bytes==len(body)
 print(json.dumps({'real_http_requests':len(hits),'duplicate_occurrences':2,'shared_content_objects':1,'resolved_spans':span_count,'exact_text':'Café & tea Repeat Repeat < 3','cross_operation_denied':True,'cross_scope_denied':True,'revocation_hold':True,'hold_consumed_requests':held[0].actual_resources.requests,'hold_consumed_bytes':held[0].actual_resources.bytes}))
try:
 with tempfile.TemporaryDirectory(prefix='def44-integrated-') as root:asyncio.run(main(pathlib.Path(root)))
finally:server.shutdown();server.server_close()
