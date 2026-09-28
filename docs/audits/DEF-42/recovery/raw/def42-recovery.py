import os, json, subprocess, pathlib, time, hashlib
out=pathlib.Path('/tmp/def42-recovery');out.mkdir(exist_ok=True)
head=pathlib.Path('/home/n4s5ti/Documents/dev/fl0sint-def42-s02')
base=pathlib.Path('/home/n4s5ti/Documents/dev/fl0sint-def42-baseline-audit')
python='/home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python'
records=[]
def run(name,argv,cwd,env=None,timeout=600):
 t=time.monotonic()
 try:
  p=subprocess.run(argv,cwd=cwd,env={**os.environ,'PIP3R_DISCLOSURE':'off',**(env or {})},capture_output=True,text=True,timeout=timeout);rc=p.returncode;stdout=p.stdout;stderr=p.stderr
 except subprocess.TimeoutExpired as e: rc=124;stdout=(e.stdout or b'').decode() if isinstance(e.stdout,bytes) else e.stdout or '';stderr=str(e)
 (out/(name+'.stdout')).write_text(stdout);(out/(name+'.stderr')).write_text(stderr)
 r=dict(name=name,argv=argv,cwd=str(cwd),environment=env or {},rc=rc,seconds=time.monotonic()-t)
 (out/(name+'.json')).write_text(json.dumps(r,indent=2));records.append(r);print(name,rc,round(r['seconds'],2),flush=True)
 return rc
for label,wt in [('baseline',base),('head',head)]:
 pkg=next(wt.glob('*-enrichers'));name=pkg.name[:-10]; src=next(pkg.glob('src/*_enrichers/website/to_text.py')).relative_to(wt).as_posix()
 run(label+'-index',['pip3r','graphos','--index','--cwd',str(wt),'--json'],out,timeout=600)
 run(label+'-drift',['pip3r','graphos','--drift','--cwd',str(wt),'--json','--no-auto-index'],out)
 for symbol in ['WebsiteToText','scan','postprocess','execute','execute_structured']+(['WebsiteTextOccurrence'] if label=='head' else []):
  for direction in ['upstream','downstream']:
   run(label+'-'+symbol+'-'+direction,['pip3r','blast','--cwd',str(wt),'--json','--no-auto-index','--include-tests','--max-depth','3','--direction',direction,src+':'+symbol],out)
 env={'PYTHONPATH':':'.join(str(x/'src') for x in wt.glob('*') if x.is_dir() and (x/'src').is_dir()),'PYTHONDONTWRITEBYTECODE':'1'}
 run(label+'-package-tests',[python,'-m','pytest','tests','-q','-p','no:cacheprovider'],pkg,env,900)
 if label=='head':
  run('head-focused',[python,'-m','pytest','tests/enrichers/test_website_to_text.py','tests/enrichers/test_acquisition_fixtures.py','-q','-p','no:cacheprovider'],pkg,env)
  run('head-source-proof',[python,'-c',f'import importlib; m=importlib.import_module("{name}_enrichers.website.to_text"); print(m.__file__); assert m.__file__.startswith({str(wt)!r})'],pkg,env)
  run('head-doctor',['pip3r','doctor','--cwd',str(wt),'--verify-refactor','WebsiteToText','--json'],out)
  run('head-hunt',['pip3r','doctor','--cwd',str(wt),'--hunt','--json'],out)
  run('head-final-drift',['pip3r','graphos','--drift','--cwd',str(wt),'--json','--no-auto-index'],out)
(out/'records.json').write_text(json.dumps(records,indent=2))
