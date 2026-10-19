"""Shared DOM checks and explicitly selected standard DOM input behavior."""
import json
import time
from ..dom import READ_CHAT, CLICK_GREET, OPEN_JOB_MENU, PICK_JOB, SCROLL_CONTACT_LIST, render_dom
from ..errors import BrowserError


def confirmation_text(text):
    """Browsers render CRLF/CR as LF; preserve all other message content."""
    return text.replace('\r\n', '\n').replace('\r', '\n')


def unique_visible(locator, description):
    locator = locator.filter(visible=True)
    if locator.count() != 1:
        raise BrowserError(f'无法唯一定位可见的{description}，已停止操作', code='input_target_ambiguous')
    return locator


def target_page(browser, target_id):
    for context in browser.contexts:
        for page in context.pages:
            session = context.new_cdp_session(page)
            try:
                ident = session.send('Target.getTargetInfo')['targetInfo']['targetId']
            finally:
                session.detach()
            if ident == target_id:
                return page
    raise BrowserError('绑定的招聘标签页已关闭；不会操作其他标签页')


def reply_guard(before, expected_text=None):
    expected = json.dumps({k: before[k] for k in ('id', 'position_title', 'messages')}, ensure_ascii=False)
    guard = READ_CHAT + render_dom("""
        const expected=EXPECTED;
        if(snapshot.id!==expected.id || snapshot.position_title!==expected.position_title || JSON.stringify(snapshot.messages)!==JSON.stringify(expected.messages)) throw Error('会话或消息已变化，已停止发送');
        if(!snapshot.editor_empty) throw Error('编辑器中有人工输入，已停止自动操作');
        const visible=e=>!!e && e.getClientRects().length>0 && getComputedStyle(e).visibility!=='hidden';
        const uniqueControl=(selector,label)=>{
            const matches=[...document.querySelectorAll(selector)].filter(visible);
            if(matches.length!==1) throw Error('无法唯一定位可见的'+label+'，已停止操作');
            return matches[0];
        };
        if(uniqueControl(__dom_chat_editor__,'回复编辑器')!==editor) throw Error('当前回复编辑器不可见或已变化，停止操作');
        if([...document.querySelectorAll(__dom_active_dialog__)].some(visible)) throw Error('请先关闭当前弹窗');
    """).replace('EXPECTED', expected)
    if expected_text is not None:
        guard = guard.replace("if(!snapshot.editor_empty) throw Error('编辑器中有人工输入，已停止自动操作');",
            'if(editor.innerText!==' + json.dumps(expected_text, ensure_ascii=False) + ") throw Error('回复草稿已被修改，停止发送');")
    return guard


class DomInputs:
    """Original DOM actions, also used when humanize is explicitly disabled."""
    humanized = False

    def greet(self, verifier, page, frame, uid, job_id, preflight, check, mark_attempt):
        mark_attempt()
        return frame.evaluate(CLICK_GREET.replace('__JOB_ID__', json.dumps(str(job_id))).replace('__UID__', json.dumps(uid)))

    def select_job(self, verifier, page, frame, tracker, job_id, check):
        if not frame.evaluate(OPEN_JOB_MENU):
            raise BrowserError('未找到岗位下拉框，停止切换', code='job_menu_missing', diagnostics={'page_url':page.url,'frame_url':frame.url})
        deadline = time.monotonic() + verifier._wait_timeout
        while not tracker.evaluate(PICK_JOB.replace('__JOB_ID__', json.dumps(str(job_id)))):
            check()
            if time.monotonic() >= deadline:
                raise BrowserError('未能在下拉框选中该岗位，请确认该岗位仍处于开放状态', code='job_option_missing', diagnostics={'page_url':page.url,'frame_url':frame.url})
            page.wait_for_timeout(100)

    def scroll_contacts(self, adapter):
        return adapter.evaluate(SCROLL_CONTACT_LIST)

    def reply(self, adapter, before, content, preflight=None):
        adapter.evaluate(reply_guard(before) + render_dom("""
            const send=uniqueControl(__dom_send_button__,'发送控件');
            editor.focus(); editor.textContent=CONTENT;
            editor.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:CONTENT}));
            return JSON.stringify({prepared:true});
        """).replace('CONTENT', json.dumps(content, ensure_ascii=False)))
        if preflight:
            preflight()
        return adapter.evaluate(reply_guard(before, content) + render_dom("""
            const send=uniqueControl(__dom_send_ready__,'就绪发送按钮');
            send.click();
            return JSON.stringify({attempted:true});
        """))
