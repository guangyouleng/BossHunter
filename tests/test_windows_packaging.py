"""Portable tests for launcher safety; no Windows processes/platform traffic."""
import importlib.util
from contextlib import contextmanager
import hashlib
import json
import os
import struct
import sys
from threading import get_ident
from types import SimpleNamespace
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from bosshunter.config import load_config, save_config

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('windows_launcher', ROOT / 'packaging/windows/launcher.py')
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)
SUPPORT_SPEC = importlib.util.spec_from_file_location('windows_build_support', ROOT / 'packaging/windows/build_support.py')
support = importlib.util.module_from_spec(SUPPORT_SPEC)
SUPPORT_SPEC.loader.exec_module(support)


class WindowsPackagingTests(TestCase):
    def test_process_identity_smoke_checks_own_pid_and_rejects_reused_identity(self):
        with patch.object(launcher.sys, 'platform', 'win32'), patch('bosshunter.recruiting.process_state.process_birth', return_value=7) as birth, patch('bosshunter.recruiting.process_state.process_alive', side_effect=[True, False]) as alive:
            launcher.check_process_identity()
        birth.assert_called_once_with(os.getpid())
        self.assertEqual([call.args for call in alive.call_args_list], [(os.getpid(),7), (os.getpid(),8)])

    def test_process_identity_smoke_stops_on_missing_or_unchecked_birth(self):
        for stamp, values in [(None, []), (7, [False]), (7, [True, True])]:
            with self.subTest(stamp=stamp, values=values), patch.object(launcher.sys, 'platform', 'win32'), patch('bosshunter.recruiting.process_state.process_birth', return_value=stamp), patch('bosshunter.recruiting.process_state.process_alive', side_effect=values):
                with self.assertRaisesRegex(RuntimeError, 'creation-time identity check failed'):
                    launcher.check_process_identity()

    def test_process_identity_smoke_skips_windows_api_on_other_systems(self):
        with patch.object(launcher.sys, 'platform', 'darwin'), patch('bosshunter.recruiting.process_state.process_birth') as birth:
            launcher.check_process_identity()
        birth.assert_not_called()

    def test_cookie_smoke_check_uses_worker_context_without_cookie_or_shadow_reads(self):
        events = []

        @contextmanager
        def com_context():
            events.append(('init', get_ident()))
            try:
                yield
            finally:
                events.append(('uninit', get_ident()))

        def connect():
            self.assertEqual(events, [('init', get_ident())])
            events.append(('wmi', get_ident()))
            return object()

        cookies = Mock()
        wmi = SimpleNamespace(WMI=connect)
        with patch.object(launcher.sys, 'platform', 'win32'), patch('bosshunter.recruiting.local_session._cookie_com_context', com_context), patch.dict(sys.modules, {'wmi': wmi, 'browser_cookie3': cookies}):
            launcher.check_cookie_worker()
        self.assertEqual([event[0] for event in events], ['init', 'wmi', 'uninit'])
        self.assertEqual(len({thread for _, thread in events}), 1)
        self.assertNotEqual(events[0][1], get_ident())
        cookies.chrome.assert_not_called()

    def test_cookie_smoke_check_propagates_worker_failure(self):
        failure = RuntimeError('synthetic WMI failure')
        wmi = Mock()
        wmi.WMI.side_effect = failure
        with patch.object(launcher.sys, 'platform', 'win32'), patch('bosshunter.recruiting.local_session._cookie_com_context'), patch.dict(sys.modules, {'wmi': wmi}):
            with self.assertRaisesRegex(RuntimeError, 'COM/WMI worker check failed') as caught:
                launcher.check_cookie_worker()
        self.assertIs(caught.exception.__cause__, failure)

    def test_cookie_smoke_check_does_not_require_windows_dependencies_on_macos(self):
        with patch.object(launcher.sys, 'platform', 'darwin'), patch('bosshunter.recruiting.local_session._cookie_com_context') as context:
            launcher.check_cookie_worker()
        context.assert_not_called()

    def test_fresh_config_is_separate_and_preserves_recruiting_pacing(self):
        with TemporaryDirectory() as folder:
            base = Path(folder)
            path, config = launcher.prepare_config(base)
            self.assertEqual(path, base / 'config.yaml')
            self.assertEqual(config['browser']['recruiting_user_data_dir'], str(base / 'chrome-profile'))
            self.assertFalse(config['browser']['auto_start_proxy'])
            self.assertEqual(config['recruiting']['read_delay_min'], 120)
            self.assertEqual(config['recruiting']['read_delay_max'], 180)
            self.assertEqual(config['recruiting']['monitor_interval_seconds'], 600)
            self.assertFalse((base / '.config.credentials.yaml').exists())
            self.assertFalse((base / 'data/recruiting.db').exists())

    def test_existing_configuration_credentials_and_data_survive(self):
        with TemporaryDirectory() as folder:
            base = Path(folder)
            path, config = launcher.prepare_config(base)
            config['ai']['api_key'] = 'synthetic-key'
            config['recruiting']['read_daily_limit'] = 123
            config['browser']['auto_start_proxy'] = True
            save_config(config, path)
            data = base / 'existing-data'
            data.write_text('retained')
            launcher.prepare_config(base)
            saved = load_config(path)
            self.assertEqual(saved['ai']['api_key'], 'synthetic-key')
            self.assertNotIn('synthetic-key', path.read_text())
            self.assertEqual(saved['recruiting']['read_daily_limit'], 123)
            self.assertEqual(data.read_text(), 'retained')

    def test_nonlocal_proxy_rejected(self):
        with TemporaryDirectory() as folder:
            path, config = launcher.prepare_config(Path(folder))
            config['browser']['proxy_host'] = '0.0.0.0'
            save_config(config, path)
            with self.assertRaisesRegex(RuntimeError, '127.0.0.1'):
                launcher.prepare_config(Path(folder))

    def test_frozen_child_uses_executable_and_preserves_spaced_paths(self):
        with patch.object(launcher.sys, 'frozen', True, create=True), patch.object(launcher.sys, 'executable', 'C:/Program Files/BossHunter.exe'):
            self.assertEqual(launcher.child_command('web', Path('C:/User Data/config.yaml')),
                             ['C:/Program Files/BossHunter.exe', '--service', 'web', 'C:/User Data/config.yaml'])

    def test_port_conflict_never_spawns_or_kills_unrelated_processes(self):
        with TemporaryDirectory() as folder, patch.object(launcher, 'port_free', return_value=False), patch.object(launcher.subprocess, 'Popen') as spawn:
            controller = launcher.Controller(Path(folder))
            with self.assertRaisesRegex(RuntimeError, '8686'):
                controller.start()
            spawn.assert_not_called()
            self.assertEqual(controller.processes, [])

    def test_startup_failure_cleans_up_its_owned_processes(self):
        with TemporaryDirectory() as folder:
            base = Path(folder)
            (base / 'logs').mkdir()
            controller = launcher.Controller(base)
            process, job = Mock(), Mock()
            process.poll.return_value = None
            def fail_spawn(*args, **kwargs):
                controller.processes.append(process)
                raise RuntimeError('synthetic startup error')
            with patch.object(launcher, 'port_free', return_value=True), patch.object(launcher, 'find_chrome', return_value=Path('chrome.exe')), patch.object(launcher, 'node_path', return_value=Mock(is_file=lambda: True)), patch.object(launcher, 'WindowsJob', return_value=job), patch.object(controller, 'spawn', side_effect=fail_spawn):
                with self.assertRaisesRegex(RuntimeError, 'synthetic startup error'):
                    controller.start()
            process.terminate.assert_called_once()
            job.close.assert_called_once()
            self.assertEqual(controller.processes, [])

    def test_complete_start_waits_for_services_and_stop_releases_all_owned_children(self):
        with TemporaryDirectory() as folder:
            base = Path(folder)
            (base / 'logs').mkdir()
            controller = launcher.Controller(base)
            job = Mock()
            processes = [Mock() for _ in range(4)]
            for process in processes:
                process.poll.return_value = None
            with patch.object(launcher, 'port_free', return_value=True), patch.object(launcher, 'find_chrome', return_value=Path('chrome.exe')), patch.object(launcher, 'node_path', return_value=Mock(is_file=lambda: True)), patch.object(launcher, 'WindowsJob', return_value=job), patch.object(launcher.subprocess, 'CREATE_NO_WINDOW', 0, create=True), patch.object(launcher.subprocess, 'Popen', side_effect=processes) as spawn, patch.object(controller, 'wait_ready') as ready, patch.object(controller.cancel, 'wait', return_value=False), patch.object(launcher, 'open_dashboard') as opened:
                controller.start()
                self.assertEqual(spawn.call_count, 4)
                chrome_args = spawn.call_args_list[0].args[0]
                self.assertEqual(chrome_args[-1], 'https://www.zhipin.com/')
                self.assertEqual(job.assign.call_count, 4)
                self.assertEqual(ready.call_count, 3)
                self.assertEqual(controller.processes, processes)
                opened.assert_called_once()
                controller.stop()
            for process in processes:
                process.terminate.assert_called_once()
            job.close.assert_called_once()
            self.assertEqual(controller.logs, [])
            for call in spawn.call_args_list:
                self.assertTrue(call.kwargs['stdout'].closed)

    def test_stop_handles_already_exited_children_and_is_idempotent(self):
        controller = launcher.Controller(Path('.'))
        exited, active, job = Mock(), Mock(), Mock()
        exited.poll.return_value = 0
        active.poll.return_value = None
        controller.processes = [exited, active]
        controller.job = job
        controller.stop()
        controller.stop()
        exited.terminate.assert_not_called()
        active.terminate.assert_called_once()
        job.close.assert_called_once()

    def test_browser_exit_preserves_http_monitoring_but_backend_exit_is_fatal(self):
        controller = launcher.Controller(Path('.'))
        browser, proxy, web, worker = [Mock() for _ in range(4)]
        controller.processes = [browser, proxy, web, worker]
        controller.browser_process = browser
        browser.poll.return_value = 0
        for process in (proxy, web, worker):
            process.poll.return_value = None
        self.assertFalse(controller.backend_exited())
        for process in (proxy, web, worker):
            with self.subTest(process=process):
                process.poll.return_value = 1
                self.assertTrue(controller.backend_exited())
                process.poll.return_value = None
        controller.stop()
        self.assertIsNone(controller.browser_process)

    def test_cancellation_interrupts_health_wait_without_network(self):
        controller = launcher.Controller(Path('.'))
        controller.cancel.set()
        with patch.object(launcher, 'http_read') as read:
            with self.assertRaisesRegex(RuntimeError, '取消'):
                controller.wait_ready('http://127.0.0.1:9222/json/version', lambda _: True)
            read.assert_not_called()

    def test_cancelled_start_does_not_write_config_or_launch_any_process(self):
        with TemporaryDirectory() as folder:
            base = Path(folder)
            controller = launcher.Controller(base)
            controller.cancel.set()
            with patch.object(launcher, 'port_free', return_value=True), patch.object(launcher, 'find_chrome', return_value=Path('chrome.exe')), patch.object(launcher, 'node_path', return_value=Mock(is_file=lambda: True)), patch.object(launcher, 'WindowsJob') as job, patch.object(controller, 'spawn') as spawn, patch.object(controller, 'wait_ready'), patch.object(launcher, 'open_dashboard') as opened:
                with self.assertRaisesRegex(RuntimeError, '取消'):
                    controller.start()
            self.assertEqual(spawn.call_count, 0)
            job.assert_not_called()
            opened.assert_not_called()
            self.assertFalse((base / 'config.yaml').exists())

    def test_cancel_during_successful_health_read_is_not_reported_ready(self):
        controller = launcher.Controller(Path('.'))
        def health(_):
            controller.cancel.set()
            return b'ready'
        with patch.object(launcher, 'http_read', side_effect=health):
            with self.assertRaisesRegex(RuntimeError, '取消'):
                controller.wait_ready('http://127.0.0.1:9222/json/version', lambda _: True)

    def test_cancelled_spawn_does_not_open_logs_or_launch_process(self):
        with TemporaryDirectory() as folder:
            controller = launcher.Controller(Path(folder))
            (Path(folder) / 'logs').mkdir()
            controller.job = Mock()
            controller.cancel.set()
            with patch.object(launcher.subprocess, 'CREATE_NO_WINDOW', 0, create=True), patch.object(launcher.subprocess, 'Popen') as spawn:
                with self.assertRaisesRegex(RuntimeError, '取消'):
                    controller.spawn(['synthetic.exe'], 'web', {})
            spawn.assert_not_called()
            self.assertEqual(controller.logs, [])

    def test_user_directory_does_not_depend_on_source_checkout(self):
        with patch.dict(os.environ, {'LOCALAPPDATA': 'C:/Users/Test/AppData/Local'}):
            self.assertEqual(launcher.user_root(), Path('C:/Users/Test/AppData/Local/BossHunter'))

    def test_spec_resource_manifest_contains_only_distribution_resources(self):
        captured = {}
        fake_a = Mock(pure=[], scripts=[],
                      binaries=[('MSVCP140.dll', 'C:/Windows/System32/msvcp140.dll', 'BINARY')],
                      datas=[('package/msvcp140.dll', 'C:/arm64/msvcp140.dll', 'DATA')])
        def analysis(*args, **kwargs):
            captured.update(kwargs)
            return fake_a
        hook = Mock(collect_all=Mock(return_value=([], [], [])), collect_submodules=lambda _: [], copy_metadata=lambda *a, **kw: [])
        with TemporaryDirectory() as folder, patch.dict(os.environ, {'BOSSHUNTER_BUILD_NODE': folder, 'BOSSHUNTER_BUILD_VC_RUNTIME': folder}), patch.dict('sys.modules', {'PyInstaller': Mock(), 'PyInstaller.utils': Mock(), 'PyInstaller.utils.hooks': hook}):
            for name in support.vc_runtime_manifest()['dll_names']:
                WindowsBuildArchitectureTests.executable(Path(folder) / name, 0x8664)
            namespace = {'SPECPATH': str(ROOT / 'packaging/windows'), 'Analysis': analysis,
                         'PYZ': Mock(), 'EXE': Mock(), 'COLLECT': Mock()}
            exec(compile((ROOT / 'packaging/windows/BossHunter.spec').read_text(), 'BossHunter.spec', 'exec'), namespace)
        sources = [Path(source) for source, _ in captured['datas']]
        self.assertIn(('playwright',), [call.args for call in hook.collect_all.call_args_list])
        self.assertIn(('cloakbrowser',), [call.args for call in hook.collect_all.call_args_list])
        self.assertIn(ROOT / 'src/bosshunter/browser/runtime/chat-observer.mjs', sources)
        self.assertNotIn(ROOT / 'config.yaml', sources)
        self.assertNotIn(ROOT / 'data', sources)
        self.assertFalse(any('__pycache__' in str(source) for source in sources))
        runtime_binaries = [Path(source) for source, _ in captured['binaries']]
        self.assertIn(Path(folder) / 'msvcp140.dll', runtime_binaries)
        self.assertIn(('MSVCP140.dll', str(Path(folder) / 'msvcp140.dll'), 'BINARY'), fake_a.binaries)
        self.assertIn(('package/msvcp140.dll', str(Path(folder) / 'msvcp140.dll'), 'BINARY'), fake_a.binaries)
        self.assertEqual(fake_a.datas, [])
        self.assertEqual(namespace['EXE'].call_args.kwargs['icon'], str(ROOT / 'packaging/windows/assets/BossHunter.ico'))

    def test_windows_icon_and_inno_setup_icon_are_present(self):
        icon = ROOT / 'packaging/windows/assets/BossHunter.ico'
        self.assertTrue(icon.is_file())
        self.assertEqual(icon.read_bytes()[:4], b'\x00\x00\x01\x00')
        script = (ROOT / 'packaging/windows/BossHunter.iss').read_text()
        self.assertIn(r'SetupIconFile=assets\BossHunter.ico', script)


