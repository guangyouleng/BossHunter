"""State-based selection must never treat old cards or a label click as a refresh."""
import unittest
from unittest.mock import Mock, patch
from bosshunter.recruiting.browser import BrowserError
from bosshunter.recruiting.recommend import RecommendVerifier
from test_recruiting_recommend import Page, Frame, PW, Browser, Context, SyncPlaywright, RECOMMEND_PAGE, FRAME_URL


class JobTransitionTests(unittest.TestCase):
    def run_selection(self, states, result=True):
        tracker = Mock()
        reads = iter([*states, None])
        tracker.evaluate.side_effect = lambda script: result if 'item.click(); return true' in script else next(reads)
        frame = Frame(FRAME_URL, result)
        frame.evaluate_handle = Mock(return_value=tracker)
        page = Page(RECOMMEND_PAGE, [frame])
        pw = PW(Browser(Context([page])))
        verifier = RecommendVerifier(lambda: {}, cdp_url='http://127.0.0.1:9222', wait_timeout=2)
        ticks = iter([n * .2 for n in range(100)])
        with patch('bosshunter.recruiting.recommend.sync_playwright', return_value=SyncPlaywright(pw)), \
             patch('bosshunter.recruiting.recommend.time.monotonic', side_effect=lambda: next(ticks)):
            try:
                value = verifier.select_job('new')
                return value, tracker
            finally:
                self.assertTrue(tracker.dispose.called)
                self.assertTrue(pw.stopped)

    def test_label_change_with_stale_cards_is_not_success(self):
        states = [{'job_id':'old'}, *[{'job_id':'new','ready':True,'changed':False,'revision':0}] * 12]
        with self.assertRaisesRegex(BrowserError, '尚未核实'):
            self.run_selection(states)

    def test_loading_or_wrong_job_never_succeeds(self):
        for state in ({'job_id':'new','ready':False,'changed':True,'revision':1},
                      {'job_id':'old','ready':True,'changed':True,'revision':1}):
            with self.assertRaises(BrowserError):
                self.run_selection([{'job_id':'old'}, *[state]*12])

    def test_refreshed_stable_cards_succeed(self):
        state = {'job_id':'new','ready':True,'changed':True,'revision':1}
        self.assertTrue(self.run_selection([{'job_id':'old'}, *[state]*8])[0])

    def test_current_job_ready_does_not_require_refresh(self):
        state = {'job_id':'new','ready':True,'changed':False,'revision':0}
        self.assertTrue(self.run_selection([state]*9)[0])

    def test_changed_cards_must_settle_again(self):
        states=[{'job_id':'old'}]
        states += [{'job_id':'new','ready':True,'changed':True,'revision':n} for n in range(12)]
        with self.assertRaises(BrowserError):
            self.run_selection(states)


if __name__ == '__main__': unittest.main()
