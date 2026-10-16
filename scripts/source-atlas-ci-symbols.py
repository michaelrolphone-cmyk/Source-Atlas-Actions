#!/usr/bin/env python3
"""Generate build-qualified Clang or Tree-sitter indexes; upload in bounded batches.
Install: pip install libclang tree-sitter tree-sitter-c tree-sitter-cpp
Uses the repository checkout and ACTUAL compile_commands.json; never invents flags.
"""
import argparse, hashlib, json, os, pathlib, shlex, subprocess, urllib.request, urllib.error, uuid, time
from functools import lru_cache
p=argparse.ArgumentParser();p.add_argument('--compile-commands',required=True);p.add_argument('--repository',required=True);p.add_argument('--root',default='.');p.add_argument('--engine',choices=['clang','tree-sitter'],default='clang');p.add_argument('--source-set-version');p.add_argument('--upload',action='store_true');p.add_argument('--output',default='symbols.json');p.add_argument('--libclang');p.add_argument('--target');p.add_argument('--map-copies',action='store_true',help='Map byte-identical generated copies back to unique tracked files; coverage remains partial');a=p.parse_args()
root=pathlib.Path(a.root).resolve();sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
if subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=root,text=True).strip():raise SystemExit('Refusing an index from a dirty checkout')
raw=pathlib.Path(a.compile_commands).read_bytes();commands=json.loads(raw);config=hashlib.sha256(raw+b'\0'+(a.target or '').encode()+b'\0parser-diagnostics=-Wno-error').hexdigest()
if not commands:raise SystemExit('The compilation database is empty')
entries=[];failed=[];indexed=0
copies={}
if a.map_copies:
    for name in subprocess.check_output(['git','ls-files','-z'],cwd=root).decode().split('\0'):
        path=root/name
        if name and path.is_file():copies.setdefault(hashlib.sha256(path.read_bytes()).hexdigest(),[]).append(name)
@lru_cache(maxsize=10000)
def source_path(path):
    path=pathlib.Path(path).resolve()
    try:return str(path.relative_to(root))
    except ValueError:
        if not a.map_copies or not path.is_file():return None
        matches=copies.get(hashlib.sha256(path.read_bytes()).hexdigest(),[])
        return matches[0] if len(matches)==1 else None
if a.engine=='clang':
    from clang import cindex
    if a.libclang:cindex.Config.set_library_file(a.libclang)