class DesktopBrowserChoiceTests(TestCase):
    def test_desktop_locks_choice_during_start_run_and_stop_then_unlocks(self):
        class Variable:
            def __init__(self, value=''): self.value = value
            def get(self): return self.value
            def set(self, value): self.value = value

        widgets = []
        def factory(kind):
            def create(*args, **kwargs):
                widget = Mock()
                props = dict(kwargs)
                widget.configure.side_effect = lambda **values: props.update(values)
                widgets.append((kind, widget, props))
                return widget
            return create
        root, controller, kernel = Mock(), Mock(processes=[]), Mock()
        controller.backend_exited.return_value = False
        kernel.CreateMutexW.return_value = 123
        dialog = Mock()
        dialog.askopenfilename.return_value = 'C:/Cloak Browser/chrome.exe'
        ttk = SimpleNamespace(**{name: factory(name) for name in ('Label', 'Frame', 'Combobox', 'Entry', 'Button')})
        tk = SimpleNamespace(Tk=lambda: root, StringVar=Variable, ttk=ttk, filedialog=dialog, messagebox=Mock())
        def find(kind, text=None):
            return next((widget, props) for name, widget, props in widgets
                        if name == kind and (text is None or props.get('text') == text))
        def drive():
            selector, choice = find('Combobox')
            _, entry = find('Entry')
            _, browse = find('Button', '选择文件')
            _, start = find('Button', '启动系统')
            _, stop = find('Button', '停止系统')
            self.assertEqual(entry['state'], 'disabled')
            choice['textvariable'].set('CloakBrowser')
            selector.bind.call_args.args[1]()
            self.assertEqual(entry['state'], 'normal')
            browse['command']()
            start['command']()
            self.assertEqual(choice['state'], 'disabled')
            self.assertEqual(browse['state'], 'disabled')
            controller.start.assert_called_once_with('cloakbrowser', 'C:/Cloak Browser/chrome.exe')
            root.after.call_args.args[1]()
            self.assertEqual(choice['state'], 'disabled')
            closed_browser = Mock()
            closed_browser.poll.return_value = 0
            controller.processes = [closed_browser]
            root.after.call_args.args[1]()
            controller.stop.assert_not_called()
            self.assertEqual(choice['state'], 'disabled')
            controller.processes = []
            stop['command']()
            self.assertEqual(entry['state'], 'disabled')
            root.after.call_args.args[1]()
            self.assertEqual(choice['state'], 'readonly')
            self.assertEqual(entry['state'], 'normal')
        root.mainloop.side_effect = drive
        with TemporaryDirectory() as folder, patch.object(launcher, 'user_root', return_value=Path(folder)), patch.object(launcher, 'Controller', return_value=controller), patch.dict(sys.modules, {'tkinter': tk}), patch('ctypes.WinDLL', return_value=kernel, create=True), patch('ctypes.get_last_error', return_value=0, create=True), patch.object(launcher.threading, 'Thread', side_effect=lambda target, **kwargs: SimpleNamespace(start=target)):
            launcher.desktop()

    def make_browser(self, base, machine=0x8664):
        binary = base / 'Cloak Browser' / 'chrome.exe'
        binary.parent.mkdir(parents=True)
        WindowsBuildArchitectureTests.executable(binary, machine)
        return binary

    def test_switch_back_restores_custom_chrome_profile_and_keeps_credentials(self):
        with TemporaryDirectory() as folder:
            base = Path(folder)
            path, config = launcher.prepare_config(base)
            config['browser']['recruiting_user_data_dir'] = str(base / 'original-profile')
            config['browser']['recruiting_target_id'] = 'old-target'
            config['ai']['api_key'] = 'synthetic-private-key'
            save_config(config, path)
            binary = self.make_browser(base)
            plan = launcher.browser_plan(base, config, 'cloakbrowser', str(binary))
            self.assertEqual(config['browser']['recruiting_target_id'], 'old-target')
            launcher.save_browser_plan(path, config, plan)
            saved = load_config(path)
            self.assertNotIn('recruiting_target_id', saved['browser'])
            self.assertEqual(saved['browser']['recruiting_user_data_dir'], str(base / 'cloakbrowser-profile'))
            self.assertEqual(saved['ai']['api_key'], 'synthetic-private-key')
            with patch.object(launcher, 'find_chrome', return_value=Path('chrome.exe')):
                restored = launcher.browser_plan(base, saved, 'chrome')
            self.assertEqual(restored['profile'], base / 'original-profile')
            launcher.save_browser_plan(path, saved, restored)
            self.assertEqual(load_config(path)['browser']['cloakbrowser_executable'], str(binary))

    def test_missing_cloak_never_falls_back_to_chrome_or_starts_services(self):
        with TemporaryDirectory() as folder:
            base = Path(folder)
            (base / 'logs').mkdir()
            controller = launcher.Controller(base)
            with patch.object(launcher, 'port_free', return_value=True), patch.object(launcher, 'find_chrome') as chrome, patch.object(controller, 'spawn') as spawn:
                with self.assertRaisesRegex(RuntimeError, '完整解压'):
                    controller.start('cloakbrowser', '')
            chrome.assert_not_called()
            spawn.assert_not_called()
            self.assertEqual(load_config(base / 'config.yaml')['browser']['recruiting_browser_kind'], 'chrome')

    def test_rejects_non_x64_invalid_and_relative_executables(self):
        with TemporaryDirectory() as folder:
            base = Path(folder)
            _, config = launcher.prepare_config(base)
            binary = self.make_browser(base, 0xaa64)
            for value in (str(binary), 'chrome.exe', str(base / 'missing.exe')):
                with self.subTest(value=value), self.assertRaises(RuntimeError):
                    launcher.browser_plan(base, config, 'cloakbrowser', value)
            binary.write_bytes(b'MZ')
            with self.assertRaisesRegex(RuntimeError, 'x64'):
                launcher.browser_plan(base, config, 'cloakbrowser', str(binary))

    def test_same_profile_is_rejected(self):
        with TemporaryDirectory() as folder:
            base = Path(folder)
            _, config = launcher.prepare_config(base)
            config['browser']['desktop_browser_profiles'] = {'cloakbrowser': config['browser']['recruiting_user_data_dir']}
            with self.assertRaisesRegex(RuntimeError, '不能共用'):
                launcher.browser_plan(base, config, 'cloakbrowser', 'unused.exe')

    def test_seed_survives_restart_and_corruption_never_changes_identity(self):
        with TemporaryDirectory() as folder:
            profile = Path(folder)
            first = launcher.cloak_profile_args(profile)
            self.assertEqual(first, launcher.cloak_profile_args(profile))
            (profile / '.cloakbrowser-seed').write_text('corrupt')
            with self.assertRaisesRegex(RuntimeError, '不会自动重置'):
                launcher.cloak_profile_args(profile)
            self.assertEqual((profile / '.cloakbrowser-seed').read_text(), 'corrupt')

    def test_cloak_start_uses_selected_binary_profile_and_owned_process_cleanup(self):
        with TemporaryDirectory() as folder:
            base = Path(folder)
            (base / 'logs').mkdir()
            binary = self.make_browser(base)
            controller = launcher.Controller(base)
            job = Mock()
            processes = [Mock() for _ in range(4)]
            for process in processes:
                process.poll.return_value = None
            with patch.object(launcher, 'port_free', return_value=True), patch.object(launcher, 'find_chrome') as chrome, patch.object(launcher, 'node_path', return_value=Mock(is_file=lambda: True)), patch.object(launcher, 'WindowsJob', return_value=job), patch.object(launcher.subprocess, 'CREATE_NO_WINDOW', 0, create=True), patch.object(launcher.subprocess, 'Popen', side_effect=processes) as spawn, patch.object(controller, 'wait_ready'), patch.object(controller.cancel, 'wait', return_value=False), patch.object(launcher, 'open_dashboard'):
                controller.start('cloakbrowser', str(binary))
                args = spawn.call_args_list[0].args[0]
                self.assertEqual(args[0], str(binary))
                self.assertEqual(args[-1], 'https://www.zhipin.com/')
                child_env = spawn.call_args_list[0].kwargs['env']
                self.assertEqual(child_env['BOSSHUNTER_CHROME_PORTS'], '9222')
                self.assertEqual(child_env['BOSSHUNTER_CHROME_PORT_STRICT'], 'true')
                self.assertIn('--user-data-dir=' + str(base / 'cloakbrowser-profile'), args)
                self.assertTrue(any(arg.startswith('--fingerprint=') for arg in args))
                self.assertNotIn('--no-sandbox', args)
                self.assertEqual(spawn.call_count, 4)
                chrome.assert_not_called()
                saved = load_config(base / 'config.yaml')['browser']
                self.assertEqual(saved['recruiting_browser_kind'], 'cloakbrowser')
                self.assertEqual(saved['chrome_ports'], [9222])
                self.assertEqual(saved['cloakbrowser_executable'], str(binary))
                controller.stop()
            job.close.assert_called_once()
            for process in processes:
                process.terminate.assert_called_once()


