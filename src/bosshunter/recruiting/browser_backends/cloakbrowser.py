"""CloakBrowser provider; all optional driver and behavioral code lives here."""
import json
import time
from .common import DomInputs, unique_visible, target_page
from .reply_state import create_state
from ..dom import SELECTORS, ARM_JOB_CLICK, ARM_GREET_CLICK, READ_CONTACT_LIST, CONTACT_SCROLL_TARGET
from ..errors import BrowserError, TaskCancelled


def enabled(config):
    settings = (config or {}).get('browser', {})
    return (settings.get('recruiting_browser_kind', 'chrome') == 'cloakbrowser'
            and settings.get('cloakbrowser_humanize', True) is True)


def start_driver():
    try:
        from playwright.sync_api import sync_playwright
        from cloakbrowser.human import patch_page, resolve_config  # noqa: F401
    except ImportError as exc:
        raise BrowserError('CloakBrowser 交互依赖未安装，请安装项目 recruiting、cloakbrowser 可选依赖后重启',
                           code='human_input_unavailable') from exc
    return sync_playwright().start()


def prepare_page(page):
    from cloakbrowser.human import patch_page, resolve_config
    patch_page(page, resolve_config('default', overrides={'mistype_chance': 0.0}))


def character_pause(previous, character):
    from cloakbrowser.human import resolve_config
    from cloakbrowser.human.keyboard import key_gap
    config = resolve_config('default', overrides={'mistype_chance': 0.0})
    time.sleep(min(.8, key_gap(config, previous, character) / 1000))


