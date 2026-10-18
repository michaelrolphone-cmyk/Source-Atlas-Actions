#!/usr/bin/env python3
"""Two exact translation units, GCC-native observations, never a production index."""
import argparse, hashlib, json, pathlib, re, runpy, shlex, shutil, subprocess, sys
HERE = pathlib.Path(__file__).resolve().parent
C_UNIT = 'lib/elf_loader/src/esp_privileged_os_cpu.c'
CPP_UNIT = 'src/runtime/serial/Files.cpp'
SHA_COMMANDS = 'e77a0c9b1fb905316f0949766ede894f01bc705b26639a7296eb38e81f1a2cd5'

def sha(p): return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
def write(p, data): pathlib.Path(p).write_text(json.dumps(data, indent=2)+'\n')
def require(ok, msg):
    if not ok: raise RuntimeError(msg)
def execute(args, cwd, log):
    p = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    pathlib.Path(log).write_text(p.stdout)
    require(p.returncode == 0, f'Compiler failed ({p.returncode}); see {log}')
    return p.stdout

def redirect(original, output):
    require(original.count('-o') == 1 and original.count('-c') == 1, 'Expected one compile/output operand')
    require(not any(a.startswith(('-fplugin', '-save-temps', '-dump', '-specs')) for a in original), 'Unexpected compiler side effects')
    # Exact reviewed commands have no dependency-generation outputs. Fail rather than silently drop any.
    require(not any(a.startswith(('-MF', '-MT', '-MQ', '-MD', '-MMD')) for a in original), 'Unexpected dependency output')
    result = list(original); i = result.index('-o')+1
    change = {'argument_index': i, 'original': result[i], 'effective': str(output), 'purpose': 'isolated object output'}
    result[i] = str(output)
    require(all(a == b for n,(a,b) in enumerate(zip(original,result)) if n != i), 'Semantic argument changed')
    return result, change

def records(p):
    r=[json.loads(x) for x in pathlib.Path(p).read_text().splitlines()]
    require(r and r[0]['event']=='metadata' and r[-1]['event']=='summary', 'Incomplete plugin output')
    require(r[-1]['semantic_coverage_complete'] is False, 'Unqualified complete coverage claim')
    return r

def normalized(rows):
    # Only diagnostic process-local IDs are removed. Locations, names, kinds, ordering and counts are retained.
    def norm(v):
        if isinstance(v, dict):
            return {k:norm(x) for k,x in v.items() if k not in ('uid','caller_uid')}
        if isinstance(v, list): return [norm(x) for x in v]
        if isinstance(v, str): return re.sub(r'<T[0-9a-f]+>', '<Tlocal>', v)
        return v
    return norm(rows)

