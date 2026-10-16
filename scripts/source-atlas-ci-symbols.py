#!/usr/bin/env python3
"""Generate build-qualified Clang or Tree-sitter indexes; upload in bounded batches.
Install: pip install libclang tree-sitter tree-sitter-c tree-sitter-cpp
Uses the repository checkout and ACTUAL compile_commands.json; never invents flags.
"""
import argparse, hashlib, json, os, pathlib, shlex, subprocess, urllib.request, uuid
p=argparse.ArgumentParser();p.add_argument('--compile-commands',required=True);p.add_argument('--repository',required=True);p.add_argument('--root',default='.');p.add_argument('--engine',choices=['clang','tree-sitter'],default='clang');p.add_argument('--source-set-version');p.add_argument('--upload',action='store_true');p.add_argument('--output',default='symbols.json');p.add_argument('--libclang');p.add_argument('--target');p.add_argument('--map-copies',action='store_true',help='Map byte-identical generated copies back to unique tracked files; coverage remains partial');a=p.parse_args()
root=pathlib.Path(a.root).resolve();sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
if subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=root,text=True).strip():raise SystemExit('Refusing an index from a dirty checkout')
raw=pathlib.Path(a.compile_commands).read_bytes();commands=json.loads(raw);config=hashlib.sha256(raw+b'\0'+(a.target or '').encode()).hexdigest()
if not commands:raise SystemExit('The compilation database is empty')
entries=[];failed=[];indexed=0
copies={}
if a.map_copies:
    for name in subprocess.check_output(['git','ls-files','-z'],cwd=root).decode().split('\0'):
        path=root/name
        if name and path.is_file():copies.setdefault(hashlib.sha256(path.read_bytes()).hexdigest(),[]).append(name)
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
            if a.target:filtered+=['--target='+a.target]
            tu=cindex.Index.create().parse(str(file),args=filtered,options=cindex.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD)
            errors=[str(d) for d in tu.diagnostics if d.severity>=cindex.Diagnostic.Error]
            if errors:raise RuntimeError('; '.join(errors)[:2000])
            local=[]
            stack=[(tu.cursor,None)]
            functions={cindex.CursorKind.FUNCTION_DECL,cindex.CursorKind.CXX_METHOD,cindex.CursorKind.CONSTRUCTOR,cindex.CursorKind.DESTRUCTOR,cindex.CursorKind.FUNCTION_TEMPLATE,cindex.CursorKind.CONVERSION_FUNCTION}
            while stack:
                c,caller=stack.pop()
                enclosing=c if c.kind in functions and c.is_definition() else caller
                stack.extend((child,enclosing) for child in reversed(list(c.get_children())))
                if not c.location.file or not c.spelling:continue
                path=source_path(c.location.file.name)
                if path is None:continue
                kind='definition' if c.is_definition() else 'declaration' if c.kind.is_declaration() else 'reference' if c.kind.is_reference() or c.kind in [cindex.CursorKind.DECL_REF_EXPR,cindex.CursorKind.MEMBER_REF_EXPR,cindex.CursorKind.CALL_EXPR] else None
                if not kind:continue
                referenced=c.referenced or c
                entry=dict(symbol=referenced.spelling or c.spelling,usr=referenced.get_usr(),path=path,line=c.location.line,kind=kind)
                if kind=='reference':
                    entry.update(reference_kind='call' if c.kind==cindex.CursorKind.CALL_EXPR else 'other',caller=caller.spelling if caller else None,caller_usr=caller.get_usr() if caller else None)
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
base=dict(repository=a.repository,commit=sha,engine=a.engine,configuration_hash=config,build_configuration=pathlib.Path(a.compile_commands).name+(' target='+a.target if a.target else ''),source_set_version=a.source_set_version,coverage=coverage)
pathlib.Path(a.output).write_text(json.dumps(dict(**base,entries=entries),indent=2))
if a.upload:
    if not indexed:raise SystemExit('No translation units parsed successfully; refusing an empty compiler index. Inspect failed_units in output.')
    index_id=str(uuid.uuid4());batches=[entries[i:i+1000] for i in range(0,len(entries),1000)] or [[]]
    for i,batch in enumerate(batches):
        payload=dict(operation='submit_symbol_index',arguments=dict(**base,index_id=index_id,batch_id=str(i),final=i==len(batches)-1,entries=batch))
        req=urllib.request.Request(os.environ['ATLAS_URL'].rstrip('/')+'/api/worker',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+os.environ['INDEX_WORKER_TOKEN'],'OAI-Sites-Authorization':'Bearer '+os.environ['SITES_SERVICE_TOKEN']})
        with urllib.request.urlopen(req,timeout=90) as r:print(r.read().decode())
print(json.dumps(dict(commit=sha,configuration_hash=config,entries=len(entries),coverage=coverage)))
