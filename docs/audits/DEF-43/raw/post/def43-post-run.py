import pathlib,subprocess,json,os,time
w=pathlib.Path('/home/n4s5ti/Documents/dev/fl0sint-def43-s03');out=pathlib.Path('/tmp/def43-final-proof');out.mkdir(exist_ok=True)
def run(name,args,cwd=w,extra=None):
 start=time.time()
 try:
  r=subprocess.run(args,cwd=cwd,env={**os.environ,'PIP3R_DISCLOSURE':'off',**(extra or {})},capture_output=True,text=True,timeout=900);rc=r.returncode;stdout=r.stdout;stderr=r.stderr
 except subprocess.TimeoutExpired as e:rc=124;stdout=str(e.stdout);stderr=str(e.stderr)
 for ext,text in [('stdout',stdout),('stderr',stderr),('json',json.dumps(dict(command=args,cwd=str(cwd),rc=rc,seconds=time.time()-start),indent=2))]:(out/(name+'.'+ext)).write_text(text)
 print(name,rc,flush=True)
env={'PYTHONPATH':':'.join(str(p) for p in w.glob('*-*/src')),'PYTHONDONTWRITEBYTECODE':'1'}
python='/home/n4s5ti/Documents/dev/fl0sint/.venv/bin/python'
for part in ['core','enrichers']:
 pkg=next(w.glob('*-'+part));run(part+'-tests',[python,'-m','pytest','tests','-q','-p','no:cacheprovider'],pkg,env)
run('integrated-redirect-smoke',[python,'/tmp/def43-after-smoke.py'],extra=env)
run('index',['pip3r','graphos','--cwd',str(w),'--index','--json'])
run('drift',['pip3r','graphos','--cwd',str(w),'--drift','--no-auto-index','--json'])
f=next(w.glob('*-enrichers/src/*_enrichers/website/to_text.py')).relative_to(w);a=next(w.glob('*-core/src/*_execution/fetch.py')).relative_to(w)
for path,symbol in [(f,'WebsiteToText'),(f,'scan'),(f,'postprocess'),(f,'execute_structured'),(a,'admit_fetch'),(a,'execute_fetch'),(a,'TrustedFetchPolicy'),(a,'AdmittedFetchOperation')]:
 for direction in ['upstream','downstream']:run(symbol+'-'+direction,['pip3r','blast','--cwd',str(w),'--json','--no-auto-index','--include-tests','--max-depth','3','--direction',direction,str(path)+':'+symbol])
run('doctor',['pip3r','doctor','--cwd',str(w),'--verify-refactor',str(a),'--json'])
