#!/usr/bin/env python3
"""Pass through to the real compiler, recording expanded build commands, including wrapper flags."""
import json, os, pathlib, subprocess, sys, uuid
cc=os.environ['ATLAS_ACTUAL_CC']
args=sys.argv[1:]
sources=[x for x in args if not x.startswith('-') and pathlib.Path(x).suffix in ('.c','.cc','.cpp','.cxx') and pathlib.Path(x).is_file()]
if sources:
    records=pathlib.Path(os.environ['ATLAS_CC_RECORDS']);records.mkdir(parents=True,exist_ok=True)
    units=[]
    for source in sources:
        # One parse entry per TU from the same real multi-input compile/link invocation.
        units.append(dict(directory=os.getcwd(),file=str(pathlib.Path(source).resolve()),arguments=[cc]+[x for x in args if x not in sources or x==source]))
    (records/(str(uuid.uuid4())+'.json')).write_text(json.dumps(units))
raise SystemExit(subprocess.call([cc,*args]))
