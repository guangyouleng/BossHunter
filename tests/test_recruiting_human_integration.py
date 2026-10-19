"""Opt-in real browser tests against offline synthetic pages, with a fresh profile.

Set BOSSHUNTER_TEST_INPUT_BROWSER to a local Chromium executable. This never
uses the user's profile, existing CDP endpoint, cookies or BOSS website.
"""
import os
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import time
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from urllib.parse import quote

from bosshunter.recruiting.browser_backends import cloakbrowser as human_input
from bosshunter.recruiting.dom import WATCH_JOB_LIST, CONTACT_SCROLL_TARGET, ARM_GREET_CLICK, READ_CHAT
from bosshunter.recruiting.recommend import RecommendVerifier
from bosshunter.recruiting.browser import BossBrowser, BrowserError, TaskCancelled


@unittest.skipUnless(os.environ.get('BOSSHUNTER_TEST_INPUT_BROWSER'), 'opt-in offline browser test')
class OfflineHumanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Exercise the same CDP attachment as production, with our own profile.
        cls.profile = TemporaryDirectory(prefix='bosshunter-offline-input-')
        cls.addClassCleanup(cls.profile.cleanup)
        cls.process = subprocess.Popen([
            os.environ['BOSSHUNTER_TEST_INPUT_BROWSER'], '--headless=new',
            '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
            '--user-data-dir=' + cls.profile.name, '--disable-background-networking',
            '--disable-component-update', '--no-first-run', 'about:blank'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.addClassCleanup(cls.stop_owned_browser)
        port_file = Path(cls.profile.name) / 'DevToolsActivePort'
        deadline = time.monotonic() + 10
        while not port_file.is_file():
            if cls.process.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError('Isolated test browser did not expose its local CDP port')
            time.sleep(.05)
        port = int(port_file.read_text().splitlines()[0])
        cls.driver = human_input.start_driver()
        cls.addClassCleanup(cls.driver.stop)
        cls.browser = cls.driver.chromium.connect_over_cdp(f'http://127.0.0.1:{port}')

    @classmethod
    def stop_owned_browser(cls):
        if cls.process.poll() is None:
            cls.process.terminate()
            try:
                cls.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.process.kill()
                cls.process.wait(timeout=5)

    def setUp(self):
        self.context = self.browser.new_context(offline=True, viewport={'width':1280, 'height':900})
        self.context.route('**/*', lambda route: route.abort())
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()

    def local_html(self, html, frame=None):
        # Older Cloak builds suppress the console marker used by set_content.
        # data: navigation creates the same offline fixture without that API.
        (frame or self.page).goto('data:text/html;charset=utf-8,' + quote(html),
                                 wait_until='domcontentloaded', timeout=5000)

    def test_iframe_job_and_greeting_use_trusted_input_and_keep_refresh_gate(self):
        self.local_html('<iframe style="position:absolute;left:110px;top:90px;width:900px;height:650px"></iframe>')
        frame = self.page.frames[1]
        self.local_html('''
<style>.job-item{display:block;height:40px}.card-item{margin-top:750px}</style>
<div class="job-selecter-wrap"><button class="ui-dropmenu-label">岗位</button></div>
<div class="job-selecter-options"><div class="job-item curr" value="old">旧岗位</div><div class="job-item" value="new">新岗位</div></div>
<ul class="card-list"><li class="card-item"><div class="card-inner" data-geek="person"><button class="btn-greet">打招呼</button></div></li></ul>
<script>
window.clicks=[];
document.querySelector('[value=new]').addEventListener('click',event=>{
 window.clicks.push({kind:'job',trusted:event.isTrusted});
 document.querySelector('[value=old]').classList.remove('curr');event.currentTarget.classList.add('curr');
 document.querySelector('.card-inner').setAttribute('data-geekid','person');
});
document.querySelector('.btn-greet').addEventListener('click',event=>{
 window.clicks.push({kind:'greet',trusted:event.isTrusted});event.currentTarget.innerText='继续沟通';
});
</script>''', frame)
        verifier = RecommendVerifier(lambda: {'browser': {'recruiting_browser_kind':'cloakbrowser'}}, wait_timeout=10)
        tracker = frame.evaluate_handle(WATCH_JOB_LIST)
        try:
            verifier._select_job_input(self.page, frame, tracker, 'new', lambda: None)
            self.assertTrue(tracker.evaluate('state => state.clicked'))
            self.assertTrue(tracker.evaluate('state => state.read().changed'))
            button = verifier._greet_locator(frame, 'person')
            button.hover(timeout=15000)
            gate = frame.evaluate_handle(ARM_GREET_CLICK.replace('__UID__', '"person"').replace('__JOB_ID__', '"new"'))
            try:
                button.click(timeout=15000)
                self.assertTrue(gate.evaluate('state=>state.clicked && !state.blocked'))
            finally:
                gate.evaluate('state=>state.dispose()')
                gate.dispose()
            self.assertEqual(frame.evaluate('window.clicks'),
                             [{'kind':'job','trusted':True}, {'kind':'greet','trusted':True}])
            self.assertEqual(button.inner_text(), '继续沟通')
        finally:
            tracker.evaluate('state => {state.disarmClick?.();state.observer.disconnect();}')
            tracker.dispose()

    def test_wheel_scrolls_contact_list_without_selecting_a_conversation(self):
        self.local_html('''
<div id="list" style="position:absolute;left:35px;top:70px;width:260px;height:300px;overflow-y:auto">
<div class="geek-item" data-id="1-0" style="height:1200px"><span class="geek-name">test</span></div>
</div><script>window.clicks=0;window.wheels=[];
document.addEventListener('click',()=>window.clicks++);
document.addEventListener('wheel',event=>window.wheels.push(event.isTrusted));</script>''')
        position = self.page.evaluate(CONTACT_SCROLL_TARGET)
        self.assertFalse(position['blocked'])
        human_input.prepare_page(self.page)
        self.page.mouse.move(position['x'], position['y'])
        self.page.mouse.wheel(0, position['delta'])
        self.page.wait_for_function('document.querySelector("#list").scrollTop>0')
        self.assertEqual(self.page.evaluate('window.clicks'), 0)
        self.assertTrue(all(self.page.evaluate('window.wheels')))

    def test_other_playwright_page_is_not_patched(self):
        human_input.prepare_page(self.page)
        ordinary = self.context.new_page()
        self.assertFalse(hasattr(ordinary._impl_obj, '_cloak_human'))

    def test_unicode_newlines_do_not_press_enter_or_submit_early(self):
        self.local_html('''<div id="editor" contenteditable="true" style="white-space:pre-wrap"></div>
<script>window.enters=0;document.addEventListener('keydown',event=>{if(event.key==='Enter')window.enters++;});</script>''')
        human_input.prepare_page(self.page)
        self.page.locator('#editor').click()
        prefix = ''
        for character in '你好A1🙂\n再见':
            self.page.keyboard.insert_text(character)
            prefix += character
            rendered = self.page.locator('#editor').inner_text()
            self.assertTrue(rendered == prefix or (prefix.endswith('\n') and rendered == prefix+'\n'))
        self.assertEqual(self.page.evaluate('window.enters'), 0)

    def reply_fixture(self):
        # Serve a synthetic document locally under the adapter's required URL.
        # Offline context + route.abort for every other URL prevent platform traffic.
        html = '''<meta charset="utf-8"><div class="geek-item selected" data-id="1-0"><span class="geek-name">test</span></div>
<div class="job-content"><span class="position-name">岗位</span></div><div id="messages"></div>
<div id="boss-chat-editor-input" contenteditable="true" style="white-space:pre-wrap;width:400px;height:120px"></div>
<input id="other"><div class="submit-content"><button class="submit">发送</button></div>
<script>window.sent=0;window.enters=0;window.events=[];window.befores=[];
window.addEventListener('beforeinput',event=>window.befores.push({data:event.data,type:event.inputType}),true);
const editor=document.querySelector('#boss-chat-editor-input'),send=document.querySelector('.submit');
editor.addEventListener('input',event=>{
 window.events.push({data:event.data,type:event.inputType,trusted:event.isTrusted});
 send.classList.toggle('active',!!editor.innerText.trim());
 if(window.onTestInput)window.onTestInput();
});
send.addEventListener('click',event=>{
 window.sent++;window.sendTrusted=event.isTrusted;
 const message=document.createElement('div');message.className='message-item';
 message.innerHTML='<span class="item-myself"></span><span class="text-content"></span>';
 message.querySelector('.text-content').innerText=editor.innerText.trim();document.querySelector('#messages').append(message);
 editor.innerText='';send.classList.remove('active');
});
editor.addEventListener('keydown',event=>{if(event.key==='Enter'){window.enters++;send.click();}});
</script>'''
        self.context.route('https://www.zhipin.com/web/chat/index', lambda route: route.fulfill(status=200, body=html, content_type='text/html'))
        self.page.goto('https://www.zhipin.com/web/chat/index', wait_until='domcontentloaded')
        adapter = BossBrowser(config_provider=lambda: {'browser': {'recruiting_browser_kind':'cloakbrowser'}})
        @contextmanager
        def input_page():
            yield self.page, lambda: None
        adapter._input_page = input_page
        before = self.page.evaluate('() => {' + READ_CHAT + 'return snapshot;}')
        self.assertEqual(before['position_title'], '岗位')
        self.assertEqual(before['messages'], [])
        return adapter, before

    def test_cloak_reply_types_unicode_linebreaks_and_sends_exactly_once(self):
        adapter, before = self.reply_fixture()
        content = '你好A1🙂\n再见'
        try:
            result = adapter.execute('reply', before, content)
        except BrowserError as error:
            self.fail(str(error.__cause__) + repr(self.page.evaluate('({before:window.befores,inputs:window.events})')))
        self.assertTrue(result['attempted'])
        self.assertEqual(self.page.evaluate('window.sent'), 1)
        self.assertTrue(self.page.evaluate('window.sendTrusted'))
        self.assertEqual(self.page.evaluate('window.enters'), 0)
        self.assertEqual(self.page.locator('.text-content').inner_text(), content)
        events = self.page.evaluate('window.events')
        self.assertEqual([event['data'] if event['type']=='insertText' else '\n' for event in events], list(content))
        self.assertTrue(all(event['trusted'] for event in events))

    def test_existing_manual_draft_is_preserved(self):
        adapter, before = self.reply_fixture()
        self.page.locator('#boss-chat-editor-input').evaluate("editor=>editor.innerText='人工草稿'")
        with self.assertRaises(BrowserError):
            adapter.execute('reply', before, '自动内容')
        self.assertEqual(self.page.locator('#boss-chat-editor-input').inner_text(), '人工草稿')
        self.assertEqual(self.page.evaluate('window.sent'), 0)

    def test_windows_crlf_reply_is_sent_with_normal_browser_linebreaks(self):
        adapter, before = self.reply_fixture()
        with patch.object(human_input, 'character_pause'):
            self.assertTrue(adapter.execute('reply', before, '你好\r\n再见')['attempted'])
        self.assertEqual(self.page.evaluate('window.sent'), 1)
        self.assertEqual(self.page.evaluate('window.enters'), 0)
        self.assertEqual(self.page.locator('.text-content').inner_text(), '你好\n再见')

    def standard_reply_fixture(self):
        from bosshunter.recruiting.browser_backends.common import DomInputs
        adapter, before = self.reply_fixture()
        adapter.inputs = DomInputs()
        # Run the standard backend's exact JS against the offline fixture.
        adapter.evaluate = lambda script: json.loads(self.page.evaluate('() => {' + script + '}'))
        return adapter, before

    def test_standard_reply_accepts_windows_linebreaks_and_sends_once(self):
        adapter, before = self.standard_reply_fixture()
        self.assertTrue(adapter.execute('reply', before, '你好\r\n再见')['attempted'])
        self.assertEqual(self.page.evaluate('window.sent'), 1)
        self.assertEqual(self.page.locator('.text-content').inner_text(), '你好\n再见')

    def test_standard_reply_ignores_hidden_send_button(self):
        adapter, before = self.standard_reply_fixture()
        self.page.evaluate("() => { const send=document.querySelector('.submit'); const copy=send.cloneNode(true); copy.style.display='none'; send.before(copy); }")
        self.assertTrue(adapter.execute('reply', before, '你好')['attempted'])
        self.assertEqual(self.page.evaluate('window.sent'), 1)

    def test_standard_reply_rejects_two_ready_send_buttons(self):
        adapter, before = self.standard_reply_fixture()
        self.page.evaluate("() => { const send=document.querySelector('.submit'); window.onTestInput=()=>{ const copy=send.cloneNode(true); copy.classList.add('active'); send.after(copy); window.onTestInput=null; }; }")
        with self.assertRaises(BrowserError):
            adapter.execute('reply', before, '你好')
        self.assertEqual(self.page.evaluate('window.sent'), 0)
        self.assertEqual(self.page.locator('#boss-chat-editor-input').inner_text(), '你好')

    def test_standard_reply_rejects_hidden_editor_selected_before_visible_editor(self):
        adapter, before = self.standard_reply_fixture()
        self.page.evaluate("() => { const editor=document.querySelector('#boss-chat-editor-input'); const copy=editor.cloneNode(true); editor.after(copy); editor.style.display='none'; }")
        with self.assertRaises(BrowserError):
            adapter.execute('reply', before, '你好')
        self.assertEqual(self.page.evaluate('window.sent'), 0)
        self.assertEqual(self.page.locator('#boss-chat-editor-input').all_inner_texts(), ['', ''])

    def test_cloak_reply_rejects_editor_hidden_during_send_hover(self):
        adapter, before = self.reply_fixture()
        self.page.evaluate("() => { const send=document.querySelector('.submit'); send.addEventListener('mouseover',()=>{ const editor=document.querySelector('#boss-chat-editor-input'); const copy=editor.cloneNode(true); editor.after(copy); editor.style.display='none'; },{once:true}); }")
        with patch.object(human_input, 'character_pause'), self.assertRaises(BrowserError):
            adapter.execute('reply', before, '你好')
        self.assertEqual(self.page.evaluate('window.sent'), 0)

    def test_manual_edit_during_typing_stops_and_preserves_it(self):
        adapter, before = self.reply_fixture()
        self.page.evaluate("() => { window.onTestInput=()=>{document.querySelector('#boss-chat-editor-input').innerText+='人工';window.onTestInput=null;}; }")
        with patch.object(human_input, 'character_pause'), self.assertRaises(BrowserError):
            adapter.execute('reply', before, '自动内容')
        self.assertIn('人工', self.page.locator('#boss-chat-editor-input').inner_text())
        self.assertEqual(self.page.evaluate('window.sent'), 0)

    def test_focus_change_during_typing_does_not_type_in_other_field(self):
        adapter, before = self.reply_fixture()
        self.page.evaluate("() => { window.onTestInput=()=>document.querySelector('#other').focus(); }")
        with patch.object(human_input, 'character_pause'), self.assertRaises(BrowserError):
            adapter.execute('reply', before, '自动内容')
        self.assertEqual(self.page.locator('#other').input_value(), '')
        self.assertEqual(self.page.evaluate('window.sent'), 0)

    def test_message_change_during_typing_prevents_send(self):
        adapter, before = self.reply_fixture()
        self.page.evaluate("() => { window.onTestInput=()=>{document.querySelector('#messages').innerHTML='<div class=message-item><span class=item-friend></span><span class=text-content>新消息</span></div>';}; }")
        with patch.object(human_input, 'character_pause'), self.assertRaises(BrowserError):
            adapter.execute('reply', before, '自动内容')
        self.assertEqual(self.page.evaluate('window.sent'), 0)
        self.assertEqual(self.page.locator('#boss-chat-editor-input').inner_text(), '自')

    def test_cancellation_while_typing_preserves_partial_draft_without_sending(self):
        adapter, before = self.reply_fixture()
        def preflight():
            if self.page.locator('#boss-chat-editor-input').inner_text():
                raise TaskCancelled('stop')
        with patch.object(human_input, 'character_pause'), self.assertRaises(TaskCancelled):
            adapter.execute('reply', before, '自动内容', preflight)
        self.assertEqual(self.page.locator('#boss-chat-editor-input').inner_text(), '自')
        self.assertEqual(self.page.evaluate('window.sent'), 0)

    def test_focus_race_after_character_guard_does_not_insert_in_other_field(self):
        adapter, before = self.reply_fixture()
        insert = self.page.keyboard.insert_text
        def steal_focus(character):
            self.page.locator('#other').focus()
            return insert(character)
        with patch.object(human_input, 'character_pause'), \
             patch.object(self.page.keyboard, 'insert_text', side_effect=steal_focus), \
             self.assertRaises(BrowserError):
            adapter.execute('reply', before, '自动内容')
        self.assertEqual(self.page.locator('#other').input_value(), '')
        self.assertEqual(self.page.evaluate('window.sent'), 0)

    def test_message_race_during_send_click_blocks_submission(self):
        adapter, before = self.reply_fixture()
        unique = human_input.unique_visible
        clicks = []
        def wrap(locator, description):
            locator = unique(locator, description)
            if description != '发送按钮':
                return locator
            from unittest.mock import Mock
            proxy = Mock(wraps=locator)
            def click(**kwargs):
                clicks.append(True)
                self.page.evaluate("document.querySelector('#messages').innerHTML='<div class=message-item><span class=item-friend></span><span class=text-content>新消息</span></div>'")
                return locator.click(**kwargs)
            proxy.click.side_effect = click
            return proxy
        with patch.object(human_input, 'character_pause'), patch.object(human_input, 'unique_visible', side_effect=wrap), \
             self.assertRaises(BrowserError):
            adapter.execute('reply', before, '自动内容')
        self.assertEqual(len(clicks), 1)
        self.assertEqual(self.page.evaluate('window.sent'), 0)
        self.assertEqual(self.page.locator('#boss-chat-editor-input').inner_text(), '自动内容')

    def test_send_button_cannot_submit_partial_reply(self):
        adapter, before = self.reply_fixture()
        self.page.evaluate("() => { window.onTestInput=()=>document.querySelector('.submit').click(); }")
        self.assertEqual(self.page.evaluate('window.sent'), 0)
        with patch.object(human_input, 'character_pause'), self.assertRaises(BrowserError):
            adapter.execute('reply', before, '自动内容')
        self.assertEqual(self.page.locator('#boss-chat-editor-input').inner_text(), '自')
        self.assertEqual(self.page.evaluate('window.sent'), 0)