for unit in commands:
    directory=pathlib.Path(unit['directory']);file=pathlib.Path(unit['file']);file=file if file.is_absolute() else directory/file
    try:
        file=file.resolve()
        if a.engine=='clang':
            from clang import cindex
            args=unit.get('arguments') or shlex.split(unit['command']);args=args[1:]
            # Remove only compile-only/output arguments. All target defines/includes are retained.
            filtered=[];i=0
            while i<len(args):
                if args[i] in ['-o','-MF','-MT','-MQ']:i+=2;continue
                if args[i] in ['-c','-MD','-MMD','-MP']:i+=1;continue
                if args[i] in ['-shared','-nostartfiles'] or args[i].startswith(('-Wl,','-l')):i+=1;continue
                candidate=(directory/args[i]).resolve() if not args[i].startswith('-') else None
                if candidate==file:i+=1;continue
                filtered.append(args[i]);i+=1
            filtered+=['-working-directory='+str(directory)]
            # GCC's warning-as-error policy must not turn Clang-only style/codegen
            # warnings into missing ASTs. This changes diagnostics, not target,
            # macros/includes or the byte-verified build. Actual parse errors remain errors.
            filtered+=['-Wno-error']
            if a.target:filtered+=['--target='+a.target]
            tu=cindex.Index.create().parse(str(file),args=filtered,options=cindex.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD)
            errors=[str(d) for d in tu.diagnostics if d.severity>=cindex.Diagnostic.Error]
            if errors:raise RuntimeError('; '.join(errors)[:2000])
            local=[]
            stack=[(tu.cursor,None,None)]
            functions={cindex.CursorKind.FUNCTION_DECL,cindex.CursorKind.CXX_METHOD,cindex.CursorKind.CONSTRUCTOR,cindex.CursorKind.DESTRUCTOR,cindex.CursorKind.FUNCTION_TEMPLATE,cindex.CursorKind.CONVERSION_FUNCTION}
            entity_kinds={'NAMESPACE':'namespace','CLASS_DECL':'class','STRUCT_DECL':'struct','UNION_DECL':'union','ENUM_DECL':'enum','ENUM_CONSTANT_DECL':'enum_value','FUNCTION_DECL':'function','CXX_METHOD':'method','CONSTRUCTOR':'constructor','DESTRUCTOR':'destructor','FUNCTION_TEMPLATE':'template','CONVERSION_FUNCTION':'method','FIELD_DECL':'field','VAR_DECL':'variable','PARM_DECL':'parameter','TYPEDEF_DECL':'typedef','TYPE_ALIAS_DECL':'typedef','CLASS_TEMPLATE':'class'}
            def qualified(cursor):
                parts=[];seen=0
                while cursor and cursor.kind!=cindex.CursorKind.TRANSLATION_UNIT and seen<30:
                    if cursor.spelling:parts.append(cursor.spelling)
                    cursor=cursor.semantic_parent;seen+=1
                return '::'.join(reversed(parts))[:1000]
            def peeled_type(cursor):
                t=cursor.type.get_canonical()
                for _ in range(8):
                    if t.kind in [cindex.TypeKind.POINTER,cindex.TypeKind.LVALUEREFERENCE,cindex.TypeKind.RVALUEREFERENCE]:t=t.get_pointee().get_canonical()
                    else:break
                return t
            while stack:
                c,caller,owner=stack.pop()
                enclosing=c if c.kind in functions and c.is_definition() else caller
                entity_kind=entity_kinds.get(c.kind.name)
                owning=c if entity_kind and c.kind.is_declaration() else owner
                children=list(c.get_children())
                stack.extend((child,enclosing,owning) for child in reversed(children))
                if not c.location.file or not c.spelling:continue
                path=source_path(c.location.file.name)
                if path is None:continue
                kind='definition' if c.is_definition() else 'declaration' if c.kind.is_declaration() else 'reference' if c.kind.is_reference() or c.kind in [cindex.CursorKind.DECL_REF_EXPR,cindex.CursorKind.MEMBER_REF_EXPR,cindex.CursorKind.CALL_EXPR] else None
                if not kind:continue
                referenced=c.referenced or c
                entry=dict(symbol=referenced.spelling or c.spelling,usr=referenced.get_usr(),path=path,line=c.location.line,kind=kind)
                if kind=='reference':
                    entry.update(reference_kind='call' if c.kind==cindex.CursorKind.CALL_EXPR else 'other',caller=caller.spelling if caller else None,caller_usr=caller.get_usr() if caller else None)
                target_kind=entity_kinds.get(referenced.kind.name)
                if target_kind:
                    parent=c.semantic_parent if kind!='reference' else referenced.semantic_parent
                    t=peeled_type(referenced);td=t.get_declaration()
                    call=c.kind==cindex.CursorKind.CALL_EXPR
                    actual_owner=caller if call and caller else owner
                    relation='invokes_callback' if call and referenced.kind in [cindex.CursorKind.FIELD_DECL,cindex.CursorKind.VAR_DECL] else 'calls' if call else 'inherits' if c.kind==cindex.CursorKind.CXX_BASE_SPECIFIER else 'uses_type' if c.kind in [cindex.CursorKind.TYPE_REF,cindex.CursorKind.TEMPLATE_REF] else 'initializer_reference' if owner and owner.kind==cindex.CursorKind.VAR_DECL and referenced.kind in functions else 'references'
                    entry.update(entity_kind=target_kind,qualified_name=qualified(referenced),signature=referenced.type.spelling[:1000],semantic_parent_usr=parent.get_usr() if parent else '',semantic_parent_name=parent.spelling if parent else '',owner_usr=actual_owner.get_usr() if actual_owner else '',owner_symbol=actual_owner.spelling if actual_owner else '',type_usr=td.get_usr() if td and td.kind!=cindex.CursorKind.NO_DECL_FOUND else '',type_spelling=t.spelling[:1000],end_line=max(c.location.line,c.extent.end.line),callable=t.kind in [cindex.TypeKind.FUNCTIONPROTO,cindex.TypeKind.FUNCTIONNOPROTO],relation_kind=relation)
                    if kind!='reference' and c.kind in [cindex.CursorKind.STRUCT_DECL,cindex.CursorKind.CLASS_DECL]:
                        entry['callback_contract']=any(child.kind==cindex.CursorKind.FIELD_DECL and peeled_type(child).kind in [cindex.TypeKind.FUNCTIONPROTO,cindex.TypeKind.FUNCTIONNOPROTO] for child in children)
                local.append(entry)

        else:
            file.relative_to(root)
            from tree_sitter import Language,Parser
            import tree_sitter_c,tree_sitter_cpp
            parser=Parser(Language(tree_sitter_c.language() if file.suffix=='.c' else tree_sitter_cpp.language()))
            raw_source=file.read_bytes();tree=parser.parse(raw_source)
            if tree.root_node.has_error:raise RuntimeError('Tree-sitter syntax errors')
            local=[]
            def walk(node):
                if node.type=='function_definition':
                    decl=node.child_by_field_name('declarator');ident=decl
                    while ident and ident.type not in ('identifier','field_identifier','operator_name'):
                        ident=ident.child_by_field_name('declarator') or (ident.named_children[0] if ident.named_children else None)
                    if ident:local.append(dict(symbol=raw_source[ident.start_byte:ident.end_byte].decode(),usr='',path=str(file.relative_to(root)),line=ident.start_point.row+1,kind='definition'))
                elif node.type in ('struct_specifier','class_specifier','enum_specifier'):
                    ident=node.child_by_field_name('name')
                    if ident:local.append(dict(symbol=raw_source[ident.start_byte:ident.end_byte].decode(),usr='',path=str(file.relative_to(root)),line=ident.start_point.row+1,kind='definition' if node.child_by_field_name('body') else 'declaration'))
                for child in node.named_children:walk(child)
            walk(tree.root_node)
        entries.extend(local);indexed+=1
    except Exception as e:failed.append(str(file)+': '+str(e))
