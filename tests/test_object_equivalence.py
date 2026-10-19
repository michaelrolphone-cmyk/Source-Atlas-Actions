import pathlib,struct,sys,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'gcc-proof'))
from object_equivalence import compare_bytes,EquivalenceError
SUFFIX='-fplugin=/proof.so -fplugin-arg-proof-output=/proof.jsonl'
BASE=b'GNU C23 15.2.0 -gdwarf-4 -Os'
def fixture(suffix='', change=None):
    names=['.text','.data','.bss','.empty','.rela.text','.symtab','.strtab','.debug_info','.rela.debug_info','.debug_abbrev','.debug_str','.debug_line','.shstrtab']
    producer=BASE+(b' '+suffix.encode() if suffix else b'')
    strings=[producer,b'unit.c',b'int',b'function',b'unreferenced']
    if suffix:strings.reverse()
    if change=='unknown-string':strings[strings.index(b'unreferenced')]=b'unreferencXd'
    if change=='name':strings[strings.index(b'function')]=b'functioX'
    if change=='unexpected-producer':strings[strings.index(producer)]=producer+b' -funknown'
    if change=='duplicate-string':strings.append(b'unreferenced')
    pool=b''.join(x+b'\0' for x in strings)
    off=lambda s:pool.index(s+b'\0')
    prod=next(x for x in strings if x.startswith(BASE))
    # One DWARF4 CU, base type and subprogram. Attribute layouts are intentionally explicit.
    abbrev=bytes([1,0x11,1,0x25,0x0e,3,0x0e,0,0,2,0x24,0,3,0x0e,0x0b,0x0b,0x3e,0x0b,0,0,3,0x2e,0,3,0x0e,0x3b,0x05,0x49,0x13,0,0,0])
    root=bytes([1])+struct.pack('<II',off(prod),off(prod if change=='non-producer-alias' else b'unit.c'))
    typename=b'unit.c' if change=='strp-retarget' else b'int'
    type_die=bytes([2])+struct.pack('<I',off(typename))+bytes([8 if change=='type-size' else 4,5])
    function=b'functioX' if change=='name' else b'function'
    sub=bytes([3])+struct.pack('<IHI',off(function),43 if change=='line' else 42,21 if change=='type-reference' else 20)
    body=struct.pack('<HIB',4,0,4)+root+type_die+sub+b'\0'
    info=struct.pack('<I',len(body))+body
    if change=='unsupported-form':abbrev=abbrev[:5]+bytes([0x7f])+abbrev[6:]
    payload={'.text':b'\x01\x02\x03\x04','.data':b'\x05\x06\x07\x08','.bss':b'', '.empty':b'', '.rela.text':bytes(12),'.symtab':bytes(16)+struct.pack('<IIIBBH',0,1 if change=='reloc-symbol-value' else 0,0,3,0,names.index('.debug_str')+1),'.strtab':b'\0symbol\0','.debug_info':info,'.debug_abbrev':abbrev,'.debug_str':pool,'.debug_line':b'line-table'}
    payload['.rela.debug_info']=b''.join(struct.pack('<IIi',pos,(1<<8)|(2 if change=='reloc-type' else 1),1 if change=='reloc-addend' else 0) for pos in ([12,16,21,28] if change!='reloc-missing' else [12,16,21]))
    if change=='pdiff-outside':payload['.rela.debug_info']+=struct.pack('<IIi',34,59,0)
    if change=='pdiff-overlap':payload['.rela.debug_info']+=struct.pack('<IIi',13,59,0)
    if change=='reloc-overlap':payload['.rela.debug_info']+=struct.pack('<IIi',13,1,0)
    if change=='unsupported-debug-section':
        names.insert(-1,'.debug_types');payload['.debug_types']=b'opaque-strp-content'
    if change in ('code','data','relocation','symbol','debug-line'):
        section={'code':'.text','data':'.data','relocation':'.rela.text','symbol':'.symtab','debug-line':'.debug_line'}[change]
        value=payload[section];payload[section]=bytes([value[0]^1])+value[1:]
    shnames=b'\0'+b''.join(n.encode()+b'\0' for n in names);payload['.shstrtab']=shnames
    headers=[(0,0,0,0,0,0,0,0,0,0)];raw=bytearray(52)
    for n in names:
        typ=8 if n=='.bss' else 4 if n.startswith('.rela.') else 2 if n=='.symtab' else 3 if n in ('.strtab','.shstrtab') else 1
        flags=6 if n=='.text' else 3 if n in ('.data','.bss') else 0
        if n=='.text' and change=='flags':flags^=1
        align=4 if n in ('.text','.data','.bss','.empty','.rela.text','.symtab') else 1
        if n=='.text' and change=='alignment':align=8
        raw+=bytes((-len(raw))%align);offset=len(raw);value=payload[n]
        headers.append((shnames.index(n.encode()+b'\0'),typ,flags,0,offset,4 if n=='.bss' else len(value),names.index('.symtab')+1 if n.startswith('.rela.') else 0,names.index('.debug_info')+1 if n=='.rela.debug_info' else 1 if n=='.rela.text' else 0,align,12 if n.startswith('.rela.') else 16 if n=='.symtab' else 0));raw+=value
    raw+=bytes((-len(raw))%4);shoff=len(raw)
    for h in headers:raw+=struct.pack('<10I',*h)
    raw[:52]=struct.pack('<16sHHIIIIIHHHHHH',b'\x7fELF\x01\x01\x01'+bytes(9),1,94,1,0,0,shoff,0x300,52,0,0,40,len(headers),len(headers)-1)
    return bytes(raw)

