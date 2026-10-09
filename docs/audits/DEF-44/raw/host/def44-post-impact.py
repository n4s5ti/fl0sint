import pathlib,subprocess,json,os,time
w=pathlib.Path('/home/n4s5ti/Documents/dev/fl0sint-def44-source');out=pathlib.Path('/tmp/def44-post-impact');out.mkdir(exist_ok=True)
def run(name,args):
 t=time.time();r=subprocess.run(args,cwd=w,env={**os.environ,'PIP3R_DISCLOSURE':'off'},capture_output=True,text=True,timeout=900)
 for ext,text in [('stdout',r.stdout),('stderr',r.stderr),('json',json.dumps({'command':args,'rc':r.returncode,'seconds':time.time()-t},indent=2))]:(out/(name+'.'+ext)).write_text(text)
 print(name,r.returncode,flush=True)
run('index',['pip3r','graphos','--cwd',str(w),'--index','--json']);run('drift',['pip3r','graphos','--cwd',str(w),'--drift','--no-auto-index','--json'])
for pattern,symbols in [('*-core/src/*_execution/acquisition.py',['ArtifactReference','SpanReference','SourceProofSpanReference','AcquisitionBundle','build_bundle']),('*-core/src/*_core/core/template_enricher.py',['TemplateEnricher']),('*-core/src/*_core/tasks/enricher.py',['run_connector_template','_safe_connector_scan_details']),('*-core/src/*_core/core/services/execution_service.py',['persist_structured_result']),('*-enrichers/src/*_enrichers/website/to_text.py',['WebsiteToText','scan','execute_structured']),('*-core/src/*_execution/fetch.py',['FetchOutcome','execute_fetch','execute_fetch_with_source_proof']),('*-core/src/*_execution/artifacts.py',['capture_source','resolve_source','resolve_span','normalize_html']),('*-core/src/*_execution/artifact_runtime.py',['load_artifact_runtime','resolve_persisted_source_proof','resolve_persisted_span'])]:
 p=next(w.glob(pattern)).relative_to(w)
 for s in symbols:
  for d in ['upstream','downstream']:run(s+'-'+d,['pip3r','blast','--cwd',str(w),'--no-auto-index','--json','--include-tests','--max-depth','3','--direction',d,str(p)+':'+s])
run('doctor-capture',['pip3r','doctor','--cwd',str(w),'--verify-refactor','capture_source','--json'])
