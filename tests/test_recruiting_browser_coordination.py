import tempfile
import threading
import subprocess
import sys
import os
from unittest.mock import Mock, patch
import unittest
from pathlib import Path
from bosshunter.recruiting.store import Store
from bosshunter.recruiting.operations import BrowserCoordinator
from bosshunter.recruiting.errors import BrowserError


class CoordinationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        path=Path(self.temp.name)/'recruiting.db'
        self.a=BrowserCoordinator(Store(path)); self.b=BrowserCoordinator(Store(path))

    def test_independent_clients_cannot_overlap_and_lease_releases(self):
        with self.a.operation('切换岗位'):
            with self.assertRaises(BrowserError) as caught:
                with self.b.operation('发送回复'): self.fail('must not run')
            self.assertEqual(caught.exception.code,'browser_busy')
            self.assertEqual(self.b.store.setting('browser_operation')['operation'],'切换岗位')
        with self.b.operation('发送回复'): pass

    def test_background_batch_conflict_is_diagnosed_once(self):
        with self.a.operation('发送回复'):
            with self.assertRaises(BrowserError) as caught:
                with self.b.operation('切换岗位并读取推荐列表'): pass
            self.b.store.record_browser_failure(caught.exception)
        diagnostics=self.a.store.setting('browser_diagnostics')
        self.assertEqual(len(diagnostics),1)
        self.assertEqual(diagnostics[0]['code'],'browser_busy')
        self.assertEqual(diagnostics[0]['stage'],'切换岗位并读取推荐列表')

    def test_real_subprocess_cannot_take_parent_lease(self):
        code="""
import sys
from bosshunter.recruiting.operations import BrowserCoordinator
from bosshunter.recruiting.store import Store
from bosshunter.recruiting.errors import BrowserError
try:
    with BrowserCoordinator(Store(sys.argv[1])).operation('child'): print('overlap')
except BrowserError as exc: print(exc.code)
"""
        with self.a.operation('parent'):
            result=subprocess.run([sys.executable,'-c',code,str(self.a.store.path)],
                env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src')},
                capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'browser_busy')

    def test_nested_operations_do_not_deadlock(self):
        with self.a.operation('岗位与列表'):
            with self.a.operation('读取列表'):
                self.assertEqual(self.a.store.setting('browser_operation')['operation'],'岗位与列表')
        self.assertEqual(self.a.store.setting('browser_operation')['status'],'done')

    def test_thread_cannot_reenter_other_threads_lease(self):
        failures=[]
        def attempt():
            try:
                with self.a.operation('another thread'): failures.append('overlap')
            except BrowserError as exc: failures.append(exc.code)
        with self.a.operation('first'):
            t=threading.Thread(target=attempt);t.start();t.join(2)
        self.assertEqual(failures,['browser_busy'])

    def test_error_releases_and_stale_owner_is_recovered(self):
        with self.assertRaises(ValueError):
            with self.a.operation('test'): raise ValueError('test')
        with self.a.store.db() as db:
            db.execute('INSERT INTO task_claims VALUES (?,?,?)',('browser-dom','0:dead','old'))
        with self.b.operation('recovered'): pass
        self.assertEqual(self.b.store.setting('browser_operation')['status'],'done')

    def test_windows_birth_token_survives_browser_operation_and_legacy_owners_still_work(self):
        with patch('bosshunter.recruiting.process_state.process_birth', return_value=(1<<32)+9):
            store = Store(self.a.store.path)
        prefix = f'{os.getpid()}:w100000009:'
        self.assertTrue(store.owner.startswith(prefix))
        with BrowserCoordinator(store).operation('synthetic birth token'):
            with store.db() as db:
                token = db.execute("SELECT owner FROM task_claims WHERE key='browser-dom'").fetchone()[0]
            self.assertTrue(token.startswith(prefix))
            with patch('bosshunter.recruiting.process_state.process_alive', return_value=True) as alive:
                self.assertTrue(Store.owner_alive(token))
            alive.assert_called_once_with(os.getpid(), (1<<32)+9)
        with patch('bosshunter.recruiting.process_state.process_alive', return_value=True) as alive:
            self.assertTrue(Store.owner_alive('1234:legacy-random-token'))
        alive.assert_called_once_with(1234)


