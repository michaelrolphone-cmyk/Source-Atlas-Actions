#!/usr/bin/env python3
"""Verify released Wave .63 inputs and prepare actual Runtime compiler commands.

This does not build firmware. The only executed build-system command is the
release's recorded IDF reconfigure command. Original command bytes are retained.
"""
import argparse
import hashlib
import json
import pathlib
import shlex
import subprocess
import zipfile

ROOT = pathlib.Path('/__w/_temp/wave-serial-logging-native')
SET_VERSION = '56cd4156-61e1-406e-81f5-f188cb3969df'
ARCHIVE_SHA = '4e4215d79a16779be0e88e2a947b0c71ef57b1c9e250d020c8c7f2954b940e52'
COMMANDS_SHA = 'e77a0c9b1fb905316f0949766ede894f01bc705b26639a7296eb38e81f1a2cd5'
PINS = {'runtime': '0aab2fd3b03b4190d638e23c79a465afeecf5dfb',
        'product': 'f42a6e07133c599a22acfaa8b8a0672ff996dc1b',
        'sdk': 'fff9895c82d744c7237be8847347bdd1b07c6643'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def check_file(path, expected):
    path = pathlib.Path(path)
    require(path.is_file(), 'Missing input: ' + str(path))
    require(sha(path.read_bytes()) == expected['sha256'], 'Input hash mismatch: ' + str(path))


def read_release(path):
    require(sha(pathlib.Path(path).read_bytes()) == ARCHIVE_SHA, 'Release archive hash mismatch')
    with zipfile.ZipFile(path) as archive:
        raw = archive.read('build/compile_commands.json')
        require(sha(raw) == COMMANDS_SHA, 'Original compiler database hash mismatch')
        q = json.loads(archive.read('qualification.json'))
        require(q['version'] == '0.1.63' and q['source_revision'] == PINS['product'], 'Release identity mismatch')
        for name, revision in PINS.items():
            require(q['controlled_build']['sources'][name]['revision'] == revision, 'Source pin mismatch: ' + name)
        config = archive.read('build/sdkconfig')
        require(b'CONFIG_IDF_TARGET="esp32s3"' in config, 'Target is not ESP32-S3')
        return q, raw, config


def select_runtime(raw, root=ROOT):
    units = json.loads(raw)
    runtime = root / 'sources/runtime'
    selected = [u for u in units if pathlib.Path(u['file']).is_relative_to(runtime)]
    require(len(units) == 930 and len(selected) == 68, 'Unexpected compiler unit inventory')
    require(len({u['file'] for u in selected}) == 68, 'Duplicate Runtime translation unit')
    for unit in selected:
        require(pathlib.Path(unit['directory']) == root / 'build', 'Unexpected compilation directory')
        args = shlex.split(unit['command'])
        require(args[0].startswith('/opt/esp/tools/xtensa-esp-elf/esp-15.2.0_20251204/'), 'Wrong compiler family')
        require(pathlib.Path(args[0]).name in ('xtensa-esp32s3-elf-gcc', 'xtensa-esp32s3-elf-g++'), 'Wrong CPU wrapper')
    return selected


def verify_sources(q, root=ROOT, sdk=pathlib.Path('/opt/esp/idf')):
    for name, revision in PINS.items():
        directory = sdk if name == 'sdk' else root / 'sources' / name
        actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=directory, text=True).strip()
        require(actual == revision, 'Checkout revision mismatch: ' + name)
        dirty = subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=directory, text=True)
        require(not dirty.strip(), 'Dirty checkout: ' + name)
    checked = 0
    for path, record in q['controlled_build']['files'].items():
        if record['kind'] in ('source', 'toolchain'):
            check_file(path, record)
            checked += 1
    for record in q['controlled_build']['tools'].values():
        check_file(record['path'], record)
    return checked


