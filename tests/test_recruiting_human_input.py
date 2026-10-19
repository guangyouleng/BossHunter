"""Scoped input tests with browser doubles; no account or platform requests."""
import unittest
from unittest.mock import Mock, patch

from bosshunter.recruiting.browser_backends import cloakbrowser as human_input
from bosshunter.recruiting.browser import BossBrowser, BrowserError, TaskCancelled
from bosshunter.recruiting.dom import CONTACT_SCROLL_TARGET, READ_JOB_ID, SELECTORS
from bosshunter.recruiting.recommend import RecommendVerifier


CONFIG = {'browser': {'recruiting_browser_kind': 'cloakbrowser'}}


class HumanInputTests(unittest.TestCase):
    def test_only_explicit_cloak_mode_enables_layer(self):
        self.assertFalse(human_input.enabled({}))
        self.assertFalse(human_input.enabled({'browser': {'cloakbrowser_humanize': True}}))
        self.assertTrue(human_input.enabled(CONFIG))
        for disabled in (False, 'false', 'true', 1, None):
            self.assertFalse(human_input.enabled({'browser': {**CONFIG['browser'], 'cloakbrowser_humanize': disabled}}))

    def test_bound_page_resolution_does_not_activate_or_close_any_page(self):
        first, second = Mock(), Mock()
        context = Mock(pages=[first, second])
        context.new_cdp_session.side_effect = [Mock(send=Mock(return_value={'targetInfo': {'targetId': 'other'}})),
                                               Mock(send=Mock(return_value={'targetInfo': {'targetId': 'bound'}}))]
        browser = Mock(contexts=[context])
        self.assertIs(human_input.target_page(browser, 'bound'), second)
        for page in (first, second):
            page.bring_to_front.assert_not_called()
            page.close.assert_not_called()
        context.new_page.assert_not_called()

    def test_missing_bound_page_never_falls_back(self):
        with self.assertRaises(BrowserError):
            human_input.target_page(Mock(contexts=[]), 'gone')

    def test_unique_visible_target_rejects_missing_or_duplicate_buttons(self):
        for count in (0, 2):
            locator = Mock()
            locator.filter.return_value.count.return_value = count
            with self.assertRaises(BrowserError):
                human_input.unique_visible(locator, '按钮')
            locator.click.assert_not_called()

    def test_missing_optional_dependency_stops_without_a_patchright_fallback(self):
        with patch.dict('sys.modules', {'playwright.sync_api': None}), \
             patch('bosshunter.recruiting.browser_backends.chrome.sync_playwright') as fallback:
            with self.assertRaises(BrowserError) as error:
                human_input.start_driver()
        self.assertEqual(error.exception.code, 'human_input_unavailable')
        fallback.assert_not_called()

    def test_chrome_normal_reply_keeps_existing_runtime_path(self):
        browser = BossBrowser(config_provider=lambda: {'browser': {'recruiting_browser_kind':'chrome'}})
        browser.evaluate = Mock(return_value={'attempted':True})
        with patch.object(human_input, 'start_driver') as start, patch.object(human_input, 'prepare_page') as prepare:
            browser.execute('reply', {'id':'1-0', 'position_title':'岗位', 'messages':[]}, '你好')
        self.assertEqual(browser.evaluate.call_count, 2)
        start.assert_not_called()
        prepare.assert_not_called()


class JobInputTests(unittest.TestCase):
    def setUp(self):
        self.verifier = RecommendVerifier(lambda: CONFIG)
        self.page, self.frame, self.tracker = Mock(), Mock(), Mock()
        self.options, self.menu, self.option = Mock(), Mock(), Mock()
        for locator in (self.menu, self.option):
            locator.filter.return_value = locator
            locator.count.return_value = 1
        self.options.filter.return_value = self.options
        self.options.count.return_value = 1
        self.frame.locator.side_effect = lambda selector: (
            self.options if selector == SELECTORS['job_options'] else
            self.menu if selector == SELECTORS['job_menu'] else self.option)
        self.tracker.evaluate.return_value = True
        p = patch.object(human_input, 'prepare_page')
        p.start(); self.addCleanup(p.stop)

    def test_already_open_menu_is_not_toggled_and_option_is_clicked_once(self):
        self.verifier._select_job_input(self.page, self.frame, self.tracker, 'new', Mock())
        self.menu.click.assert_not_called()
        self.option.hover.assert_called_once()
        self.option.click.assert_called_once()

    def test_closed_menu_is_opened_once(self):
        self.options.count.return_value = 0
        self.verifier._select_job_input(self.page, self.frame, self.tracker, 'new', Mock())
        self.menu.click.assert_called_once()
        self.option.click.assert_called_once()

    def test_replaced_option_is_not_clicked(self):
        self.tracker.evaluate.return_value = False
        with self.assertRaises(BrowserError):
            self.verifier._select_job_input(self.page, self.frame, self.tracker, 'new', Mock())
        self.option.click.assert_not_called()

    def test_account_change_during_movement_stops_before_selection(self):
        self.verifier._verify_session = Mock(side_effect=BrowserError('账号变化'))
        with self.assertRaises(BrowserError):
            self.verifier._select_job_input(self.page, self.frame, self.tracker, 'new', Mock())
        self.option.click.assert_not_called()


