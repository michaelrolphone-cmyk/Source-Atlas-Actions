#!/usr/bin/env python3
"""Run the real producer with host libclang; no network or live upload.
Host semantic qualification is not target GCC object/ELF provenance.
"""
import ast
from types import SimpleNamespace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

PRODUCER = Path(os.environ.get('ATLAS_PRODUCER_UNDER_TEST', Path(__file__).resolve().parents[1] / 'scripts/source-atlas-ci-symbols.py'))
HEADER = '''static int helper(int value) { return value + 1; }
namespace { struct LocalType { int value; }; inline int hidden(LocalType x) { return helper(x.value); } }
struct GlobalType { int value; };
inline int shared_counter() { static int state=0; struct InlineType { int value; }; return ++state; }
static int local_counter() { static int local_state=0; struct LocalCounterType { int value; }; return ++local_state; }
'''
WRAPPER = '''import json,runpy,sys,urllib.request
out=sys.argv.pop(1)
calls=[]
class Reply:
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def read(self):return b'{"accepted":true}'
def send(request,timeout=0):
 calls.append(json.loads(request.data));return Reply()
urllib.request.urlopen=send
script=sys.argv[1];sys.argv=sys.argv[1:]
runpy.run_path(script,run_name='__main__')
open(out,'w').write(json.dumps(calls))
'''
class ProducerContracts(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.path=Path(self.temp.name);self.root=self.path/'repo';self.root.mkdir()
  (self.root/'common.h').write_text(HEADER)
  for name in ['a','b']:(self.root/(name+'.cpp')).write_text('#include "common.h"\nint '+name+'(int input) { LocalType local{input}; return hidden(local) + helper(input); }\n')
  self.git('init','-q');self.git('add','.');self.git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture')
  self.commit=self.git('rev-parse','HEAD').strip();self.commands=self.path/'compile_commands.json'
  self.commands.write_text(json.dumps([{'directory':str(self.root),'file':str(self.root/(n+'.cpp')),'arguments':['clang++','-std=c++17','-c',str(self.root/(n+'.cpp')),'-o',n+'.o']} for n in ['a','b']]))
 def git(self,*args):return subprocess.check_output(['git','-C',str(self.root),*args],text=True)
 def run_producer(self,upload=False):
  output=self.path/'symbols.json';payloads=self.path/'payloads.json'
  args=[str(PRODUCER),'--root',str(self.root),'--repository','fixture/repository','--compile-commands',str(self.commands),'--source-set-version','fixture-set','--output',str(output)]
  env={**os.environ,'ATLAS_URL':'https://fixture.invalid','INDEX_WORKER_TOKEN':'fixture-token','SITES_SERVICE_TOKEN':'fixture-service'}
  if upload:
   wrapper=self.path/'wrapper.py';wrapper.write_text(WRAPPER);command=[sys.executable,str(wrapper),str(payloads),*args,'--upload']
  else:command=[sys.executable,*args]
  p=subprocess.run(command,env=env,text=True,capture_output=True)
  return p,json.loads(output.read_text()) if output.exists() else None,json.loads(payloads.read_text()) if payloads.exists() else None
 def good(self,upload=False):
  p,out,calls=self.run_producer(upload);self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(out['coverage']['indexed_units'],2,out['coverage']);self.assertTrue(out['coverage']['complete']);return out,calls
 def test_observed_ranges_and_schema(self):
  out,_=self.good();self.assertEqual(out['architecture_schema'],2)
  rows=[x for x in out['entries'] if x['symbol']=='helper' and x['kind']=='definition'];self.assertEqual(len(rows),2)
  for r in rows:self.assertEqual((r['line'],r['column'],r['end_line'],r['end_column']),(1,12,1,len(HEADER.splitlines()[0])+1))
  for r in out['entries']:
   self.assertGreater(r.get('column',1),0)
   if 'end_column' in r:self.assertIn('column',r);self.assertGreaterEqual(r['end_line'],r['line'])
 def test_same_header_local_function_scoped_per_unit(self):
  out,_=self.good();rows=[x for x in out['entries'] if x['symbol']=='helper' and x['kind']=='definition']
  self.assertEqual({x['translation_unit'] for x in rows},{'a.cpp','b.cpp'});self.assertEqual(len({x['usr'] for x in rows}),1)
  self.assertTrue(all(x['identity_scope']=='translation_unit' for x in rows))
  self.assertEqual(len({(x['translation_unit'],x['usr']) for x in rows}),2)
 def test_anonymous_namespace_and_local_type_scopes(self):
  out,_=self.good()
  for symbol in ['hidden','LocalType']:
   rows=[x for x in out['entries'] if x['symbol']==symbol and x['kind']=='definition'];self.assertTrue(rows,symbol);self.assertTrue(all(x['identity_scope']=='translation_unit' for x in rows),rows)
 def test_global_identity_remains_global(self):
  out,_=self.good();rows=[x for x in out['entries'] if x['symbol']=='GlobalType' and x['kind']=='definition'];self.assertTrue(rows);self.assertTrue(all(x['identity_scope']=='global' for x in rows))
 def test_external_inline_local_entities_are_shared(self):
  out,_=self.good()
  for symbol in ['shared_counter','state','InlineType']:
   rows=[x for x in out['entries'] if x['symbol']==symbol and x['kind']=='definition'];self.assertTrue(rows,symbol)
   self.assertEqual({x['translation_unit'] for x in rows},{'a.cpp','b.cpp'});self.assertEqual(len({x['usr'] for x in rows}),1)
   self.assertTrue(all(x['identity_scope']=='global' for x in rows),rows)
 def test_internal_function_local_entities_remain_unit_scoped(self):
  out,_=self.good()
  for symbol in ['local_counter','local_state','LocalCounterType']:
   rows=[x for x in out['entries'] if x['symbol']==symbol and x['kind']=='definition'];self.assertTrue(rows,symbol);self.assertTrue(all(x['identity_scope']=='translation_unit' for x in rows),rows)
 def test_call_owner_and_target_scopes(self):
  out,_=self.good();rows=[x for x in out['entries'] if x.get('relation_kind')=='calls' and x['symbol']=='helper'];self.assertTrue(rows)
  self.assertTrue(all(x['identity_scope']=='translation_unit' for x in rows))
  for r in rows:
   if r.get('caller')=='hidden':self.assertEqual(r['caller_scope'],'translation_unit');self.assertEqual(r['owner_scope'],'translation_unit')
   elif r.get('caller') in ['a','b']:self.assertEqual(r['caller_scope'],'global');self.assertEqual(r['owner_scope'],'global')
 def test_schema2_upload_does_not_collide_with_historical_schema1(self):
  out,calls=self.good(True);args=calls[0]['arguments'];base='fixture/repository@'+self.commit+':'+out['configuration_hash']+':fixture-set:'
  old=str(uuid.uuid5(uuid.NAMESPACE_URL,base+'architecture-v1'));new=str(uuid.uuid5(uuid.NAMESPACE_URL,base+'architecture-v2'))
  self.assertNotEqual(args['index_id'],old);self.assertEqual(args['index_id'],new);self.assertEqual(args['architecture_schema'],2)
  self.assertTrue(all(c['operation']=='submit_symbol_index' for c in calls))
 def test_repeat_upload_keeps_exact_schema2_identity_and_bytes(self):
  first,calls1=self.good(True);second,calls2=self.good(True);self.assertEqual(first,second);self.assertEqual(calls1,calls2)
 def test_failed_unit_has_explicit_incomplete_coverage(self):
  (self.root/'b.cpp').write_text('this is invalid C++ !');self.git('add','.');self.git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','invalid unit')
  p,out,_=self.run_producer();self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(out['coverage']['indexed_units'],1);self.assertFalse(out['coverage']['complete']);self.assertEqual(len(out['coverage']['failed_units']),1)
 def test_dirty_checkout_rejected_before_output(self):
  (self.root/'a.cpp').write_text('// dirty\n');p,out,_=self.run_producer();self.assertNotEqual(p.returncode,0);self.assertIn('dirty checkout',p.stderr);self.assertIsNone(out)
 def test_empty_compilation_database_rejected(self):
  self.commands.write_text('[]');p,out,_=self.run_producer();self.assertNotEqual(p.returncode,0);self.assertIn('compilation database is empty',p.stderr);self.assertIsNone(out)
class LocationContracts(unittest.TestCase):
 def location(self,line,column,end_line,end_column,end_file='source.cpp'):
  tree=ast.parse(PRODUCER.read_text());function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='observed_location')
  namespace={'source_path':lambda name:name};exec(compile(ast.Module(body=[function],type_ignores=[]),str(PRODUCER),'exec'),namespace)
  point=lambda line,column,file:SimpleNamespace(line=line,column=column,file=SimpleNamespace(name=file) if file else None)
  cursor=SimpleNamespace(location=point(line,column,'source.cpp'),extent=SimpleNamespace(end=point(end_line,end_column,end_file)))
  return namespace['observed_location'](cursor,'source.cpp')
 def test_multiline_range_preserved(self):self.assertEqual(self.location(4,9,6,2),{'column':9,'end_line':6,'end_column':2})
 def test_backward_range_not_fabricated(self):self.assertEqual(self.location(4,9,3,20),{'column':9});self.assertEqual(self.location(4,9,4,8),{'column':9})
 def test_cross_file_or_absent_range_remains_unknown(self):self.assertEqual(self.location(4,9,6,20,'other.h'),{'column':9});self.assertEqual(self.location(4,9,6,20,None),{'column':9})
 def test_unknown_columns_not_fabricated(self):self.assertEqual(self.location(4,0,6,20),{});self.assertEqual(self.location(4,9,6,0),{'column':9})
if __name__=='__main__':unittest.main(verbosity=2)
