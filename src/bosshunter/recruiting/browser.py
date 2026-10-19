"""Employer DOM adapter using the project's built-in Browser Runtime.

No raw browser/JS endpoint is exposed by the recruiting API. Every write is an
explicit action with a current conversation check. Invitation submission does
not exist in this adapter.
"""
from .dom import READ_CHAT, READ_CONTACT_LIST, render_dom
from .browser_backends import load_backend
from .browser_backends.common import reply_guard

import json
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from bosshunter.browser.client import RuntimeClient
from bosshunter.browser.runtime import ensure_runtime
from .policy import check_reply
from .errors import BrowserError, TaskCancelled, ConversationNotSelected, AccountPauseError
from .operations import browser_operation
from .page_state import platform_security_url, page_url_kind, page_issue_error, PAGE_STATUS


def refusal_cooldown(retry_after):
    """At least 30 minutes; honor a longer Retry-After in either HTTP format."""
    try:
        extra = int(retry_after) if retry_after.isdigit() else (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()
        return max(1800, extra)
    except (TypeError, ValueError, OverflowError):
        return 1800

class BossBrowser:
    def __init__(self, runtime=None, target_id=None, config_provider=None):
        self.runtime = runtime or RuntimeClient()
        self.target_id = target_id
        self.config_provider = config_provider or (lambda: {})
        self.inputs = load_backend(self.config_provider())

    @staticmethod
    def recruiter_target(target):
        if not isinstance(target, dict) or target.get("type") != "page" or not target.get("targetId"):
            return False
        try:
            url = urlsplit(target.get("url", ""))
            return (url.scheme == "https" and url.hostname == "www.zhipin.com"
                    and url.port in (None, 443) and not url.username and not url.password
                    and url.path.startswith("/web/chat/"))
        except (TypeError, ValueError):
            return False

    def bound_target(self):
        ensure_runtime()  # 确保原项目 Node Browser Runtime 已启动（发送消息走它）
        health = self.runtime.health()
        if not isinstance(health, dict) or health.get("runtime") != "bosshunter":
            raise BrowserError("BossHunter Browser Runtime 未连接，请检查原项目浏览器服务及 Chrome 调试连接")
        # /targets reconnects the runtime's CDP session without creating or activating tabs.
        all_targets = self.runtime.targets()
        if self.target_id:
            bound = next((t for t in all_targets if t.get('targetId') == self.target_id), None)
            kind = page_url_kind(bound.get('url', '')) if bound else ''
            if kind:
                self._raise_page_issue(kind, {'page_url':bound['url'], 'target_bound':True})
        targets = [t for t in all_targets if self.recruiter_target(t)]
        if self.target_id:
            if not any(t['targetId'] == self.target_id for t in targets):
                raise BrowserError("绑定的招聘标签页已关闭或离开招聘端，停止操作；不会切换到其他标签页")
        elif len(targets) == 1:
            self.target_id = targets[0]['targetId']
        else:
            raise BrowserError("无法唯一确定招聘标签页，请核对已打开的 BOSS 招聘页面；不会自动选择或新建标签页")
        return self.target_id

    def _raise_page_issue(self, kind, diagnostics):
        exc = page_issue_error(kind, diagnostics=diagnostics)
        if isinstance(exc, AccountPauseError) and getattr(self, 'account_pause_handler', None):
            self.account_pause_handler(refusal_cooldown(''))
        raise exc

    @browser_operation('读取页面状态')
    def evaluate(self, body, *, allow_modal=False):
        target_id = self.bound_target()
        # Guard again inside the selected page in case it navigated after /targets.
        probe = "const pageIssue=" + PAGE_STATUS + ";if(pageIssue && !(pageIssue==='modal_blocked' && " + ("true" if allow_modal else "false") + ")) return JSON.stringify({page_issue:pageIssue,diagnostic:{page_url:location.origin+location.pathname,target_bound:true}});"
        code = "(()=>{try{if(location.origin!=='https://www.zhipin.com'||!location.pathname.startsWith('/web/chat/')) throw Error('绑定页面已离开招聘端');" + probe + body + "}catch(e){return JSON.stringify({adapter_error:String(e.message)});}})()"
        value = self.runtime.evaluate(target_id, code, timeout=25)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                pass
        if isinstance(value, dict) and value.get('page_issue'):
            self._raise_page_issue(value['page_issue'], value.get('diagnostic', {}))
        if isinstance(value, dict) and value.get("adapter_error"):
            raise BrowserError(str(value["adapter_error"])[:300])
        if not isinstance(value, dict) or not value or value.get("error"):
            raise BrowserError("Browser Runtime 未返回有效页面结果，请核对绑定页面；不会自动重试操作")
        return value

    def status(self):
        return self.evaluate(render_dom("return JSON.stringify({connected:true,host:location.hostname,path:location.pathname,recruiter:!!document.querySelector(__dom_jobs_navigation__)});"))

    def read_current(self):
        return self.evaluate(READ_CHAT + "return JSON.stringify(snapshot);")

    @browser_operation('核对浏览器账号')
    def verify_account(self, ident, expected_account, on_refusal=None):
        match = re.fullmatch(r'([1-9][0-9]*)-([01])', ident)
        if not match or not expected_account:
            raise AccountPauseError('发送前缺少已核实的招聘账号身份，请先绑定并同步会话')
        gid, source = match.groups()
        args = json.dumps({'gid': gid, 'src': source, 'account': str(expected_account)})
        value = self.evaluate('return (async()=>{try{const expected=' + args + render_dom(r""";
            if(location.pathname!=='/web/chat/index' || document.querySelector(__dom_chat_selected__)?.getAttribute('data-id')!==expected.gid+'-'+expected.src)
                return JSON.stringify({account_verified:false});
            const params=new URLSearchParams({gid:expected.gid,src:expected.src,maxMsgId:'0',c:'1',page:'1'});
            const response=await fetch('/wapi/zpchat/boss/historyMsg?'+params,{credentials:'same-origin',redirect:'error'});
            if(!response.ok) return JSON.stringify({account_verified:false,refusal_status:response.status,retry_after:response.headers.get('Retry-After')||''});
            const body=await response.json();
            const messages=body?.zpData?.messages;
            if(body.code!==0 || !Array.isArray(messages) || !messages.length) return JSON.stringify({account_verified:false});
            const valid=messages.every(m=>{
                const incoming=String(m.from?.uid)===expected.gid, outgoing=String(m.to?.uid)===expected.gid;
                const peer=incoming?m.from:m.to, account=incoming?m.to:m.from;
                return incoming!==outgoing && String(peer?.source)===expected.src && String(account?.uid)===expected.account;
            });
            return JSON.stringify({account_verified:valid});
        }catch(e){return JSON.stringify({account_verified:false});}})();
        """))
        if value.get('account_verified') is not True:
            if on_refusal:
                on_refusal(refusal_cooldown(value.get('retry_after', '')))
            raise AccountPauseError('浏览器当前招聘账号与已绑定账号不一致或无法核实，停止外发')

    @browser_operation('核对当前会话')
    def open_conversation(self, ident):
        # Never navigate or switch the user's selected conversation as a side effect.
        previous = self.read_current()
        if previous.get("id") != ident:
            raise ConversationNotSelected("当前页面不是绑定会话，请在 Chrome 手动打开该会话；未切换页面")
        time.sleep(.35)
        snapshot = self.read_current()
        if snapshot != previous:
            raise BrowserError("当前会话仍在变化，请核对后重试；未执行发送")
        return snapshot

    def read_contact_list(self, load_all=False, before_load=None):
        """读取聊天页左侧联系人列表（含岗位名）；只读，不点选、不导航、不发消息。

        load_all=True 时先反复滚动到底触发懒加载，直到没有新增联系人，再一次性读取全部；
        联系人列表是滚动加载的，只读当前 DOM 会漏掉未加载的人。
        """
        collected = {}
        reason = 'visible_dom'
        if load_all:
            last = -1
            stable = 0
            for step in range(200):
                if before_load:
                    before_load(step == 0)
                value = self._scroll_contacts()
                for contact in value.get('contacts', []):
                    if contact.get('ident'):
                        collected[contact['ident']] = contact
                count = len(collected) if collected else value.get('count', 0)
                if count == last and (not self.inputs.humanized or value.get('at_bottom') is True):
                    stable += 1
                    if stable >= 3:
                        reason = 'loaded_list_stable'
                        break
                else:
                    stable = 0
                    last = count
                time.sleep(0.8)
            else:
                reason = 'scroll_limit'
        value = self.evaluate(READ_CONTACT_LIST + "return JSON.stringify({contacts});")
        for contact in value.get('contacts', []):
            if contact.get('ident'):
                collected[contact['ident']] = contact
        self.contact_coverage = {'complete': False, 'scope': 'loaded_browser_contacts',
                                 'reason': reason, 'loaded': len(collected),
                                 'note': '仅包含当前页面可加载的联系人，未证明覆盖全账号历史联系人'}
        return list(collected.values())

    @browser_operation('滚动加载联系人')
    def _scroll_contacts(self):
        return self.inputs.scroll_contacts(self)

    @contextmanager
    def _input_page(self):
        # Shared exact-target discovery and page risk checks; providers only
        # implement input. Import locally to avoid the verifier/adapter cycle.
        from .recommend import RecommendVerifier
        target = self.bound_target()
        verifier = RecommendVerifier(self.config_provider,
            account_pause_handler=getattr(self, 'account_pause_handler', None))
        verifier._target_id = target
        verifier.cancel_check = getattr(self, 'cancel_check', None)
        pw = self.inputs.start_driver()
        try:
            browser = pw.chromium.connect_over_cdp(verifier._find_cdp_url())
            page = self.inputs.target_page(browser, target)
            def require_chat_page():
                if (not self.recruiter_target({'type':'page', 'targetId':target, 'url':page.url})
                        or urlsplit(page.url).path != '/web/chat/index'):
                    raise BrowserError('绑定页面已离开 BOSS 招聘端沟通页，停止操作；不会切换页面',
                                       code='bound_page_changed', diagnostics={'page_url':page.url, 'target_bound':True})
            if not page_url_kind(page.url):
                require_chat_page()
            with verifier._page_guard(page) as risk_check:
                def check():
                    # Known login/restriction pages must still reach the shared
                    # pause handler; unknown origins must receive no DOM input.
                    if not page_url_kind(page.url):
                        require_chat_page()
                    risk_check()
                check()
                yield page, check
                check()
        finally:
            pw.stop()  # Disconnect only; keep the user's browser and tabs open.

    @browser_operation('读取岗位说明')
    def read_position(self, expected_title, expected_job_id):
        return self.evaluate(render_dom(r"""
            if(location.hostname!=='www.zhipin.com'||location.pathname!=='/web/chat/job/edit') throw Error('请在 Chrome 职位管理中打开目标岗位；只读取，不保存平台表单');
            const f=[...document.querySelectorAll(__dom_frames__)].find(e=>new URL(e.src,location.href).pathname==='/web/frame/job/edit');
            const d=f?.contentDocument;
            const title=d?.querySelector(__dom_job_title_input__)?.value.trim();
            const jd=d?.querySelector(__dom_textarea__)?.value.trim();
            const urls=[new URL(location.href),new URL(f?.src || location.href,location.href)];
            const ids=urls.flatMap(u=>['encryptJobId','jobId','jobid','id'].map(k=>u.searchParams.get(k))).filter(Boolean);
            if(!ids.includes(EXPECTED_ID)) throw Error('当前岗位平台 ID 未能核实，停止读取 JD');
            if(title!==EXPECTED || !jd) throw Error('当前平台岗位与绑定会话不一致，已停止读取');
            return JSON.stringify({title,jd,source:'boss_job_form'});
        """).replace('EXPECTED_ID', json.dumps(expected_job_id)).replace("EXPECTED", json.dumps(expected_title, ensure_ascii=False)))

    @browser_operation('提交会话操作')
    def execute(self, kind, before, content="", preflight=None):
        if preflight:
            preflight()
        if kind == "invitation":
            raise BrowserError("本次测试禁止发送面试邀约")
        if kind not in {"reply", "request_resume", "accept_resume"}:
            raise BrowserError("动作不在执行白名单内")
        if kind == "reply":
            check_reply(content)
            return self.inputs.reply(self, before, content, preflight)
        guard = reply_guard(before)
        if kind == "request_resume":
            self.evaluate(guard + render_dom("""
                if(snapshot.messages.some(m=>/简历请求已发送|索取.*简历/.test(m.text))) throw Error('已有简历请求记录，请人工核对，避免重复索取');
                if([...document.querySelectorAll(__dom_attachment_button__)].some(e=>e.innerText.includes('点击预览附件简历'))) throw Error('当前已收到附件，无需再次索取');
                const b=[...document.querySelectorAll(__dom_toolbar_button__)].find(e=>e.innerText.trim()==='求简历');
                if(!visible(b)) throw Error('找不到求简历入口');
                b.click(); return JSON.stringify({opened:true});
            """))
            if preflight:
                preflight()
            return self.evaluate(guard + render_dom("""
                const dialog=[...document.querySelectorAll(__dom_exchange_dialog__)].find(e=>visible(e)&&e.innerText.includes('确定向牛人索取简历吗'));
                const b=dialog?.querySelector(__dom_confirm_button__);
                if(!visible(b)) throw Error('求简历确认框不匹配');
                b.click(); return JSON.stringify({attempted:true});
            """), allow_modal=True)
        return self.evaluate(guard + render_dom("""
            const cards=[...document.querySelectorAll(__dom_message_card__)].filter(e=>e.innerText.includes('对方想发送附件简历给您'));
            const b=cards.flatMap(e=>[...e.querySelectorAll(__dom_card_button__)]).find(e=>e.innerText.trim()==='同意'&&!e.classList.contains('disabled'));
            if(!visible(b)) throw Error('没有可接收的新简历申请卡');
            b.click(); return JSON.stringify({attempted:true});
        """))
