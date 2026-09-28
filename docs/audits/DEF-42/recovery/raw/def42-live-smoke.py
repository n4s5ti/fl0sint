import sys, pathlib, importlib.util, importlib, asyncio, json, os
wt=pathlib.Path('/home/n4s5ti/Documents/dev/fl0sint-def42-s02')
for p in wt.glob('*-*/src'): sys.path.insert(0,str(p))
test=next(wt.glob('*-enrichers/tests/enrichers/test_website_to_text.py'))
spec=importlib.util.spec_from_file_location('fixture',test); f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
module=importlib.import_module(f.WebsiteToText.__module__)
assert module.__file__.startswith(str(wt))
module.Logger=f._SilentLogger
importlib.import_module(f.WebsiteToText.__mro__[1].__module__).Logger=f._SilentLogger
os.environ['NO_PROXY']='127.0.0.1,localhost'
async def main():
 graph=f._RecordingGraph(); e=f.WebsiteToText(sketch_id='def42-smoke',params_schema=[],params={},graph_service=graph)
 with f._loopback_pages() as root:
  inputs=[f.Website(url=root+'/slow'),f.Website(url=root+'/fast')]
  result=await e.execute(inputs)
 pairs=[(str(a.url).rsplit('/',1)[-1],b.text) for a,b,_ in graph.relationships]
 assert pairs==[('slow','slow page'),('fast','fast page')],pairs
 assert [x.text for x in result]==['slow page','fast page']
 print(json.dumps({'source':module.__file__,'transport':'real loopback HTTP','pairs':pairs,'flushes':graph.flushes,'result':'PASS'}))
asyncio.run(main())