class Backend(DomInputs):
    kind = 'cloakbrowser'

    def __init__(self, settings):
        self.humanized = settings.get('cloakbrowser_humanize', True) is True

    @staticmethod
    def start_driver():
        return start_driver()

    @staticmethod
    def target_page(browser, target_id):
        return target_page(browser, target_id)

    @staticmethod
    def greet_locator(frame, uid):
        value = json.dumps(str(uid))
        identity = frame.locator(f':is({SELECTORS["recommend_identity"]})[data-geekid={value}], '
                                 f':is({SELECTORS["recommend_identity"]})[data-geek={value}]')
        card = unique_visible(frame.locator(SELECTORS['recommend_cards']).filter(has=identity), '候选人卡片')
        return unique_visible(card.locator(SELECTORS['greet_button']), '打招呼按钮')

    def greet(self, verifier, page, frame, uid, job_id, preflight, check, mark_attempt):
        if not self.humanized:
            return super().greet(verifier, page, frame, uid, job_id, preflight, check, mark_attempt)
        button = verifier._greet_locator(frame, uid)
        label = button.inner_text().strip()
        if label != '打招呼':
            return 'already' if label == '继续沟通' else 'unavailable'
        prepare_page(page)
        button.hover(timeout=verifier._wait_timeout * 1000)
        if preflight:
            preflight()
        verifier._verify_session(page)
        check()
        if verifier._job_id_from_frame(frame) != job_id or button.inner_text().strip() != '打招呼':
            raise BrowserError('岗位或招呼按钮已变化，停止发送', code='job_identity_changed')
        gate = frame.evaluate_handle(ARM_GREET_CLICK.replace('__UID__', json.dumps(str(uid)))
                                    .replace('__JOB_ID__', json.dumps(job_id)))
        try:
            mark_attempt()
            button.click(timeout=verifier._wait_timeout * 1000)
            if gate.evaluate('state => state.clicked && !state.blocked') is not True:
                raise BrowserError('招呼点击时页面身份变化或未核实点击，结果待核实', code='greeting_uncertain')
        finally:
            self.dispose_state(gate)
        return 'clicked'

    def select_job(self, verifier, page, frame, tracker, job_id, check):
        if not self.humanized:
            return super().select_job(verifier, page, frame, tracker, job_id, check)
        prepare_page(page)
        if frame.locator(SELECTORS['job_options']).filter(visible=True).count() == 0:
            menu = unique_visible(frame.locator(SELECTORS['job_menu']), '岗位下拉框')
            check()
            menu.click(timeout=verifier._wait_timeout * 1000)
        value = json.dumps(str(job_id))
        option = frame.locator(f':is({SELECTORS["job_options"]})[value={value}]').filter(visible=True)
        option.wait_for(state='visible', timeout=verifier._wait_timeout * 1000)
        option = unique_visible(option, '目标岗位')
        option.hover(timeout=verifier._wait_timeout * 1000)
        verifier._verify_session(page)
        check()
        if not tracker.evaluate(ARM_JOB_CLICK.replace('__JOB_ID__', value)):
            raise BrowserError('岗位选项已变化，停止切换', code='job_option_missing')
        option.click(timeout=verifier._wait_timeout * 1000)
        if tracker.evaluate('state => state.clicked') is not True:
            raise BrowserError('未核实目标岗位的点击，停止读取', code='job_transition_timeout')

    def scroll_contacts(self, adapter):
        if not self.humanized:
            return super().scroll_contacts(adapter)
        with adapter._input_page() as (page, check):
            position = page.evaluate(CONTACT_SCROLL_TARGET)
            if position.get('missing') or position.get('blocked'):
                raise BrowserError('无法核实联系人滚动区域，停止加载', code='contact_scroll_unavailable')
            if not position.get('empty'):
                prepare_page(page)
                page.mouse.move(position['x'], position['y'])
                check()
                current = page.evaluate(CONTACT_SCROLL_TARGET)
                if (current.get('missing') or current.get('blocked') or current.get('empty')
                        or abs(current['x']-position['x']) > 2 or abs(current['y']-position['y']) > 2):
                    raise BrowserError('联系人滚动区域已变化，停止加载', code='contact_scroll_unavailable')
                page.mouse.wheel(0, current['delta'])
                check()
            after = page.evaluate(CONTACT_SCROLL_TARGET)
            value = page.evaluate('() => {' + READ_CONTACT_LIST + 'return {contacts};}')
            return {**value, 'at_bottom':after.get('at_bottom') is True}

    @staticmethod
    def dispose_state(state):
        try:
            state.evaluate('state => state.dispose()')
        except Exception:
            pass
        try:
            state.dispose()
        except Exception:
            pass

    def reply(self, adapter, before, content, preflight=None):
        if not self.humanized:
            return super().reply(adapter, before, content, preflight)
        attempted = False
        try:
            with adapter._input_page() as (page, check):
                # Verify the current empty editor before even focusing it.
                from .common import reply_guard
                page.evaluate('() => {' + reply_guard(before) + 'return true;}')
                editor = unique_visible(page.locator(SELECTORS['chat_editor']), '回复编辑器')
                prepare_page(page)
                editor.click(timeout=10000)
                check()
                if preflight:
                    preflight()
                state = create_state(page, before, content)
                try:
                    previous = ''
                    for character in content:
                        character_pause(previous, character)
                        check()
                        if preflight:
                            preflight()
                        state.evaluate('state => state.arm(' + json.dumps(character, ensure_ascii=False) + ')')
                        # Do not press Enter, select all, clear, paste or simulate typos.
                        page.keyboard.insert_text(character)
                        state.evaluate('state => state.finishCharacter()')
                        previous = character
                    send = page.locator(SELECTORS['send_ready']).filter(visible=True)
                    send.wait_for(state='visible', timeout=5000)
                    send = unique_visible(send, '发送按钮')
                    send.hover(timeout=10000)
                    check()
                    if preflight:
                        preflight()
                    state.evaluate('state => state.armSend()')
                    attempted = True
                    send.click(timeout=10000)
                    if state.evaluate('state => state.clicked && !state.interfered') is not True:
                        raise BrowserError('未确认回复发送点击，结果待核实', code='reply_uncertain')
                    return {'attempted':True}
                finally:
                    self.dispose_state(state)
        except TaskCancelled as exc:
            if attempted:
                raise BrowserError('已进入回复发送点击步骤，结果待核实；禁止自动重试', code='reply_uncertain') from exc
            raise