class WindowsBuildArchitectureTests(TestCase):
    @staticmethod
    def executable(path, machine):
        content = bytearray(128)
        content[:2] = b'MZ'
        struct.pack_into('<I', content, 60, 80)
        content[80:84] = b'PE\0\0'
        struct.pack_into('<H', content, 84, machine)
        path.write_bytes(content)

    def test_accepts_x64_but_rejects_arm64_and_x86_binaries(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'python.exe'
            self.executable(path, 0x8664)
            support.require_x64(path)
            for machine in (0xaa64, 0x14c):
                self.executable(path, machine)
                with self.assertRaisesRegex(ValueError, 'x64 required'):
                    support.require_x64(path)

    def test_invalid_executable_does_not_pass_architecture_check(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'bad.exe'
            path.write_bytes(b'not an executable')
            with self.assertRaisesRegex(ValueError, 'Not a Windows executable'):
                support.pe_machine(path)
            self.executable(path, 0x8664)
            content = bytearray(path.read_bytes())
            content[80:84] = b'bad!'
            path.write_bytes(content)
            with self.assertRaisesRegex(ValueError, 'Invalid PE header'):
                support.pe_machine(path)

    def test_bundle_rejects_mixed_architecture_native_dependency(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            self.executable(root / 'BossHunter.exe', 0x8664)
            self.executable(root / 'native.pyd', 0x8664)
            support.verify_bundle(root)
            self.executable(root / 'node.exe', 0xaa64)
            with self.assertRaisesRegex(ValueError, 'x64 required'):
                support.verify_bundle(root)

    def test_probe_requires_x64_package_abi(self):
        with patch.object(support.sys, 'platform', 'win32'), patch.object(support.sys, 'version_info', (3,12)), patch.object(support, 'require_x64'), patch.object(support.sysconfig, 'get_platform', return_value='win-arm64'):
            with self.assertRaisesRegex(ValueError, 'win-amd64'):
                support.probe_python()

    def test_pyinstaller_bootloader_override_only_after_verified_x64_python(self):
        with patch.dict(os.environ, {'PROCESSOR_ARCHITECTURE': 'ARM64'}), patch.object(support, 'probe_python') as probe, patch.object(support.runpy, 'run_module') as run, patch.object(support.sys, 'argv', []):
            support.main(['--pyinstaller', '--clean', 'BossHunter.spec'])
            probe.assert_called_once()
            self.assertEqual(os.environ['PROCESSOR_ARCHITECTURE'], 'AMD64')
            self.assertEqual(support.sys.argv, ['pyinstaller', '--clean', 'BossHunter.spec'])
            run.assert_called_once_with('PyInstaller', run_name='__main__')

    def test_invalid_python_cannot_change_architecture_or_start_build(self):
        with patch.dict(os.environ, {'PROCESSOR_ARCHITECTURE': 'ARM64'}), patch.object(support, 'probe_python', side_effect=ValueError('wrong Python')), patch.object(support.runpy, 'run_module') as run:
            with self.assertRaisesRegex(ValueError, 'wrong Python'):
                support.main(['--pyinstaller', 'BossHunter.spec'])
            self.assertEqual(os.environ['PROCESSOR_ARCHITECTURE'], 'ARM64')
            run.assert_not_called()


class NodeArchivePathTests(TestCase):
    def archive(self, folder, members):
        path = Path(folder) / 'node.zip'
        with zipfile.ZipFile(path, 'w') as package:
            for member in members:
                package.writestr(member, 'synthetic file')
        return path

    def test_real_node_member_fails_at_nested_commit_root_but_fits_short_root(self):
        member = 'node-v22.23.3-win-x64/node_modules/npm/node_modules/@npmcli/metavuln-calculator/node_modules/pacote/lib/util/tar-create-options.js'
        root = 'C:\\Users\\test\\Desktop\\' + ('BossHunter-' + 'a' * 40 + '\\') * 2 + '.build\\windows'
        with TemporaryDirectory() as folder:
            archive = self.archive(folder, [member])
            with self.assertRaisesRegex(ValueError, r'271.*C:\\BossHunter'):
                support.check_node_archive(archive, root)
            support.check_node_archive(archive, r'C:\BossHunter\.build\windows')

    def test_short_changelog_does_not_hide_a_deeper_path_failure(self):
        with TemporaryDirectory() as folder:
            archive = self.archive(folder, ['node/CHANGELOG.md', 'node/' + 'nested/' * 35 + 'file.js'])
            with self.assertRaisesRegex(ValueError, 'Node ZIP paths are too long'):
                support.check_node_archive(archive, r'C:\BossHunter\.build\windows')
            self.assertEqual(list(Path(folder).iterdir()), [archive])

    def test_windows_path_lengths_count_utf16_units_and_directory_limits(self):
        with TemporaryDirectory() as folder:
            archive = self.archive(folder, ['node/' + 'x' * 200 + '.js'])
            with self.assertRaises(ValueError):
                support.check_node_archive(archive, 'C:\\' + '\U0001f600' * 30)
            archive = self.archive(folder, ['node/' + 'x' * 240 + '/'])
            with self.assertRaises(ValueError):
                support.check_node_archive(archive, r'C:\build')

    def test_empty_archive_fails_before_extraction(self):
        with TemporaryDirectory() as folder:
            archive = self.archive(folder, [])
            with self.assertRaisesRegex(ValueError, 'empty'):
                support.check_node_archive(archive, r'C:\build')


class FrozenExternalProcessTests(TestCase):
    def test_external_path_filters_only_bundle_entries_and_preserves_input(self):
        original = {'PATH': r'C:\BossHunter\_internal;C:\BossHunter\_internal\pywin32_system32;C:\Windows\System32;D:\tools;C:\BossHunter\_internal-other',
                    'BOSSHUNTER_NODE_PATH': r'C:\BossHunter\_internal\vendor\node\node.exe'}
        with patch.object(launcher.sys, 'platform', 'win32'), patch.object(launcher.sys, 'frozen', True, create=True), patch.object(launcher.sys, '_MEIPASS', r'C:\BossHunter\_internal', create=True):
            result = launcher.external_environment(original)
        self.assertEqual(result['PATH'], r'C:\Windows\System32;D:\tools;C:\BossHunter\_internal-other')
        self.assertEqual(result['BOSSHUNTER_NODE_PATH'], original['BOSSHUNTER_NODE_PATH'])
        self.assertIn('pywin32_system32', original['PATH'])

    def test_external_dll_directory_is_restored_even_when_start_fails(self):
        import ctypes
        previous = r'C:\BossHunter\_internal'
        def get_directory(size, buffer):
            if buffer is not None:
                buffer.value = previous
            return len(previous)
        kernel = Mock()
        kernel.GetDllDirectoryW.side_effect = get_directory
        kernel.SetDllDirectoryW.return_value = True
        with patch.object(launcher.sys, 'platform', 'win32'), patch.object(launcher.sys, 'frozen', True, create=True), patch.object(ctypes, 'WinDLL', return_value=kernel, create=True):
            with self.assertRaisesRegex(RuntimeError, 'synthetic launch failure'):
                with launcher.external_dll_search():
                    kernel.SetDllDirectoryW.assert_called_once_with(None)
                    raise RuntimeError('synthetic launch failure')
        self.assertEqual([call.args[0] for call in kernel.SetDllDirectoryW.call_args_list], [None, previous])

    def test_source_execution_keeps_existing_environment_and_does_not_call_windows_api(self):
        import ctypes
        with patch.object(launcher.sys, 'frozen', False, create=True), patch.object(ctypes, 'WinDLL', create=True) as load:
            with launcher.external_dll_search():
                self.assertEqual(launcher.external_environment({'PATH': 'original'}), {'PATH': 'original'})
            load.assert_not_called()


class NativeBuildPreflightTests(TestCase):
    def test_checks_pdf_and_ocr_with_the_exact_build_python_and_bounded_processes(self):
        passed = support.subprocess.CompletedProcess([], 0, 'native check OK', '')
        with patch.object(support.sys, 'executable', 'C:/Python With Spaces/python.exe'), patch.object(support.subprocess, 'run', return_value=passed) as run:
            support.main(['--check-native'])
        self.assertEqual(run.call_count, 2)
        for call in run.call_args_list:
            self.assertEqual(call.args[0][:5], ['C:/Python With Spaces/python.exe', '-I', '-X', 'utf8', '-c'])
            self.assertEqual(call.kwargs['timeout'], 120)
            self.assertTrue(call.kwargs['capture_output'])

    def test_unfrozen_dll_failure_stops_and_preserves_error_with_runtime_guidance(self):
        failed = support.subprocess.CompletedProcess([], 1, '', 'ImportError: DLL load failed while importing _extra: missing module')
        with patch.object(support.sys, 'platform', 'win32'), patch.object(support.subprocess, 'run', return_value=failed) as run, patch('builtins.print') as output:
            with self.assertRaisesRegex(RuntimeError, 'UNFROZEN.*possible cause.*vc_redist.x64.exe'):
                support.check_native_dependencies()
        run.assert_called_once()
        self.assertTrue(any('_extra: missing module' in str(call) for call in output.call_args_list))

    def test_native_crash_reports_exit_status_without_assuming_a_missing_runtime(self):
        crashed = support.subprocess.CompletedProcess([], -1073741795, '', '')
        with patch.object(support.sys, 'platform', 'win32'), patch.object(support.subprocess, 'run', return_value=crashed):
            with self.assertRaisesRegex(RuntimeError, '0xc000001d') as error:
                support.check_native_dependencies()
        self.assertNotIn('vc_redist', str(error.exception))

    def test_ocr_failure_is_not_hidden_by_a_successful_pdf_check(self):
        results = [support.subprocess.CompletedProcess([], 0, 'PDF OK', ''),
                   support.subprocess.CompletedProcess([], 1, '', 'OCR model failed')]
        with patch.object(support.subprocess, 'run', side_effect=results):
            with self.assertRaisesRegex(RuntimeError, 'RapidOCR.*UNFROZEN'):
                support.check_native_dependencies()

    def test_timed_out_native_check_stops_with_a_clear_error(self):
        with patch.object(support.subprocess, 'run', side_effect=support.subprocess.TimeoutExpired('python', 120)) as run:
            with self.assertRaisesRegex(RuntimeError, 'timed out.*PyInstaller was not started'):
                support.check_native_dependencies()
        run.assert_called_once()


class X64VcRuntimeTests(TestCase):
    def make_archive(self, root, machine=0x8664, missing=None):
        manifest = dict(support.vc_runtime_manifest())
        archive = root / 'runtime.vsix'
        binary = root / 'fixture.dll'
        WindowsBuildArchitectureTests.executable(binary, machine)
        with zipfile.ZipFile(archive, 'w') as package:
            for name in manifest['dll_names']:
                if name != missing:
                    package.writestr(manifest['member_prefix'] + name, binary.read_bytes())
            package.writestr('debug_nonredist/x64/msvcp140d.dll', b'must not ship')
            package.writestr('arm64/msvcp140.dll', b'must not ship')
        manifest['sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
        return archive, manifest

    def test_verified_archive_stages_only_release_x64_runtime(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            archive, manifest = self.make_archive(root)
            target = root / 'staged runtime'
            with patch.object(support, 'vc_runtime_manifest', return_value=manifest):
                support.stage_vc_runtime(archive, target)
                files = support.vc_runtime_files(target)
            self.assertEqual({path.name for path in files}, set(manifest['dll_names']))
            self.assertEqual({path.name for path in target.iterdir()}, set(manifest['dll_names']))

    def test_hash_mismatch_does_not_extract_or_modify_existing_files(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            archive, manifest = self.make_archive(root)
            target = root / 'staged'
            target.mkdir()
            (target / 'msvcp140.dll').write_bytes(b'previous runtime')
            manifest['sha256'] = '0' * 64
            with patch.object(support, 'vc_runtime_manifest', return_value=manifest):
                with self.assertRaisesRegex(ValueError, 'SHA-256 mismatch'):
                    support.stage_vc_runtime(archive, target)
            self.assertEqual((target / 'msvcp140.dll').read_bytes(), b'previous runtime')

    def test_arm64_archive_payload_cannot_replace_previous_runtime(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            archive, manifest = self.make_archive(root, machine=0xaa64)
            target = root / 'staged'
            target.mkdir()
            (target / 'msvcp140.dll').write_bytes(b'previous runtime')
            with patch.object(support, 'vc_runtime_manifest', return_value=manifest):
                with self.assertRaisesRegex(ValueError, '0xaa64'):
                    support.stage_vc_runtime(archive, target)
            self.assertEqual((target / 'msvcp140.dll').read_bytes(), b'previous runtime')

    def test_missing_payload_is_not_silently_ignored(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            archive, manifest = self.make_archive(root, missing='msvcp140.dll')
            target = root / 'staged'
            with patch.object(support, 'vc_runtime_manifest', return_value=manifest):
                with self.assertRaises(KeyError):
                    support.stage_vc_runtime(archive, target)
            self.assertFalse(target.exists())

    def test_hooks_cannot_overwrite_pinned_runtime_even_when_collected_as_data(self):
        files = [Path('C:/verified/msvcp140.dll'), Path('C:/verified/vcruntime140.dll')]
        binaries = [('MSVCP140.dll', 'C:/Windows/System32/msvcp140.dll', 'BINARY'),
                    ('python312.dll', 'C:/Python312/python312.dll', 'BINARY')]
        datas = [(r'package\msvcp140.dll', 'C:/arm64/msvcp140.dll', 'DATA'),
                 ('models/model.onnx', 'C:/models/model.onnx', 'DATA')]
        pinned, data = support.pin_vc_runtime(binaries, datas, files)
        self.assertEqual(pinned, [
            ('MSVCP140.dll', 'C:/verified/msvcp140.dll', 'BINARY'),
            ('python312.dll', 'C:/Python312/python312.dll', 'BINARY'),
            (r'package\msvcp140.dll', 'C:/verified/msvcp140.dll', 'BINARY'),
            ('vcruntime140.dll', 'C:/verified/vcruntime140.dll', 'BINARY'),
        ])
        self.assertEqual(data, [('models/model.onnx', 'C:/models/model.onnx', 'DATA')])
        self.assertEqual(binaries[0][1], 'C:/Windows/System32/msvcp140.dll')

    def test_other_arm64_dependencies_still_fail_bundle_validation(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            WindowsBuildArchitectureTests.executable(root / 'BossHunter.exe', 0x8664)
            WindowsBuildArchitectureTests.executable(root / 'msvcp140.dll', 0x8664)
            WindowsBuildArchitectureTests.executable(root / 'other.dll', 0xaa64)
            with self.assertRaisesRegex(ValueError, 'other.dll'):
                support.verify_bundle(root)
