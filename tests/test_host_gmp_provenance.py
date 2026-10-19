import pathlib,sys,tempfile,types,unittest
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'gcc-proof'))
import verify_host_gmp as m
class HostGMPProvenance(unittest.TestCase):
 def test_signed_package_digest(self):
  data=b'Packages fixture';plain='Origin: Ubuntu\nSHA256:\n '+m.sha(data)+' '+str(len(data))+' main/binary-amd64/Packages\n'
  m.verify_packages(plain,data)
  with self.assertRaises(RuntimeError):m.verify_packages(plain,data+b'changed')
 def test_ambiguous_signed_digest(self):
  with self.assertRaises(RuntimeError):m.verify_packages('Origin: Ubuntu\nSHA256:\n\nSHA256:\n',b'')
 def test_unverified_armored_text_never_parsed(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=pathlib.Path(tmp);(root/'noble-InRelease').write_text('UNSIGNED misleading SHA256 section before armor')
   def run(cmd,**kwargs):
    self.assertIn('--output',cmd);pathlib.Path(cmd[cmd.index('--output')+1]).write_text('VERIFIED payload only')
    return types.SimpleNamespace(returncode=0,stdout='[GNUPG:] VALIDSIG fixture',stderr='')
   with patch.object(m.subprocess,'run',run):self.assertEqual(m.signed_plaintext(root)[0],'VERIFIED payload only')
 def test_signature_failure_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   with patch.object(m.subprocess,'run',return_value=types.SimpleNamespace(returncode=1,stdout='',stderr='bad signature')):
    with self.assertRaises(RuntimeError):m.signed_plaintext(pathlib.Path(tmp))
 def test_member_extracted_header_binding(self):
  data=b'header';m.verify_header(data,data,m.sha(data))
  with self.assertRaises(RuntimeError):m.verify_header(data,b'other',m.sha(data))
  with self.assertRaises(RuntimeError):m.verify_header(b'other',data,m.sha(data))
if __name__=='__main__':unittest.main()
