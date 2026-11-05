import copy
import hashlib
import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('inputs', pathlib.Path(__file__).parents[1] / 'scripts/source-atlas-wave063-inputs.py')
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


def fixture():
    units = []
    for i in range(930):
        root = str(m.ROOT / 'sources/runtime') if i < 68 else '/opt/esp/idf'
        units.append({'directory': str(m.ROOT / 'build'), 'file': root + '/file' + str(i) + '.c',
                      'command': '/opt/esp/tools/xtensa-esp-elf/esp-15.2.0_20251204/xtensa-esp-elf/bin/xtensa-esp32s3-elf-gcc -DTEST=1 -c ' + root + '/file' + str(i) + '.c -o file.o'})
    return units


class Inputs(unittest.TestCase):
    def environment(self):
        return {'IDF_PATH': '/opt/esp/idf',
                'IDF_PYTHON_ENV_PATH': '/opt/esp/python_env/idf6.1_py3.12_env',
                'ESP_IDF_VERSION': '6.1'}

    def test_pinned_environment(self):
        self.assertEqual(self.environment(), m.verify_environment(self.environment()))

    def test_missing_environment(self):
        for key in self.environment():
            with self.subTest(key=key):
                env = self.environment(); del env[key]
                with self.assertRaisesRegex(ValueError, key):
                    m.verify_environment(env)

    def test_wrong_environment(self):
        for key in self.environment():
            with self.subTest(key=key):
                env = self.environment(); env[key] = 'unexpected'
                with self.assertRaisesRegex(ValueError, key):
                    m.verify_environment(env)

    def test_environment_receipt_is_allowlisted(self):
        env = self.environment(); env['UNRELATED_PRIVATE_VALUE'] = 'not-for-receipt'
        self.assertEqual(self.environment(), m.verify_environment(env))

    def test_reconfigure_does_not_launch_without_environment(self):
        q = {'controlled_build': {'commands': [[]]}}
        with patch.dict(m.os.environ, {}, clear=True), patch.object(m.subprocess, 'run') as run:
            with tempfile.TemporaryDirectory() as d:
                with self.assertRaisesRegex(ValueError, 'IDF_PATH'):
                    m.reconfigure(q, b'x', pathlib.Path(d))
            run.assert_not_called()

    def test_select_actual_inventory(self):
        self.assertEqual(68, len(m.select_runtime(json.dumps(fixture()))))

    def test_missing_unit(self):
        with self.assertRaisesRegex(ValueError, 'inventory'):
            m.select_runtime(json.dumps(fixture()[:-1]))

    def test_duplicate_unit(self):
        u = fixture(); u[1] = copy.deepcopy(u[0])
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            m.select_runtime(json.dumps(u))

    def test_wrong_working_directory(self):
        u = fixture(); u[0]['directory'] = '/tmp'
        with self.assertRaisesRegex(ValueError, 'directory'):
            m.select_runtime(json.dumps(u))

    def test_wrong_compiler_family(self):
        u = fixture(); u[0]['command'] = u[0]['command'].replace('15.2.0', '8.4.0')
        with self.assertRaisesRegex(ValueError, 'compiler family'):
            m.select_runtime(json.dumps(u))

    def test_wrong_cpu_wrapper(self):
        u = fixture(); u[0]['command'] = u[0]['command'].replace('xtensa-esp32s3-elf-gcc', 'xtensa-esp32-elf-gcc')
        with self.assertRaisesRegex(ValueError, 'CPU wrapper'):
            m.select_runtime(json.dumps(u))

    def test_archive_tampering(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / 'release.zip'; p.write_bytes(b'wrong archive')
            with self.assertRaisesRegex(ValueError, 'archive hash'):
                m.read_release(p)

    def test_missing_or_changed_input(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / 'header.h'
            r = {'sha256': hashlib.sha256(b'exact').hexdigest()}
            with self.assertRaisesRegex(ValueError, 'Missing input'): m.check_file(p, r)
            p.write_bytes(b'exact'); m.check_file(p, r)
            p.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'): m.check_file(p, r)

    def test_parser_preserves_actual_arguments(self):
        units = fixture()[:2]
        def probe(*args, **kwargs):
            return subprocess.CompletedProcess(args[0], 0, '', '#include <...> search starts here:\n /opt/compiler/include\nEnd of search list.\n')
        result, probes = m.parser_commands(units, runner=probe)
        self.assertEqual(1, len(probes))
        for old, new in zip(units, result):
            original = m.shlex.split(old['command'])
            self.assertEqual(original, new['original_arguments'])
            self.assertEqual(original, new['arguments'][:len(original)])
            self.assertEqual(['-mcpu=esp32s3', '-isystem', '/opt/compiler/include'], new['arguments'][len(original):])

    def test_bad_probe_rejected(self):
        def probe(*args, **kwargs): return subprocess.CompletedProcess(args[0], 0, '', '')
        with self.assertRaisesRegex(ValueError, 'No implicit'):
            m.parser_commands(fixture()[:1], runner=probe)

    def test_no_build_command_execution(self):
        q = {'controlled_build': {'commands': [['cmake', '--build', '/tmp']]}}
        with tempfile.TemporaryDirectory() as d, patch.object(m.subprocess, 'run') as run, patch.dict(m.os.environ, self.environment(), clear=True):
            with self.assertRaisesRegex(ValueError, 'configuration command'):
                m.reconfigure(q, b'x', pathlib.Path(d))
            run.assert_not_called()

    def test_wrong_dispatch_identity(self):
        with patch('sys.argv', ['inputs', '--archive', 'unused', '--output', 'unused', '--source-set-version', 'old-x4']):
            with self.assertRaisesRegex(ValueError, 'source-set'):
                m.main()


if __name__ == '__main__': unittest.main()