class GreetingTests(unittest.TestCase):
    def setUp(self):
        self.verifier = RecommendVerifier(lambda: CONFIG, cdp_url='http://127.0.0.1:9222')
        self.page, self.frame, self.button = Mock(), Mock(), Mock()
        self.frame.evaluate.side_effect = lambda script: 'job' if script == READ_JOB_ID else '继续沟通'
        self.button.inner_text.return_value = '打招呼'
        self.frame.evaluate_handle.return_value.evaluate.return_value = True
        self.driver = Mock()
        self.check = Mock()
        from contextlib import contextmanager
        @contextmanager
        def guard(page):
            yield self.check
        for name, value in (('_recommend_page', self.page), ('_recommend_frame', self.frame),
                            ('_greet_locator', self.button), ('_wait_greet_text', '继续沟通')):
            p = patch.object(self.verifier, name, return_value=value)
            p.start(); self.addCleanup(p.stop)
        p = patch.object(self.verifier, '_page_guard', guard)
        p.start(); self.addCleanup(p.stop)
        for name, value in (('start_driver', self.driver), ('prepare_page', None)):
            p = patch.object(human_input, name, return_value=value)
            p.start(); self.addCleanup(p.stop)

    def test_human_greeting_clicks_once_and_never_uses_dom_click(self):
        preflight = Mock()
        result = self.verifier.greet('candidate', 'job', preflight)
        self.assertTrue(result['sent'])
        self.button.hover.assert_called_once()
        self.button.click.assert_called_once()
        self.assertEqual(preflight.call_count, 2)
        self.assertFalse(any('btn.click()' in str(call) for call in self.frame.evaluate.call_args_list))
        self.driver.stop.assert_called_once()
        self.driver.chromium.connect_over_cdp.return_value.close.assert_not_called()

    def test_click_timeout_is_not_retried_or_replaced_with_js(self):
        self.button.click.side_effect = TimeoutError('uncertain')
        with self.assertRaises(BrowserError):
            self.verifier.greet('candidate', 'job')
        self.button.click.assert_called_once()
        self.driver.stop.assert_called_once()
        self.assertFalse(any('btn.click()' in str(call) for call in self.frame.evaluate.call_args_list))

    def test_click_guard_rejection_remains_uncertain_and_cleans_up(self):
        self.frame.evaluate_handle.return_value.evaluate.side_effect = [False, None]
        with self.assertRaises(BrowserError) as error:
            self.verifier.greet('candidate', 'job')
        self.assertEqual(error.exception.code, 'greeting_uncertain')
        self.button.click.assert_called_once()
        self.frame.evaluate_handle.return_value.dispose.assert_called_once()

    def test_cancellation_after_input_remains_uncertain(self):
        self.button.click.side_effect = TaskCancelled('stop')
        with self.assertRaises(BrowserError) as error:
            self.verifier.greet('candidate', 'job')
        self.assertEqual(error.exception.code, 'greeting_uncertain')

    def test_job_changed_while_hovering_stops_before_click(self):
        self.frame.evaluate.side_effect = ['job', 'other']
        with self.assertRaises(BrowserError):
            self.verifier.greet('candidate', 'job')
        self.button.click.assert_not_called()

    def test_preflight_cancellation_after_hover_does_not_claim_input(self):
        with self.assertRaises(TaskCancelled):
            self.verifier.greet('candidate', 'job', Mock(side_effect=[None, TaskCancelled('stop')]))
        self.button.click.assert_not_called()

    def test_already_greeted_does_not_move_or_click(self):
        self.button.inner_text.return_value = '继续沟通'
        result = self.verifier.greet('candidate', 'job')
        self.assertTrue(result['sent'])
        self.button.hover.assert_not_called()
        self.button.click.assert_not_called()
        human_input.prepare_page.assert_not_called()


