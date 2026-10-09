import pathlib,json,subprocess,time,os
pre=pathlib.Path('/tmp/def45-pre');out=pathlib.Path('/tmp/def45-post-corrected');out.mkdir(exist_ok=True)
for p in sorted(pre.glob('*stream.json')):
 spec=json.loads(p.read_text());args=spec['command'];t=time.time();r=subprocess.run(args,cwd=spec['cwd'],env={**os.environ,'PIP3R_DISCLOSURE':'off'},capture_output=True,text=True,timeout=120)
 for suffix,data in [('stdout',r.stdout),('stderr',r.stderr),('json',json.dumps({'command':args,'cwd':spec['cwd'],'rc':r.returncode,'seconds':time.time()-t}))]:(out/(p.stem+'.'+suffix)).write_text(data)
 print(p.stem,r.returncode,flush=True)
