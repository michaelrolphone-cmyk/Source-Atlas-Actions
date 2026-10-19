"""Verify retained Ubuntu InRelease -> Packages -> pinned .deb -> extracted header."""
import hashlib,io,json,pathlib,re,subprocess,sys,tarfile
PACKAGE='libgmp-dev'; VERSION='2:6.3.0+dfsg-2ubuntu6'; ARCH='amd64'
DEB_SHA='ffadf91b116cc2647ef213742ac1cbe80f450af14413c146df6e200f8c3c9a1a'
HEADER_SHA='121611c0e60457f58a1fa42abf6dff4b98cd4649f38caf2ac09f8f457e03bb9a'
def sha(b):return hashlib.sha256(b).hexdigest()
def require(ok,msg):
    if not ok:raise RuntimeError(msg)
def signed_plaintext(root):
    output=root/'signed-plaintext.txt';require(not output.exists(),'Signed plaintext output already exists')
    command=['gpgv','--status-fd=1','--output',str(output.resolve()),'--keyring',str((root/'ubuntu-archive-keyring.gpg').resolve()),str((root/'noble-InRelease').resolve())]
    signature=subprocess.run(command,capture_output=True,text=True)
    (root/'signature-status.log').write_text(signature.stdout);(root/'signature-stderr.log').write_text(signature.stderr)
    require(signature.returncode==0 and '[GNUPG:] VALIDSIG ' in signature.stdout and output.is_file(),'InRelease signature failed')
    return output.read_text(),signature.stdout

def verify_packages(plaintext,packages):
    require(plaintext.count('\nSHA256:\n')==1,'Missing or ambiguous signed SHA256 section')
    block=plaintext.split('\nSHA256:\n',1)[1].split('\nSHA512:',1)[0]
    rows=[line.split() for line in block.splitlines() if re.match(r'^ [0-9a-f]{64} ',line)]
    expected=[r for r in rows if len(r)==3 and r[2]=='main/binary-amd64/Packages'];require(len(expected)==1,'Missing signed Packages hash')
    require(expected[0][0]==sha(packages) and int(expected[0][1])==len(packages),'Packages hash/size differs from signed metadata')

def verify_header(member,extracted,expected=HEADER_SHA):
    require(member==extracted and sha(member)==expected,'Package member/extracted GMP header mismatch')

def main(root):
    root=pathlib.Path(root);release=(root/'noble-InRelease').read_bytes();packages=(root/'noble-main-amd64-Packages').read_bytes()
    plaintext,signature_status=signed_plaintext(root)
    verify_packages(plaintext,packages)
    matches=[]
    for stanza in packages.decode().split('\n\n'):
        fields=dict(line.split(': ',1) for line in stanza.splitlines() if ': ' in line and not line.startswith(' '))
        if (fields.get('Package'),fields.get('Version'),fields.get('Architecture'))==(PACKAGE,VERSION,ARCH):matches.append(fields)
    require(len(matches)==1,'Pinned package missing or ambiguous');record=matches[0]
    debs=list(root.glob('libgmp-dev_*.deb'));require(len(debs)==1,'Expected one package')
    deb=debs[0];raw=deb.read_bytes();require(sha(raw)==record['SHA256']==DEB_SHA and len(raw)==int(record['Size']),'Pinned package hash/size mismatch')
    metadata=subprocess.check_output(['dpkg-deb','-f',str(deb),'Package','Version','Architecture'],text=True)
    fields=dict(x.split(': ',1) for x in metadata.splitlines());require((fields['Package'],fields['Version'],fields['Architecture'])==(PACKAGE,VERSION,ARCH),'Package metadata mismatch')
    header=root/'extracted/usr/include/x86_64-linux-gnu/gmp.h'
    tarbytes=subprocess.check_output(['dpkg-deb','--fsys-tarfile',str(deb)])
    with tarfile.open(fileobj=io.BytesIO(tarbytes)) as tar:
        members=[m for m in tar.getmembers() if m.name.removeprefix('./')=='usr/include/x86_64-linux-gnu/gmp.h']
        require(len(members)==1 and members[0].isfile(),'Missing or ambiguous package GMP header')
        member=tar.extractfile(members[0]).read()
    verify_header(member,header.read_bytes())
    (root/'verified-chain.json').write_text(json.dumps({'passed':True,'package':PACKAGE,'version':VERSION,'architecture':ARCH,'package_sha256':DEB_SHA,'header_sha256':HEADER_SHA,'inrelease_sha256':sha(release),'packages_sha256':sha(packages),'keyring_sha256':sha((root/'ubuntu-archive-keyring.gpg').read_bytes()),'validsig_records':[x for x in signature_status.splitlines() if x.startswith('[GNUPG:] VALIDSIG ')],'package_filename':record['Filename'],'trust_root':'Ubuntu archive keyring from exact pinned IDF container; retained with signed metadata','scope':'Host observation-plugin header only'},indent=2)+'\n')
if __name__=='__main__':main(sys.argv[1])