class WindowsLivenessTests(unittest.TestCase):
    def test_reused_pid_with_different_creation_time_is_not_the_old_owner(self):
        from bosshunter.recruiting.process_state import process_alive
        kernel=Mock();kernel.OpenProcess.return_value=123
        kernel.WaitForSingleObject.return_value=0x102
        def times(handle, created, exited, kernel_time, user_time):
            created._obj.dwLowDateTime=5
            created._obj.dwHighDateTime=1
            return True
        kernel.GetProcessTimes.side_effect=times
        with patch('bosshunter.recruiting.process_state.sys.platform','win32'), \
             patch('ctypes.WinDLL',return_value=kernel,create=True), \
             patch('bosshunter.recruiting.process_state.os.kill') as kill:
            self.assertFalse(process_alive(1234, (1<<32)+4))
            self.assertTrue(process_alive(1234, (1<<32)+5))
        kill.assert_not_called()
        kernel.OpenProcess.assert_called_with(0x101000,False,1234)
        self.assertEqual(kernel.CloseHandle.call_count,2)

    def test_unknown_creation_time_does_not_steal_a_live_process_lease(self):
        from bosshunter.recruiting.process_state import process_alive
        kernel=Mock();kernel.OpenProcess.return_value=123
        kernel.WaitForSingleObject.return_value=0x102
        kernel.GetProcessTimes.return_value=False
        with patch('bosshunter.recruiting.process_state.sys.platform','win32'), \
             patch('ctypes.WinDLL',return_value=kernel,create=True):
            self.assertTrue(process_alive(1234, 7))
        kernel.CloseHandle.assert_called_once_with(123)

    def test_windows_birth_query_is_read_only_and_closes_its_handle(self):
        from bosshunter.recruiting.process_state import process_birth
        kernel=Mock();kernel.OpenProcess.return_value=123
        def times(handle, created, *rest):
            created._obj.dwLowDateTime=9
            created._obj.dwHighDateTime=1
            return True
        kernel.GetProcessTimes.side_effect=times
        with patch('bosshunter.recruiting.process_state.sys.platform','win32'), \
             patch('ctypes.WinDLL',return_value=kernel,create=True):
            self.assertEqual(process_birth(1234),(1<<32)+9)
        kernel.OpenProcess.assert_called_once_with(0x1000,False,1234)
        kernel.CloseHandle.assert_called_once_with(123)

    def test_unreadable_birth_does_not_invent_an_identity(self):
        from bosshunter.recruiting.process_state import process_birth
        kernel=Mock();kernel.OpenProcess.return_value=123
        kernel.GetProcessTimes.return_value=False
        with patch('bosshunter.recruiting.process_state.sys.platform','win32'), \
             patch('ctypes.WinDLL',return_value=kernel,create=True):
            self.assertIsNone(process_birth(1234))
            kernel.OpenProcess.return_value=0
            self.assertIsNone(process_birth(1234))
        kernel.CloseHandle.assert_called_once_with(123)

    def test_windows_checks_wait_state_and_never_sends_signal(self):
        from bosshunter.recruiting.process_state import process_alive
        kernel=Mock();kernel.OpenProcess.return_value=123
        for result,expected in [(0x102,True),(0,False),(0xffffffff,True)]:
            kernel.WaitForSingleObject.return_value=result
            with patch('bosshunter.recruiting.process_state.sys.platform','win32'), \
                 patch('ctypes.WinDLL',return_value=kernel,create=True), \
                 patch('bosshunter.recruiting.process_state.os.kill') as kill:
                self.assertEqual(process_alive(1234),expected)
            kill.assert_not_called()
            kernel.OpenProcess.assert_called_with(0x100000,False,1234)
            kernel.WaitForSingleObject.assert_called_with(123,0)
            kernel.CloseHandle.assert_called_with(123)

    def test_missing_or_inaccessible_windows_owner(self):
        from bosshunter.recruiting.process_state import process_alive
        kernel=Mock();kernel.OpenProcess.return_value=0
        for error,expected in [(87,False),(1168,False),(5,True)]:
            with patch('bosshunter.recruiting.process_state.sys.platform','win32'), \
                 patch('ctypes.WinDLL',return_value=kernel,create=True), \
                 patch('ctypes.get_last_error',return_value=error,create=True):
                self.assertEqual(process_alive(1234),expected)
        kernel.CloseHandle.assert_not_called()
