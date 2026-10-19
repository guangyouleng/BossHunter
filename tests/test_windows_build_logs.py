"""Full native build output must survive missing PowerShell transcript output."""
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location(
    'windows_build_log_support', Path(__file__).resolve().parents[1] / 'packaging/windows/build_support.py')
support = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(support)


class BuildLogTests(TestCase):
    def test_real_child_stdout_stderr_and_partial_final_line_are_logged_and_mirrored(self):
        script = "import sys; print('stdout', flush=True); sys.stderr.write('stderr\\n'); sys.stderr.flush(); print('final', end='', flush=True)"
        with TemporaryDirectory(prefix='build log ') as folder, patch.object(support.sys, 'stdout', new=io.StringIO()) as output:
            log = Path(folder) / 'nested' / 'pyinstaller.log'
            code = support.run_logged_command([sys.executable, '-u', '-c', script], log)
            self.assertEqual(code, 0)
            self.assertEqual(log.read_bytes().replace(b'\r\n', b'\n'), b'stdout\nstderr\nfinal')
            self.assertIn('stdout\nstderr\nfinal', output.getvalue().replace('\r\n', '\n'))

    def test_real_child_preserves_underlying_exception_chain_and_failure_exit(self):
        script = "try:\n raise PermissionError('synthetic PE write failure')\nexcept OSError as error:\n raise RuntimeError('synthetic retry exhaustion') from error"
        with TemporaryDirectory() as folder, patch.object(support.sys, 'stdout', new=io.StringIO()):
            log = Path(folder) / 'pyinstaller.log'
            code = support.run_logged_command([sys.executable, '-u', '-c', script], log)
            content = log.read_text()
        self.assertNotEqual(code, 0)
        self.assertIn('PermissionError: synthetic PE write failure', content)
        self.assertIn('direct cause', content)
        self.assertIn('RuntimeError: synthetic retry exhaustion', content)

    def test_failed_new_run_replaces_old_success_log_and_retains_invalid_bytes(self):
        script = "import sys; sys.stdout.buffer.write(b'new\\xffoutput'); sys.stdout.flush(); sys.exit(7)"
        with TemporaryDirectory() as folder, patch.object(support.sys, 'stdout', new=io.StringIO()):
            log = Path(folder) / 'pyinstaller.log'
            log.write_text('old success log')
            self.assertEqual(support.run_logged_command([sys.executable, '-u', '-c', script], log), 7)
            self.assertEqual(log.read_bytes(), b'new\xffoutput')

    def test_non_unicode_terminal_does_not_lose_or_fail_child_output(self):
        script = "print('\\u4e2d\\u6587')"
        terminal = Mock()
        def write(text):
            text.encode('ascii')
        terminal.write.side_effect = write
        with TemporaryDirectory() as folder, patch.object(support.sys, 'stdout', terminal):
            log = Path(folder) / 'pyinstaller.log'
            self.assertEqual(support.run_logged_command([sys.executable, '-u', '-X', 'utf8', '-c', script], log), 0)
            self.assertEqual(log.read_text(encoding='utf-8'), '中文\n')

    def test_log_cli_keeps_exact_arguments_and_only_calls_verified_build_child(self):
        arguments = ['--clean', '--distpath', 'C:/Project With Spaces/dist', 'BossHunter.spec']
        with patch.object(support, 'probe_python') as probe, patch.object(support, 'run_logged_command', return_value=0) as run:
            support.main(['--pyinstaller-log', 'C:/Build Logs/pyinstaller.log', *arguments])
        probe.assert_called_once()
        command, log = run.call_args.args
        self.assertEqual(command[:4], [sys.executable, '-u', '-X', 'utf8'])
        self.assertEqual(command[5:], ['--pyinstaller', *arguments])
        self.assertEqual(log, 'C:/Build Logs/pyinstaller.log')

    def test_invalid_python_never_launches_logged_child(self):
        with patch.object(support, 'probe_python', side_effect=ValueError('wrong ABI')), patch.object(support, 'run_logged_command') as run:
            with self.assertRaisesRegex(ValueError, 'wrong ABI'):
                support.main(['--pyinstaller-log', 'pyinstaller.log', 'BossHunter.spec'])
        run.assert_not_called()

    def test_log_cli_preserves_failure_instead_of_reporting_success(self):
        with patch.object(support, 'probe_python'), patch.object(support, 'run_logged_command', return_value=9):
            with self.assertRaises(SystemExit) as caught:
                support.main(['--pyinstaller-log', 'pyinstaller.log', 'BossHunter.spec'])
        self.assertEqual(caught.exception.code, 9)

    def test_cancel_terminates_only_its_logged_child_and_preserves_log(self):
        process = Mock()
        process.__enter__ = Mock(return_value=process)
        process.__exit__ = Mock(return_value=False)
        process.stdout = Mock()
        process.stdout.__iter__ = Mock(side_effect=KeyboardInterrupt)
        process.poll.return_value = None
        with TemporaryDirectory() as folder, patch.object(support.subprocess, 'Popen', return_value=process), patch.object(support.sys, 'stdout', new=io.StringIO()):
            log = Path(folder) / 'pyinstaller.log'
            with self.assertRaises(KeyboardInterrupt):
                support.run_logged_command(['synthetic-child'], log)
            self.assertEqual(log.read_bytes(), b'')
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=10)
        process.kill.assert_not_called()

    def test_cancel_escalates_only_when_its_child_ignores_terminate(self):
        process = Mock()
        process.__enter__ = Mock(return_value=process)
        process.__exit__ = Mock(return_value=False)
        process.stdout = Mock()
        process.stdout.__iter__ = Mock(side_effect=KeyboardInterrupt)
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('synthetic-child', 10), 1]
        with TemporaryDirectory() as folder, patch.object(support.subprocess, 'Popen', return_value=process), patch.object(support.sys, 'stdout', new=io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                support.run_logged_command(['synthetic-child'], Path(folder) / 'pyinstaller.log')
        process.kill.assert_called_once()
