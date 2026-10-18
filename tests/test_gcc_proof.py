import importlib.util, pathlib, unittest
p=pathlib.Path(__file__).resolve().parents[1]/'gcc-proof/run-proof.py'
s=importlib.util.spec_from_file_location('proof',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class ProofBoundaryTests(unittest.TestCase):
 def test_only_output_changes(self):
  a=['gcc','-D__GNUC__=15','-mno-target-align','-fzero-init-padding-bits=all','-o','old.o','-c','file.c']
  b,change=m.redirect(a,pathlib.Path('/tmp/proof.o'))
  self.assertEqual(a[:5]+['/tmp/proof.o']+a[6:],b);self.assertEqual('old.o',a[5])
 def test_reject_plugin(self):
  with self.assertRaises(RuntimeError):m.redirect(['gcc','-fplugin=x','-c','a.c','-o','a.o'],pathlib.Path('/tmp/x'))
 def test_reject_missing_output(self):
  with self.assertRaises(RuntimeError):m.redirect(['gcc','-c','a.c'],pathlib.Path('/tmp/x'))
 def test_reject_dependency_output(self):
  with self.assertRaises(RuntimeError):m.redirect(['gcc','-MMD','-c','a.c','-o','a.o'],pathlib.Path('/tmp/x'))
 def test_reject_extra_output(self):
  with self.assertRaises(RuntimeError):m.redirect(['gcc','-c','a.c','-o','a.o','-o','b.o'],pathlib.Path('/tmp/x'))
 def test_normalization_preserves_locations(self):
  a=m.normalized([{'uid':13,'type':'int <T10>','location':{'line':2,'column':10}}])
  self.assertEqual(a,[{'type':'int <Tlocal>','location':{'line':2,'column':10}}])
 def test_workflow_has_no_upload_credentials(self):
  t=(p.parent/'workflow.yml').read_text()
  for forbidden in ('INDEX_WORKER_TOKEN','SITES_SERVICE_TOKEN','--upload','--engine clang'):
   self.assertNotIn(forbidden,t)
if __name__=='__main__':unittest.main()