class ContactScrollTests(unittest.TestCase):
    def setUp(self):
        self.browser = BossBrowser(config_provider=lambda: CONFIG, target_id='bound')
        self.browser.bound_target = Mock(return_value='bound')
        self.page = Mock(url='https://www.zhipin.com/web/chat/index', frames=[])
        self.position = {'x': 100, 'y': 120, 'delta': 300, 'at_bottom': False}
        self.page.evaluate.side_effect = lambda script: (
            self.position.copy() if script == CONTACT_SCROLL_TARGET else {'contacts': []} if 'return {contacts}' in script else '')
        self.driver = Mock()
        for module, name, kwargs in ((human_input, 'start_driver', {'return_value':self.driver}),
                                    (human_input, 'prepare_page', {}),
                                    (human_input, 'target_page', {'return_value':self.page}),
                                    (RecommendVerifier, '_find_cdp_url', {'return_value':'http://127.0.0.1:9222'})):
            p = patch.object(module, name, **kwargs)
            p.start(); self.addCleanup(p.stop)

    def test_wheel_targets_only_verified_contact_container(self):
        self.browser._scroll_contacts()
        self.page.mouse.move.assert_called_once_with(100, 120)
        self.page.mouse.wheel.assert_called_once_with(0, 300)
        self.page.mouse.click.assert_not_called()
        self.page.bring_to_front.assert_not_called()
        human_input.target_page.assert_called_once_with(self.driver.chromium.connect_over_cdp.return_value, 'bound')
        self.driver.stop.assert_called_once()

    def test_unknown_or_covered_scroll_area_stops_without_moving(self):
        for position in ({'missing':True}, {'blocked':True}):
            self.position = position
            with self.assertRaises(BrowserError):
                self.browser._scroll_contacts()
        self.page.mouse.move.assert_not_called()
        self.page.mouse.wheel.assert_not_called()

    def test_page_moving_during_pointer_travel_stops_before_wheel(self):
        self.page.mouse.move.side_effect = lambda *args: self.position.update(x=200)
        with self.assertRaises(BrowserError):
            self.browser._scroll_contacts()
        self.page.mouse.wheel.assert_not_called()

    def test_verification_page_pauses_layer_before_pointer_input(self):
        self.page.evaluate.side_effect = lambda script: 'verification_required'
        with self.assertRaises(BrowserError):
            self.browser._scroll_contacts()
        self.page.mouse.move.assert_not_called()
        self.page.mouse.wheel.assert_not_called()

    def test_same_chat_path_on_foreign_origin_never_receives_input(self):
        self.browser.account_pause_handler = Mock()
        for url in ('https://example.org/web/chat/index', 'http://www.zhipin.com/web/chat/index',
                    'https://www.zhipin.com:444/web/chat/index', 'https://user@www.zhipin.com/web/chat/index'):
            self.page.url = url
            with self.assertRaises(BrowserError):
                self.browser._scroll_contacts()
        self.page.mouse.move.assert_not_called()
        self.page.mouse.wheel.assert_not_called()
        self.browser.account_pause_handler.assert_not_called()

    def test_navigation_during_pointer_travel_stops_before_wheel(self):
        self.page.mouse.move.side_effect = lambda *args: setattr(self.page, 'url', 'https://example.org/web/chat/index')
        with self.assertRaises(BrowserError):
            self.browser._scroll_contacts()
        self.page.mouse.wheel.assert_not_called()

    def test_login_navigation_still_triggers_shared_account_pause(self):
        self.browser.account_pause_handler = Mock()
        self.page.mouse.move.side_effect = lambda *args: setattr(self.page, 'url', 'https://www.zhipin.com/web/user/')
        with self.assertRaises(BrowserError) as error:
            self.browser._scroll_contacts()
        self.assertEqual(error.exception.code, 'login_required')
        self.browser.account_pause_handler.assert_called_once_with(1800)
        self.page.mouse.wheel.assert_not_called()

    def test_stable_count_midway_through_scroll_does_not_end_loading(self):
        row = {'ident':'1-0', 'name':'test'}
        self.browser._scroll_contacts = Mock(side_effect=[
            {'contacts':[row], 'at_bottom':False} for _ in range(5)] + [
            {'contacts':[row], 'at_bottom':True} for _ in range(3)])
        self.browser.evaluate = Mock(return_value={'contacts':[row]})
        with patch('bosshunter.recruiting.browser.time.sleep'):
            self.assertEqual(self.browser.read_contact_list(True), [row])
        self.assertEqual(self.browser._scroll_contacts.call_count, 8)
        self.assertEqual(self.browser.contact_coverage['reason'], 'loaded_list_stable')

    def test_chrome_mode_retains_original_scroll_adapter(self):
        browser = BossBrowser(config_provider=lambda: {'browser': {'recruiting_browser_kind':'chrome'}})
        browser.evaluate = Mock(return_value={'contacts':[], 'count':0})
        with patch('bosshunter.recruiting.browser.time.sleep'):
            browser.read_contact_list(True)
        human_input.start_driver.assert_not_called()
