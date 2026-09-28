import pathlib,sys,importlib,inspect,textwrap,ast,pytest,hashlib
wt=pathlib.Path('/home/n4s5ti/Documents/dev/fl0sint-def42-s02')
for p in wt.glob('*-*/src'):sys.path.insert(0,str(p))
pkg=next(wt.glob('*-enrichers'));prefix=pkg.name[:-10];m=importlib.import_module(prefix+'_enrichers.website.to_text');src=pathlib.Path(m.__file__);before=hashlib.sha256(src.read_bytes()).hexdigest()
mode=sys.argv[1]
if mode=='source-swap':
 tree=ast.parse(textwrap.dedent(inspect.getsource(m.WebsiteToText._postprocess_occurrences)))
 class Swap(ast.NodeTransformer):
  def visit_Call(self,node):
   self.generic_visit(node)
   if isinstance(node.func,ast.Attribute) and node.func.attr=='create_relationship':node.args[0]=ast.parse('occurrences[0].source',mode='eval').body
   return node
 tree=ast.fix_missing_locations(Swap().visit(tree));ns=dict(vars(m));exec(compile(tree,'<source-swap-mutation>','exec'),ns);m.WebsiteToText._postprocess_occurrences=ns['_postprocess_occurrences']
 selected='test_legacy_public_execute_uses_loopback_occurrences_not_completion_zip'
else:
 original=m.WebsiteToText.execute_structured
 async def drop_failed(self,values):
  result=await original(self,values)
  return result.model_copy(update={'outcomes':tuple(o for o in result.outcomes if o.status is m.OutcomeStatus.SUCCESS)})
 m.WebsiteToText.execute_structured=drop_failed
 selected='test_structured_execution_distinguishes_none_transport_failure_from_empty_success'
rc=pytest.main([str(pkg/'tests/enrichers/test_website_to_text.py')+'::'+selected,'-q','-p','no:cacheprovider'])
assert hashlib.sha256(src.read_bytes()).hexdigest()==before
print('production_source_unchanged',before,'mutation',mode,'pytest_exit',rc)
sys.exit(0 if rc==1 else 1)
