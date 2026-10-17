"""Offline classification, diagnostic privacy and shared state tests."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
import httpx
from bosshunter.browser.client import RuntimeClient
from bosshunter.recruiting.browser import BossBrowser, BrowserError, AccountPauseError
from bosshunter.recruiting.errors import safe_diagnostics
from bosshunter.recruiting.page_state import page_url_kind, SECURITY_PATHS
from bosshunter.recruiting.store import Store
from bosshunter.recruiting.local_session import LocalBossSession


class PageDiagnosticTests(unittest.TestCase):
    def test_known_paths_exact_domains_and_unrelated_pages(self):
        for path in SECURITY_PATHS:
            self.assertEqual(page_url_kind('https://www.zhipin.com'+path+'?secret=x'), 'verification_required')
        self.assertEqual(page_url_kind('https://www.zhipin.com/web/user/?ka=header-login'),'login_required')
        for url in ('https://www.zhipin.com.evil.test/web/passport/zp/403.html',
                    'https://www.zhipin.com/web/chat/recommend?_security_check=1',
                    'https://www.zhipin.com/web/chat/job/list'):
            self.assertEqual(page_url_kind(url),'')

    def test_diagnostics_drop_secrets_and_mask_identifiers(self):
        detail=safe_diagnostics({'page_url':'https://user:password@www.zhipin.com/web/chat/recommend?token=secret#secret',
            'frame_url':'https://www.zhipin.com/web/frame/recommend/encrypted-candidate?token=secret',
            'cookie':'secret','text':'candidate text','resume':'private','job_matches':True})
        self.assertNotIn('secret',json.dumps(detail));self.assertNotIn('encrypted-candidate',json.dumps(detail))
        self.assertEqual(detail['job_matches'],True);self.assertNotIn('cookie',detail)

    def test_history_is_bounded_deduplicated_and_cross_instance(self):
        with TemporaryDirectory() as d:
            a=Store(Path(d)/'db');b=Store(Path(d)/'db')
            for i in range(25):
                exc=BrowserError('private exception body',code='job_transition_timeout',stage='切换并核验岗位',diagnostics={'cookie':'secret','list_ready':False})
                a.record_browser_failure(exc);a.record_browser_failure(exc)
            rows=b.setting('browser_diagnostics');self.assertEqual(len(rows),20)
            self.assertNotIn('private exception',json.dumps(rows));self.assertNotIn('secret',json.dumps(rows))

    def test_bound_login_page_pauses_without_selecting_another_tab(self):
        runtime=Mock(spec=RuntimeClient);runtime.health.return_value={'runtime':'bosshunter'}
        runtime.targets.return_value=[{'type':'page','targetId':'bound','url':'https://www.zhipin.com/web/user/'},
                                      {'type':'page','targetId':'other','url':'https://www.zhipin.com/web/chat/index'}]
        browser=BossBrowser(runtime,target_id='bound');browser.account_pause_handler=Mock()
        with patch('bosshunter.recruiting.browser.ensure_runtime'):
            with self.assertRaises(AccountPauseError) as caught:browser.status()
        self.assertEqual(caught.exception.code,'login_required');runtime.evaluate.assert_not_called()
        browser.account_pause_handler.assert_called_once_with(1800)

    def test_paywall_is_not_account_restriction_and_diagnostic_is_recorded(self):
        runtime=Mock(spec=RuntimeClient);runtime.health.return_value={'runtime':'bosshunter'}
        runtime.targets.return_value=[{'type':'page','targetId':'bound','url':'https://www.zhipin.com/web/chat/index'}]
        runtime.evaluate.return_value={'page_issue':'paywall','diagnostic':{'page_url':'https://www.zhipin.com/web/chat/index?token=secret'}}
        browser=BossBrowser(runtime,target_id='bound');browser.account_pause_handler=Mock();browser.diagnostic_handler=Mock()
        with patch('bosshunter.recruiting.browser.ensure_runtime'):
            with self.assertRaises(BrowserError) as caught:browser.status()
        self.assertNotIsInstance(caught.exception,AccountPauseError)
        self.assertEqual(caught.exception.code,'paywall');browser.account_pause_handler.assert_not_called()
        browser.diagnostic_handler.assert_called_once()

    def test_http_login_redirect_is_classified_and_does_not_retry(self):
        pause=Mock();session=LocalBossSession(account_pause_handler=pause)
        response=httpx.Response(302,headers={'location':'/web/user/?token=secret'},request=httpx.Request('GET','https://www.zhipin.com/wapi/test'))
        with self.assertRaises(AccountPauseError) as caught:session._check_response(response)
        self.assertEqual(caught.exception.code,'login_required');pause.assert_called_once_with(1800)

    def test_user_content_containing_selector_tokens_is_never_replaced(self):
        browser=BossBrowser();before={'id':'1-0','position_title':'岗位','messages':[]}
        with patch.object(browser,'evaluate',return_value={'attempted':True}) as evaluate:
            browser.execute('reply',before,'__dom_send_button__')
        self.assertIn('"__dom_send_button__"',evaluate.call_args_list[0].args[0])
