"""Fail-closed ELF32/Xtensa DWARF4 provenance equivalence; never raw identity.

No object is rewritten. Only exact recorded plugin suffixes on DW_AT_producer,
and proven DW_FORM_strp layout operands, may differ. Unsupported layouts fail.
"""
import collections, hashlib, pathlib, struct

class EquivalenceError(ValueError): pass

def need(ok, message):
    if not ok: raise EquivalenceError(message)
def digest(b): return hashlib.sha256(b).hexdigest()
def uleb(b, p):
    value=0; shift=0
    while True:
        need(p<len(b) and shift<64, 'Invalid ULEB128')
        x=b[p];p+=1;value|=(x&127)<<shift
        if x<128:return value,p
        shift+=7

def cstring(b, p):
    need(0<=p<len(b), 'String offset outside table')
    q=b.find(b'\0',p);need(q>=0,'Unterminated string')
    return b[p:q],q+1

def elf(raw):
    need(len(raw)>=52 and raw[:7]==b'\x7fELF\x01\x01\x01','Expected ELF32 little-endian')
    h=struct.unpack_from('<16sHHIIIIIHHHHHH',raw)
    need(h[1]==1 and h[2]==94 and h[3]==1 and h[4]==0 and h[5]==0 and h[8]==52 and h[10]==0,'Expected Xtensa relocatable ELF without segments')
    need(h[11]==40 and 0<h[12]<0xff00 and h[13]<h[12], 'Unsupported section table')
    need(h[6]+h[12]*40==len(raw),'Trailing bytes or invalid section table')
    headers=[struct.unpack_from('<10I',raw,h[6]+i*40) for i in range(h[12])]
    need(headers[0]==(0,)*10,'Noncanonical NULL section')
    names_header=headers[h[13]];need(names_header[1]==3,'Missing section-name string table')
    names=raw[names_header[4]:names_header[4]+names_header[5]]; sections={};occupied=[]
    for i,s in enumerate(headers):
        name=cstring(names,s[0])[0].decode('ascii')
        # GCC C++ emits multiple SHT_GROUP sections named .group. Preserve each
        # ordered section index; never merge their identities by shared name.
        need(not name.startswith('.group@'),'Reserved structural key collision')
        key='.group@'+str(i) if name=='.group' and s[1]==17 else name
        need(key not in sections,'Duplicate section name')
        need((name=='.group')==(s[1]==17),'Unsupported group section identity')
        need(s[8]==0 or s[8]&(s[8]-1)==0,'Invalid section alignment')
        need(s[4]<=h[6] and (s[1]==8 or s[4]+s[5]<=h[6]),'Section outside payload region')
        content=raw[s[4]:s[4]+s[5]] if s[1]!=8 else b''
        sections[key]={'index':i,'name':name,'header':s,'data':content}
        if content and i:occupied.append((s[4],s[4]+s[5],max(1,s[8]),key))
    grouped=set();signatures=set()
    for section in sections.values():
        s=section['header']
        if s[1]!=17:continue
        need(s[2]==0 and s[8]==4 and s[9]==4 and len(section['data'])>=8 and len(section['data'])%4==0,'Invalid group encoding')
        words=struct.unpack('<'+'I'*(len(section['data'])//4),section['data'])
        need(words[0]==1 and 0<s[6]<len(headers),'Expected COMDAT group and symbol linkage')
        symbols=headers[s[6]];need(symbols[1]==2 and symbols[9]==16 and 0<s[7]<symbols[5]//16,'Invalid COMDAT signature symbol')
        need(0<symbols[6]<len(headers) and headers[symbols[6]][1]==3,'Invalid COMDAT signature string table')
        signature=struct.unpack_from('<IIIBBH',raw,symbols[4]+s[7]*16)
        string_header=headers[symbols[6]];symbol_strings=raw[string_header[4]:string_header[4]+string_header[5]]
        signature_name=cstring(symbol_strings,signature[0])[0]
        need(signature_name and signature_name not in signatures,'Ambiguous COMDAT signature');signatures.add(signature_name)
        for member in words[1:]:
            need(0<member<len(headers) and member not in grouped and headers[member][2]&0x200,'Invalid or ambiguous group membership')
            grouped.add(member)
    need(grouped=={i for i,s in enumerate(headers) if s[2]&0x200},'Orphan group-flagged section')
    end=52; order=[]
    for start,stop,alignment,name in sorted(occupied):
        need(start==(end+alignment-1)//alignment*alignment,'Unexpected gap, overlap or section order')
        need(not any(raw[end:start]),'Nonzero section padding')
        end=stop;order.append(name)
    need(h[6]==(end+3)//4*4 and not any(raw[end:h[6]]),'Unexpected section-table padding')
    return h,sections,order

def abbreviations(raw,offset):
    result={};p=offset
    while True:
        code,p=uleb(raw,p)
        if code==0:return result
        need(code not in result,'Duplicate abbreviation')
        tag,p=uleb(raw,p);need(p<len(raw),'Missing child flag');children=raw[p];p+=1
        need(children in (0,1),'Invalid child flag');attrs=[]
        while True:
            attr,p=uleb(raw,p);form,p=uleb(raw,p)
            if attr==0 and form==0:break
            need(attr!=0 and form!=0,'Invalid abbreviation attribute')
            attrs.append((attr,form))
        result[code]=(tag,children,attrs)

def dwarf_strp(info,abbrev,strings):
    """Return exact strp operand positions, attribute codes, and resolved bytes."""
    p=0;result=[];units=0
    fixed={0x01:4,0x05:2,0x06:4,0x07:8,0x0b:1,0x0c:1,0x0e:4,
           0x10:4,0x11:1,0x12:2,0x13:4,0x14:8,0x17:4,0x19:0,0x20:8}
    while p<len(info):
        need(p+11<=len(info),'Incomplete CU header');length=struct.unpack_from('<I',info,p)[0];end=p+4+length
        need(length<0xfffffff0 and end<=len(info),'Unsupported DWARF unit size')
        version,offset,address_size=struct.unpack_from('<HIB',info,p+4)
        need(version==4 and address_size==4,'Only DWARF4 with 32-bit addresses is qualified')
        table=abbreviations(abbrev,offset);p+=11;depth=0;roots=0;units+=1
        while p<end:
            code,p=uleb(info,p)
            if code==0:
                need(depth>0,'Unmatched DIE terminator');depth-=1;continue
            need(code in table,'Unknown abbreviation');tag,children,attrs=table[code]
            if depth==0:roots+=1;need(roots==1 and tag==0x11,'Expected one compile-unit root')
            for attr,form in attrs:
                start=p
                if form in fixed:p+=fixed[form]
                elif form==0x08:_,p=cstring(info,p)
                elif form in (0x0d,0x0f,0x15):_,p=uleb(info,p)
                elif form in (0x09,0x18):n,p=uleb(info,p);p+=n
                elif form in (0x03,0x04,0x0a):
                    size={0x03:2,0x04:4,0x0a:1}[form];need(p+size<=end,'Incomplete block size')
                    n=int.from_bytes(info[p:p+size],'little');p+=size+n
                else:raise EquivalenceError('Unsupported DWARF form '+hex(form))
                need(p<=end,'Attribute outside CU')
                if attr==0x25:need(form==0x0e and depth==0,'Producer must be root strp')
                if form==0x0e:
                    off=struct.unpack_from('<I',info,start)[0];value,_=cstring(strings,off)
                    result.append((start,attr,off,value))
            depth+=children
        need(p==end and depth==0 and roots==1,'Invalid CU nesting')
    need(units==1,'Only one compile unit is qualified')
    return result

def canonical_debug(sections,suffix):
    for name in ('.debug_info','.debug_abbrev','.debug_str'):need(name in sections,'Missing '+name)
    info=sections['.debug_info']['data'];strings=sections['.debug_str']['data']
    need(strings.endswith(b'\0'),'Incomplete debug string table')
    operands=dwarf_strp(info,sections['.debug_abbrev']['data'],strings)
    supported={'.debug_info','.debug_abbrev','.debug_str','.debug_line','.debug_loc','.debug_aranges','.debug_ranges','.debug_frame'}
    for name in sections:
        need(not name.startswith(('.zdebug','.gnu.linkonce.wi')), 'Unsupported alternate debug section: '+name)
        if name.startswith(('.debug','.rela.debug','.rel.debug')):
            base=name.removeprefix('.rela').removeprefix('.rel')
            need(base in supported,'Unsupported debug section: '+name)
            need(not sections[name]['header'][2]&0x800,'Compressed debug section is unsupported')
    # A raw strp value is meaningful only under this exact relocation profile.
    positions={x[0] for x in operands};relocated=[];byindex={s['index']:s for s in sections.values()}
    for section in sections.values():
        sh=section['header']
        if sh[1] not in (4,9):continue
        need(sh[6] in byindex and sh[7] in byindex,'Invalid relocation linkage')
        symbols=byindex[sh[6]];need(symbols['header'][1]==2 and symbols['header'][9]==16,'Unsupported symbol table')
        step=12 if sh[1]==4 else 8
        need(sh[9]==step and len(section['data'])%step==0,'Invalid relocation entry size')
        for at in range(0,len(section['data']),step):
            offset,rinfo=struct.unpack_from('<II',section['data'],at);symindex=rinfo>>8;rtype=rinfo&255
            need((symindex+1)*16<=len(symbols['data']),'Invalid relocation symbol')
            symbol=struct.unpack_from('<IIIBBH',symbols['data'],symindex*16)
            if sh[7]==sections['.debug_info']['index']:
                # R_XTENSA_32 (1) and R_XTENSA_PDIFF32 (59) both occupy
                # four bytes. PDIFF32 is allowed only outside strp operands.
                need(sh[1]==4 and rtype in (1,59),'Unsupported debug-info relocation width/type')
                if any(offset<pos+4 and pos<offset+4 for pos in positions):
                    need(rtype==1 and offset in positions and symbol[5]==sections['.debug_str']['index'],'Unexpected relocation overlapping strp')
            if symbol[5]!=sections['.debug_str']['index']:continue
            need(sh[1]==4 and rtype==1 and sh[7]==sections['.debug_info']['index'],'Unsupported debug-string relocation')
            addend=struct.unpack_from('<i',section['data'],at+8)[0]
            need(symbol[1]==0 and symbol[2]==0 and symbol[3]==3 and symbol[4]==0 and addend==0,'Nonzero/non-section debug-string relocation')
            need(offset in positions,'Relocation outside parsed strp operand');relocated.append(offset)
    need(collections.Counter(relocated)==collections.Counter(positions),'Missing or duplicate strp relocations')
    producers=[x for x in operands if x[1]==0x25];need(len(producers)==1,'Expected one producer')
    producer=producers[0][3];suffix=suffix.encode('utf-8')
    if suffix:
        need(producer.endswith(b' '+suffix),'Producer lacks exact recorded plugin suffix')
        normalized_producer=producer[:-len(suffix)-1]
    else:normalized_producer=producer
    need(b'-fplugin' not in normalized_producer,'Unexplained plugin producer option')
    pool=strings[:-1].split(b'\0');need(pool.count(producer)==1,'Producer not unique complete string')
    pool[pool.index(producer)]=normalized_producer
    normalized=bytearray(info);resolved=[]
    for pos,attr,offset,value in operands:
        normalized[pos:pos+4]=b'\0'*4
        resolved.append((pos,attr,normalized_producer if attr==0x25 else value))
    return bytes(normalized),resolved,collections.Counter(pool),len(operands)

def compare_bytes(left,right,left_suffix='',right_suffix=''):
    lh,ls,lo=elf(left);rh,rs,ro=elf(right)
    need(lh[:6]+lh[7:]==rh[:6]+rh[7:],'ELF header changed beyond section-table offset')
    need(list(ls)==list(rs) and lo==ro,'Section inventory/order changed')
    differences=[]
    for name,s in ls.items():
        t=rs[name];sh=s['header'];th=t['header']
        # File-backed nonempty sections have offsets derived by elf()'s exact
        # packed layout. NOBITS/empty offsets are not derived: preserve them.
        allowed=({4,5} if name=='.debug_str' else {4}) if s['data'] and t['data'] else set()
        need(all(a==b for i,(a,b) in enumerate(zip(sh,th)) if i not in allowed),'Section metadata changed: '+name)
        if s['data']!=t['data']:differences.append(name)
        if name not in ('.debug_info','.debug_str'):need(s['data']==t['data'],'Non-provenance section changed: '+name)
    if not differences:
        # No debug information in synthetic controls: exact identity is sufficient.
        need(left==right,'Container bytes changed without section changes')
        return {'passed':True,'raw_objects_identical':True,'differing_sections':[],'normalization':'none'}
    l=canonical_debug(ls,left_suffix);r=canonical_debug(rs,right_suffix)
    need(l[0]==r[0],'DWARF bytes changed outside parsed strp operands')
    need(l[1]==r[1],'DWARF attribute identity or resolved string changed')
    need(l[2]==r[2],'Complete debug string pool changed')
    return {'passed':True,'raw_objects_identical':left==right,'differing_sections':differences,'strp_operands_checked':l[3],'normalization':'Exact ordered recorded plugin suffix on sole DW_AT_producer; derived strp offsets and ELF layout only','limitations':['No object rewritten; raw hashes remain distinct','Not whole-program semantic or reference completeness proof','Only ELF32 little-endian Xtensa ET_REL, one DWARF4 CU; unknown layouts fail']}

def compare_files(left,right,left_suffix='',right_suffix=''):
    a=pathlib.Path(left).read_bytes();b=pathlib.Path(right).read_bytes()
    result={'left_sha256':digest(a),'right_sha256':digest(b),'raw_objects_identical':a==b}
    try:result.update(compare_bytes(a,b,left_suffix,right_suffix))
    except (EquivalenceError,ValueError,struct.error) as e:result.update(passed=False,error=str(e))
    return result