class ObjectEquivalence(unittest.TestCase):
    def test_raw_identical(self):
        r=compare_bytes(fixture(),fixture());self.assertTrue(r['raw_objects_identical'])
    def test_only_recorded_producer_and_strp_layout(self):
        r=compare_bytes(fixture(),fixture(SUFFIX),'',SUFFIX);self.assertTrue(r['passed']);self.assertFalse(r['raw_objects_identical'])
    def test_repeated_instrumentation_output(self):
        second=SUFFIX.replace('proof.jsonl','second.jsonl')
        self.assertTrue(compare_bytes(fixture(SUFFIX),fixture(second),SUFFIX,second)['passed'])
    def test_negative_controls(self):
        for mutation in ['unknown-string','name','unexpected-producer','duplicate-string','non-producer-alias','strp-retarget','type-size','line','type-reference','unsupported-form','code','data','relocation','symbol','debug-line','flags','alignment','unsupported-debug-section','reloc-symbol-value','reloc-type','reloc-addend','reloc-missing']:
            with self.subTest(mutation=mutation):
                with self.assertRaises(EquivalenceError):compare_bytes(fixture(),fixture(SUFFIX,mutation),'',SUFFIX)
    def test_pdiff32_outside_strp(self):
        self.assertTrue(compare_bytes(fixture(change='pdiff-outside'),fixture(SUFFIX,'pdiff-outside'),'',SUFFIX)['passed'])
    def test_wrong_recorded_suffix_rejected(self):
        with self.assertRaises(EquivalenceError):compare_bytes(fixture(),fixture(SUFFIX),'',SUFFIX+' -fextra')
    def test_missing_producer_allowance_rejected(self):
        with self.assertRaises(EquivalenceError):compare_bytes(fixture(),fixture(SUFFIX))
    def test_truncated_container_rejected(self):
        with self.assertRaises(EquivalenceError):compare_bytes(fixture(),fixture(SUFFIX)[:-1],'',SUFFIX)
    def test_nonderived_section_offsets_rejected(self):
        for index in (0,3,4):
            with self.subTest(section_index=index):
                raw=bytearray(fixture(SUFFIX));shoff=struct.unpack_from('<I',raw,32)[0]
                at=shoff+40*index+16;old=struct.unpack_from('<I',raw,at)[0];struct.pack_into('<I',raw,at,old+1)
                with self.assertRaises(EquivalenceError):compare_bytes(fixture(),bytes(raw),'',SUFFIX)
    def test_unchanged_invalid_relocation_context_rejected(self):
        # Same invalid relocation bytes on both sides must still fail, rather
        # than merely being rejected by the exact-section comparison.
        for mutation in ('reloc-symbol-value','reloc-type','reloc-addend','reloc-missing','reloc-overlap','pdiff-overlap','unsupported-debug-section'):
            with self.subTest(mutation=mutation):
                with self.assertRaises(EquivalenceError):compare_bytes(fixture(change=mutation),fixture(SUFFIX,mutation),'',SUFFIX)
if __name__=='__main__':unittest.main()