def validate_common(rows, root, unit):
    gates=[]
    def gate(name, ok, value): gates.append({'gate':name,'passed':bool(ok),'observed':value})
    defs={x['uid']:x for x in rows if x['event']=='definition'}
    calls=[x for x in rows if x['event'] in ('direct_call','unresolved_indirect_call')]
    gate('callers_have_definition_provenance', all(x['caller_uid'] in defs for x in calls),len(calls))
    gate('direct_targets_are_function_declarations', all(x['target']['tree_code']=='function_decl' for x in calls if x['event']=='direct_call'),len(calls))
    bad=[]
    for x in calls:
        loc=x['location']; p=root/loc['path']
        if not loc['path'] or not p.is_file() or loc['line']<1 or loc['column']<1: bad.append(loc); continue
        lines=p.read_text(errors='replace').splitlines()
        if loc['line']>len(lines) or loc['column']>len(lines[loc['line']-1])+1: bad.append(loc)
    gate('emitted_calls_have_valid_source_coordinates',not bad,bad[:10])
    gate('unsupported_categories_are_explicit',len(rows[-1]['unsupported'])>=10,rows[-1]['unsupported'])
    gate('whole_release_coverage_never_claimed',rows[-1]['semantic_coverage_complete'] is False,False)
    main=[x for x in rows if x.get('location',{}).get('path')==unit]
    direct=[x for x in main if x['event']=='direct_call']
    indirect=[x for x in main if x['event']=='unresolved_indirect_call']
    def matches(line,target): return [x for x in direct if x['location']['line']==line and x['target']['symbol']==target]
    if unit==C_UNIT:
        main_defs=[x for x in main if x['event']=='definition' and not x['artificial']]
        gate('c_18_handwritten_definitions',len(main_defs)==18,[{'symbol':x['symbol'],'line':x['location']['line']} for x in main_defs])
        for line,target in [(31,'risc_provider_diagnostic_abi_v1'),(106,'begin_scope'),(112,'esp_elf_privileged_os_cpu_begin_selected_diagnostics_v1'),(127,'begin_scope'),(279,'esp_elf_privileged_os_cpu_lookup_current'),(284,'esp_elf_privileged_os_cpu_lookup_current')]:
            gate(f'c_direct_{line}_{target}',len(matches(line,target))==1,len(matches(line,target)))
        gate('c_weak_tests_and_addresses_are_not_calls',not any(x['location']['line'] in (29,30,250,251,252) and x['target']['symbol'].startswith('risc_provider_diagnostic_') for x in direct),[x['target']['symbol'] for x in direct if x['location']['line'] in (29,30,250,251,252)])
        aliases=[x for x in rows if x.get('symbol','').startswith('risc_os_cpu_link_')]
        gate('c_assembler_aliases_remain_variable_entities',bool(aliases) and all(x['tree_code']=='var_decl' for x in aliases),len(aliases))
    elif unit==CPP_UNIT:
        methods=[x for x in main if x['event']=='definition' and x['symbol']=='shutdown']
        gate('cpp_files_method_qualification',len(methods)==1 and methods[0]['qualified_name']=='RiscSerial::Files::shutdown',[x['qualified_name'] for x in methods])
        for target in ('disconnect','checkpoint','exitSafe'):
            gate('cpp_shutdown_'+target,len(matches(92,target))==1,len(matches(92,target)))
        for line,target in [(48,'outputSize'),(110,'number')]:
            hits=matches(line,target)
            gate(f'cpp_same_line_occurrences_{line}_{target}',len(hits)==2 and len({x['location']['column'] for x in hits})==2,[x['location'] for x in hits])
            gate(f'cpp_same_target_identity_{line}_{target}',len(hits)==2 and len({x['target']['uid'] for x in hits})==1,[x['target']['uid'] for x in hits])
        for line in (46,48,111):
            gate(f'cpp_indirect_callback_{line}',any(x['location']['line']==line for x in indirect),[x['location'] for x in indirect if x['location']['line']==line])
    return {'unit':unit,'gates':gates,'passed':all(x['passed'] for x in gates),'summary':rows[-1]}

def run_unit(entry,root,plugin,out,label):
    source=pathlib.Path(entry['file']); before=sha(source)
    original=shlex.split(entry['command']); effective,change=redirect(original,out/(label+'.o'))
    receipt={'unit':str(source.relative_to(root)),'original_entry':entry,'original_argv':original,'cwd':entry['directory'],'path_mapping':{},'output_redirection':change,'source_sha256_before':before,'runs':[]}
    write(out/(label+'-commands.json'),receipt)
    object_hashes=[]; parsed=[]
    for attempt in ('baseline','instrumented-1','instrumented-2'):
        args=list(effective); events=out/(label+'-'+attempt+'.jsonl')
        if attempt!='baseline':
            args += ['-fplugin='+str(plugin),'-fplugin-arg-atlas_gcc_proof-output='+str(events),'-fplugin-arg-atlas_gcc_proof-root='+str(root),'-fplugin-arg-atlas_gcc_proof-unit='+receipt['unit']]
        run={'attempt':attempt,'effective_argv':args,'log':str(out/(label+'-'+attempt+'.log'))}
        receipt['runs'].append(run); write(out/(label+'-commands.json'),receipt)
        execute(args,entry['directory'],run['log'])
        run['exit_code']=0; run['object_sha256']=sha(out/(label+'.o'));object_hashes.append(run['object_sha256'])
        shutil.copy2(out/(label+'.o'),out/(label+'-'+attempt+'.o'))
        if attempt!='baseline': parsed.append(records(events));run['events_sha256']=sha(events)
        write(out/(label+'-commands.json'),receipt)
    receipt.update(source_sha256_after=sha(source),source_unchanged=sha(source)==before,objects_identical=len(set(object_hashes))==1,normalized_events_repeatable=normalized(parsed[0])==normalized(parsed[1]))
    receipt['validation']=validate_common(parsed[0],root,receipt['unit'])
    receipt['passed']=all((receipt['source_unchanged'],receipt['objects_identical'],receipt['normalized_events_repeatable'],receipt['validation']['passed']))
    write(out/(label+'-commands.json'),receipt)
    require(receipt['passed'],f'{label} bounded fidelity gates failed; inspect receipt')
    return receipt

