"""Independent providers and reply submission failure boundaries."""
from contextlib import contextmanager
from pathlib import Path
import os
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch
from bosshunter.recruiting import browser_backends
from bosshunter.recruiting.browser_backends import cloakbrowser
from bosshunter.recruiting.errors import BrowserError, TaskCancelled


class ProviderTests(unittest.TestCase):
    def run_isolated(self, script):
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=10,
            env={**os.environ, 'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src')})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_chrome_does_not_import_optional_cloak_or_playwright(self):
        self.run_isolated('''
import sys
sys.modules['cloakbrowser']=None
sys.modules['playwright']=None
from bosshunter.recruiting.browser_backends import load_backend
backend=load_backend({'browser':{'recruiting_browser_kind':'chrome'}})
assert backend.kind=='chrome'
assert 'bosshunter.recruiting.browser_backends.cloakbrowser' not in sys.modules
''')

    def test_cloak_provider_can_load_after_chrome_provider_is_removed(self):
        self.run_isolated('''
import sys
sys.modules['patchright']=None
from bosshunter.recruiting.browser_backends import load_backend, PROVIDERS, browser_choices
del PROVIDERS['chrome']
backend=load_backend({'browser':{'recruiting_browser_kind':'cloakbrowser'}})
assert backend.kind=='cloakbrowser'
assert browser_choices()=={'cloakbrowser':'CloakBrowser'}
assert 'bosshunter.recruiting.browser_backends.chrome' not in sys.modules
''')

    def test_unknown_or_removed_provider_stops_without_fallback(self):
        with patch.dict(browser_backends.PROVIDERS, {'chrome':'.chrome'}, clear=True), \
             patch.object(browser_backends, 'import_module') as imports:
            for kind in ('cloakbrowser', 'unknown', [], None):
                with self.assertRaises(BrowserError) as error:
                    browser_backends.load_backend({'browser':{'recruiting_browser_kind':kind}})
                self.assertEqual(error.exception.code, 'browser_backend_unavailable')
            imports.assert_not_called()

    def test_explicit_disable_uses_standard_actions_without_loading_human_layer(self):
        backend = browser_backends.load_backend({'browser':{'recruiting_browser_kind':'cloakbrowser', 'cloakbrowser_humanize':False}})
        adapter = Mock()
        with patch.object(cloakbrowser, 'prepare_page') as prepare, patch.object(cloakbrowser, 'start_driver') as start:
            backend.reply(adapter, {'id':'1-0', 'position_title':'岗位', 'messages':[]}, '你好')
        self.assertEqual(adapter.evaluate.call_count, 2)
        prepare.assert_not_called()
        start.assert_not_called()


class ReplyBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.backend = cloakbrowser.Backend({})
        self.page, self.editor, self.send, self.state = Mock(), Mock(), Mock(), Mock()
        self.send.filter.return_value = self.send
        self.page.locator.side_effect = [self.editor, self.send]
        self.state.evaluate.return_value = True
        self.adapter = Mock()
        self.check = Mock()
        @contextmanager
        def input_page():
            yield self.page, self.check
        self.adapter._input_page = input_page
        for name, kwargs in (('prepare_page', {}), ('character_pause', {}),
                             ('create_state', {'return_value':self.state}),
                             ('unique_visible', {'side_effect':lambda locator, description: locator})):
            p = patch.object(cloakbrowser, name, **kwargs)
            p.start(); self.addCleanup(p.stop)
        self.before = {'id':'1-0', 'position_title':'岗位', 'messages':[]}

    def test_reply_types_one_character_at_a_time_and_clicks_send_once(self):
        self.assertTrue(self.backend.reply(self.adapter, self.before, '你\n🙂')['attempted'])
        self.assertEqual([c.args[0] for c in self.page.keyboard.insert_text.call_args_list], ['你','\n','🙂'])
        self.page.keyboard.press.assert_not_called()
        self.editor.fill.assert_not_called()
        self.editor.click.assert_called_once()
        self.send.click.assert_called_once()
        self.state.dispose.assert_called_once()

    def test_stopped_before_submit_never_clicks_send(self):
        self.send.hover.side_effect = TaskCancelled('stop')
        with self.assertRaises(TaskCancelled):
            self.backend.reply(self.adapter, self.before, '你好')
        self.send.click.assert_not_called()
        self.state.dispose.assert_called_once()

    def test_stopped_after_submit_boundary_remains_uncertain(self):
        self.send.click.side_effect = TaskCancelled('stop')
        with self.assertRaises(BrowserError) as error:
            self.backend.reply(self.adapter, self.before, '你好')
        self.assertNotIsInstance(error.exception, TaskCancelled)
        self.assertEqual(error.exception.code, 'reply_uncertain')
        self.send.click.assert_called_once()
        self.state.dispose.assert_called_once()

    def test_submit_timeout_never_retries_or_uses_runtime_dom_click(self):
        self.send.click.side_effect = TimeoutError('uncertain')
        with self.assertRaises(TimeoutError):
            self.backend.reply(self.adapter, self.before, '你好')
        self.send.click.assert_called_once()
        self.adapter.evaluate.assert_not_called()
        self.state.dispose.assert_called_once()

    def test_cancellation_on_page_guard_exit_cannot_mark_send_cancelled(self):
        @contextmanager
        def input_page():
            yield self.page, self.check
            raise TaskCancelled('stop after submit')
        self.adapter._input_page = input_page
        with self.assertRaises(BrowserError) as error:
            self.backend.reply(self.adapter, self.before, '你好')
        self.assertNotIsInstance(error.exception, TaskCancelled)
        self.assertEqual(error.exception.code, 'reply_uncertain')
        self.send.click.assert_called_once()

    def test_typing_rejection_preserves_partial_draft_and_never_submits(self):
        self.page.keyboard.insert_text.side_effect = BrowserError('编辑器变化')
        with self.assertRaises(BrowserError):
            self.backend.reply(self.adapter, self.before, '你好')
        self.send.click.assert_not_called()
        self.editor.fill.assert_not_called()
        self.page.keyboard.press.assert_not_called()
        self.state.dispose.assert_called_once()


class ReplyConfirmationTests(unittest.TestCase):
    def setUp(self):
        import test_recruiting
        test_recruiting.RecruitingTests.setUp(self)

    def tearDown(self):
        self.temp.cleanup()

    def test_windows_line_endings_do_not_turn_success_into_uncertain(self):
        original = self.browser.execute
        def browser_send(kind, before, content):
            return original(kind, before, content.replace('\r\n', '\n'))
        self.browser.execute = browser_send
        draft = self.service.prepare_reply(self.cid, '你好\r\n再见')
        with patch('bosshunter.recruiting.service.time.sleep'):
            result = self.service.execute(draft['id'])
        self.assertEqual(result['status'], 'sent')
        self.assertEqual(self.browser.calls, 1)

    def test_existing_equivalent_message_does_not_confirm_a_new_send(self):
        self.browser.snapshot['messages'].append({'direction':'out', 'kind':'text', 'text':'你好\n再见', 'time':''})
        self.service.sync(self.cid)
        self.browser.execute = Mock()
        draft = self.service.prepare_reply(self.cid, '你好\r\n再见')
        with patch('bosshunter.recruiting.service.time.sleep'):
            result = self.service.execute(draft['id'])
        self.assertEqual(result['status'], 'uncertain')
        self.browser.execute.assert_called_once()

    def test_extra_linebreak_is_still_a_different_message(self):
        original = self.browser.execute
        self.browser.execute = lambda kind, before, content: original(kind, before, '你好\n\n再见')
        draft = self.service.prepare_reply(self.cid, '你好\r\n再见')
        with patch('bosshunter.recruiting.service.time.sleep'):
            result = self.service.execute(draft['id'])
        self.assertEqual(result['status'], 'uncertain')
