import pathlib,subprocess,json,os,time,hashlib,shutil
w=pathlib.Path('/home/n4s5ti/Documents/dev/fl0sint-def45-extraction');out=pathlib.Path('/tmp/def45-pre');out.mkdir(exist_ok=True)
env={**os.environ,'PIP3R_DISCLOSURE':'off','AUTH_SECRET':'def45-fixture-only','REDIS_URL':'redis://127.0.0.1:6379','PYTHONPATH':':'.join(str(p) for p in w.glob('*-*/src'))}
def run(name,args,cwd=w):
 t=time.time();r=subprocess.run(args,cwd=cwd,env=env,capture_output=True,text=True,timeout=900)
 for ext,text in [('stdout',r.stdout),('stderr',r.stderr),('json',json.dumps({'command':args,'cwd':str(cwd),'rc':r.returncode,'seconds':time.time()-t},indent=2))]:(out/(name+'.'+ext)).write_text(text)
 print(name,r.returncode,flush=True)
run('baseline',['git','rev-parse','HEAD']);run('index',['pip3r','graphos','--cwd',str(w),'--index','--json']);run('drift',['pip3r','graphos','--cwd',str(w),'--drift','--no-auto-index','--json'])
roots=[('*-enrichers/src/*_enrichers/website/to_links.py',['WebsiteToLinks']),('*-enrichers/src/*_enrichers/website/to_crawler.py',['WebsiteToCrawler']),('*-enrichers/src/*_enrichers/organization/to_fire_enrich_contact_emails.py',['OrganizationToFireEnrichContactEmails']),('*-enrichers/src/*_enrichers/organization/to_fire_enrich_contact_individuals.py',['OrganizationToFireEnrichContactIndividuals']),('*-enrichers/src/*_enrichers/organization/to_fire_enrich_contact_people.py',['OrganizationToFireEnrichContactPeople']),('*-enrichers/src/*_enrichers/website/to_text.py',['WebsiteToText','scan','execute_structured']),('*-core/src/*_execution/artifacts.py',['normalize_html','resolve_source','resolve_span']),('*-core/src/*_execution/fetch.py',['execute_fetch_with_source_proof']),('*-core/src/*_execution/acquisition.py',['AcquisitionBundle','CandidateReference','SpanReference']),('*-core/src/*_core/core/enricher_base.py',['create_node','create_relationship']),('*-types/src/*_types/email.py',['Email']),('*-types/src/*_types/phone.py',['Phone']),('*-types/src/*_types/website.py',['Website'])]
for pattern,symbols in roots:
 p=next(w.glob(pattern)).relative_to(w)
 for s in symbols:
  for d in ['upstream','downstream']:run(s+'-'+d,['pip3r','blast','--cwd',str(w),'--no-auto-index','--json','--include-tests','--max-depth','3','--direction',d,str(p)+':'+s])
for part in ['core','enrichers']:
 pkg=next(w.glob('*-'+part));run(part+'-tests',['/home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python','-m','pytest','tests','-q','-p','no:cacheprovider'],pkg)
p=w/'docs/audits/DEF-45';shutil.copytree(out,p/'raw/pre',dirs_exist_ok=True)
sha=lambda f:hashlib.sha256(f.read_bytes()).hexdigest()
(p/'baseline.json').write_text(json.dumps({'base':subprocess.check_output(['git','rev-parse','HEAD'],cwd=w).decode().strip(),'kind':'genuine pre-edit receipts','configuration_sha256':{str(f.relative_to(w)):sha(f) for f in w.glob('**/pyproject.toml') if '.gitnexus' not in str(f)},'receipt_sha256':{str(f.relative_to(p)):sha(f) for f in p.rglob('*') if f.is_file()},'lsp':'unavailable missing pyright executable'},indent=2))
