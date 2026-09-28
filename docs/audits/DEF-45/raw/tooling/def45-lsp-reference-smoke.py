import asyncio,json,pathlib
async def main():
 w=pathlib.Path('/home/n4s5ti/Documents/dev/fl0sint-def45-extraction');f=next(w.glob('*-core/src/*_execution/observed_extraction.py'));text=f.read_text();line=next(i for i,s in enumerate(text.splitlines()) if s.startswith('def extract_observations('))
 p=await asyncio.create_subprocess_exec('/home/n4s5ti/.local/bin/pyright-langserver','--stdio',stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
 async def send(msg):
  b=json.dumps({'jsonrpc':'2.0',**msg}).encode();p.stdin.write(f'Content-Length: {len(b)}\r\n\r\n'.encode()+b);await p.stdin.drain()
 async def response(id):
  while True:
   head=await p.stdout.readuntil(b'\r\n\r\n');n=int(head.split(b'Content-Length: ')[1].split(b'\r')[0]);msg=json.loads(await p.stdout.readexactly(n))
   if msg.get('id')==id and 'method' not in msg:return msg
   if 'id' in msg and 'method' in msg:await send({'id':msg['id'],'result':None})
 try:
  await send({'id':1,'method':'initialize','params':{'processId':None,'rootUri':w.as_uri(),'capabilities':{}}});init=await response(1);assert 'result' in init,init
  await send({'method':'initialized','params':{}})
  await send({'method':'textDocument/didOpen','params':{'textDocument':{'uri':f.as_uri(),'languageId':'python','version':1,'text':text}}})
  await send({'id':2,'method':'textDocument/references','params':{'textDocument':{'uri':f.as_uri()},'position':{'line':line,'character':5},'context':{'includeDeclaration':True}}});refs=await response(2);assert refs.get('result'),refs
  assert any(r["range"]["start"]["line"]==334 for r in refs["result"]),refs
  print(json.dumps({'status':'PASS','server':'pyright1.1.414','references':refs['result']},indent=2))
 finally:
  p.terminate();await p.wait()
asyncio.run(asyncio.wait_for(main(),90))