def controls(toolchain,plugin,out):
    result=[]
    for ext,expected_defs,expected_calls,expected_indirect in [('c',3,2,1),('cpp',4,2,0)]:
        src=HERE/('control.'+ext); compiler=toolchain/'bin'/('xtensa-esp32s3-elf-'+('gcc' if ext=='c' else 'g++'))
        entry={'file':str(src),'directory':str(HERE),'command':shlex.join([str(compiler),'-c',str(src),'-o','unused.o'])}
        proof=run_unit(entry,HERE,plugin,out,'control-'+ext)
        rows=records(out/('control-'+ext+'-instrumented-1.jsonl')); summary=rows[-1]
        require((summary['definitions'],summary['direct_calls'],summary['unresolved_indirect_calls'])==(expected_defs,expected_calls,expected_indirect),'Control count mismatch')
        calls=[x for x in rows if x['event']=='direct_call']
        require(all(x['location']['line']!=x['target']['location']['line'] for x in calls),'Declaration used as callsite')
        if ext=='cpp':
            require(calls[0]['target']['qualified_name']=='Proof::callee' and calls[0]['target']['location']['line']==2,'Wrong overload')
            require(calls[1]['target']['qualified_name']=='Proof::Box::work','Wrong member context')
        result.append(proof)
    write(out/'controls.json',{'passed':True,'units':result})

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--toolchain',required=True);ap.add_argument('--output',required=True);ap.add_argument('--released',action='store_true');args=ap.parse_args()
    t=pathlib.Path(args.toolchain).resolve();out=pathlib.Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
    plugin=out/'atlas_gcc_proof.so'; source=HERE/'source-atlas-gcc-proof.cc';inc=t/'lib/gcc/xtensa-esp-elf/15.2.0/plugin/include'
    build=['c++','-std=gnu++17','-shared','-fPIC','-fno-rtti','-O0','-g','-I'+str(inc),str(source),'-o',str(plugin)]
    execute(build,HERE,out/'plugin-build.log')
    provenance={'plugin_source_sha256':sha(source),'plugin_binary_sha256':sha(plugin),'build_command':build,'host_compiler_version':subprocess.check_output(['c++','--version'],text=True),'host_compiler_sha256':sha(pathlib.Path(shutil.which('c++')).resolve()),'toolchain_files':{str(p.relative_to(t)):sha(p) for p in [t/'bin/xtensa-esp-elf-gcc',t/'bin/xtensa-esp-elf-g++',t/'bin/xtensa-esp32s3-elf-gcc',t/'bin/xtensa-esp32s3-elf-g++',t/'libexec/gcc/xtensa-esp-elf/15.2.0/cc1',t/'libexec/gcc/xtensa-esp-elf/15.2.0/cc1plus']},'plugin_headers':{str(p.relative_to(inc)):sha(p) for p in sorted(inc.rglob('*')) if p.is_file()},'production_upload':False,'semantic_coverage_complete':False}
    write(out/'plugin-provenance.json',provenance)
    controls(t,plugin,out)
    if not args.released:return
    helper=runpy.run_path(str(HERE.parent/'scripts/source-atlas-wave063-inputs.py'))
    q,raw,_=helper['read_release'](out.parent/'release.zip'); require(hashlib.sha256(raw).hexdigest()==SHA_COMMANDS,'Wrong original commands')
    require(raw==(out.parent/'original-compile_commands.json').read_bytes(),'Regenerated original command bytes differ')
    helper['verify_sources'](q)
    root=helper['ROOT']/'sources/runtime';units=json.loads(raw);results=[]
    for label,unit in [('released-c',C_UNIT),('released-cpp',CPP_UNIT)]:
        entries=[x for x in units if x['file']==str(root/unit)];require(len(entries)==1,'Missing or duplicate exact unit')
        result=run_unit(entries[0],root,plugin,out,label);results.append(result)
        helper['verify_sources'](q)
    report={'schema':'gcc-native-two-unit-proof/1','source_set_version':helper['SET_VERSION'],'source_pins':helper['PINS'],'original_commands_sha256':SHA_COMMANDS,'selected_units':2,'release_runtime_units':68,'selected_units_compiled':2,'fidelity_gates_passed':True,'production_upload':False,'whole_release_coverage_complete':False,'baseline_instrumented_objects_identical':all(x['objects_identical'] for x in results),'results':results}
    write(out/'proof-summary.json',report)
    print(json.dumps({k:v for k,v in report.items() if k!='results'}))
if __name__=='__main__': main()