unique={json.dumps(e,sort_keys=True):e for e in entries};entries=list(unique.values())
coverage=dict(total_units=len(commands),indexed_units=indexed,failed_units=failed,complete=indexed==len(commands))
if a.map_copies:
    coverage['complete']=False
    coverage['failed_units'].append('Generated copies mapped by unique byte identity; generated-only sources and ambiguous copies are not covered by canonical Git source. No complete-reference claim.')
base=dict(repository=a.repository,commit=sha,engine=a.engine,configuration_hash=config,build_configuration=pathlib.Path(a.compile_commands).name+(' target='+a.target if a.target else '')+'; parser diagnostics: -Wno-error',source_set_version=a.source_set_version,coverage=coverage,**({'architecture_schema':1} if a.engine=='clang' else {}))
pathlib.Path(a.output).write_text(json.dumps(dict(**base,entries=entries),indent=2))
if a.upload:
    if not indexed:raise SystemExit('No translation units parsed successfully; refusing an empty compiler index. Inspect failed_units in output.')
    index_id=str(uuid.uuid5(uuid.NAMESPACE_URL,a.repository+'@'+sha+':'+config+':'+str(a.source_set_version)+':architecture-v1'));batches=[entries[i:i+1000] for i in range(0,len(entries),1000)] or [[]]
    for i,batch in enumerate(batches):
        payload=dict(operation='submit_symbol_index',arguments=dict(**base,index_id=index_id,batch_id=str(i),final=i==len(batches)-1,entries=batch))
        req=urllib.request.Request(os.environ['ATLAS_URL'].rstrip('/')+'/api/worker',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+os.environ['INDEX_WORKER_TOKEN'],'OAI-Sites-Authorization':'Bearer '+os.environ['SITES_SERVICE_TOKEN']})
        for attempt in range(8):
            try:
                with urllib.request.urlopen(req,timeout=90) as r:print(r.read().decode())
                break
            except urllib.error.HTTPError as e:
                try:details=json.loads(e.read())
                except (ValueError,UnicodeError):details={}
                if attempt==7 or not (details.get('waiting') or e.code>=500):raise
                from datetime import datetime
                deadline=details.get('next_retry_at')
                delay=max(1,datetime.fromisoformat(deadline.replace('Z','+00:00')).timestamp()-time.time()) if deadline else min(30,2**attempt)
                if delay>120:raise RuntimeError('Upload waiting exceeds CI retry window; re-run with the same stable index ID after '+str(deadline))
                time.sleep(delay+0.25)
print(json.dumps(dict(commit=sha,configuration_hash=config,entries=len(entries),coverage=coverage)))