def reconfigure(q, config, root=ROOT):
    build = root / 'build'
    build.mkdir(parents=True, exist_ok=True)
    (build / 'sdkconfig').write_bytes(config)
    command = q['controlled_build']['commands'][0]
    expected = ['/opt/esp/python_env/idf6.1_py3.12_env/bin/python', '/opt/esp/idf/tools/idf.py',
                '-C', str(root / 'sources/runtime/idf'), '-B', str(build),
                '-D', 'RISC_PRODUCT_PROFILE=' + str(root / 'sources/product/profiles/provisioning-native.cmake'),
                '-D', 'RISC_SOURCE_SHA=' + PINS['runtime'], '-D', 'SDKCONFIG=' + str(build / 'sdkconfig'),
                '-D', 'CCACHE_ENABLE=0', 'reconfigure']
    require(command == expected, 'Unexpected configuration command')
    subprocess.run(command, check=True)
    headers = []
    for path, record in q['controlled_build']['files'].items():
        if record['kind'] == 'generated' and path.endswith(('.h', '.inc')) and '/bootloader/' not in path:
            check_file(path, record)
            headers.append(path)
    require(len(headers) == 8, 'Unexpected generated Runtime header inventory')
    check_file(build / 'sdkconfig', q['controlled_build']['files'][str(build / 'sdkconfig')])
    return headers


def parser_commands(units, runner=subprocess.run):
    probes, result = {}, []
    for unit in units:
        args = shlex.split(unit['command'])
        compiler = args[0]
        language = 'c++' if compiler.endswith('g++') else 'c'
        if compiler not in probes:
            probe = runner([compiler, '-E', '-v', '-x', language, '-'], input='', text=True, capture_output=True, check=True)
            require('#include <...> search starts here:' in probe.stderr, 'No implicit include probe')
            paths = probe.stderr.split('#include <...> search starts here:', 1)[1].split('End of search list.', 1)[0]
            includes = [line.strip() for line in paths.splitlines() if line.strip()]
            require(includes and all(pathlib.Path(p).is_absolute() for p in includes), 'Invalid implicit include path')
            probes[compiler] = {'language': language, 'implicit_includes': includes, 'stderr': probe.stderr}
        parsed = dict(unit, original_arguments=args[:], arguments=args[:])
        # The GCC executable name selects S3 implicitly. Clang does not run this
        # wrapper. Preserve that verified CPU choice explicitly for its parser.
        parsed['arguments'].append('-mcpu=esp32s3')
        for path in probes[compiler]['implicit_includes']:
            parsed['arguments'] += ['-isystem', path]
        parsed['parser_adaptation'] = {'cpu': 'esp32s3', 'basis': 'verified GCC wrapper and sdkconfig',
                                       'implicit_includes': probes[compiler]['implicit_includes']}
        result.append(parsed)
    return result, probes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', required=True)
    parser.add_argument('--source-set-version', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    require(args.source_set_version == SET_VERSION, 'Wrong immutable source-set version')
    q, raw, config = read_release(args.archive)
    units = select_runtime(raw)
    checked = verify_sources(q)
    headers = reconfigure(q, config)
    commands, probes = parser_commands(units)
    out = pathlib.Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'original-compile_commands.json').write_bytes(raw)
    parsed = json.dumps(commands, indent=2) + '\n'
    (out / 'compile_commands.json').write_text(parsed)
    receipt = {'schema': 'atlas-wave063-inputs/1', 'archive_sha256': ARCHIVE_SHA,
               'original_commands_sha256': COMMANDS_SHA, 'parser_commands_sha256': sha(parsed.encode()),
               'source_set_version': SET_VERSION, 'source_pins': PINS, 'total_release_units': 930,
               'selected_runtime_units': len(units), 'source_and_toolchain_files_verified': checked,
               'generated_headers_verified': headers, 'compiler_probes': probes,
               'firmware_rebuilt': False, 'scope': 'Runtime translation units only; other repositories remain unqualified'}
    (out / 'input-verification.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({k: v for k, v in receipt.items() if k != 'compiler_probes'}))


if __name__ == '__main__':
    main()
