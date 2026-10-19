"""Candidate verification and scoped input on the BOSS recommend page.

Connects to the selected running browser over CDP, reusing its login,
then reloads the recommend page and reads
the fresh first candidate card. BOSS only refreshes the recommend list on a real
browser reload, not on in-app page switches, so the reload is mandatory.

Never creates, activates, closes, or switches tabs. Discovery reloads the bound
recommend page; greeting clicks the authorized card and sends BOSS's default greeting.
"""
from .dom import READ_GREET_TEXT, READ_JOB_ID, WATCH_JOB_LIST, READ_CANDIDATES
from .browser_backends import load_backend

import json
import time
from contextlib import contextmanager
from urllib.parse import urlparse, urljoin

import httpx

from .operations import browser_operation
from .page_state import page_url_kind, page_issue_error, PAGE_STATUS
from .browser import AccountPauseError, BrowserError, TaskCancelled, refusal_cooldown, platform_security_url

RECOMMEND_URL_PREFIX = "https://www.zhipin.com/web/chat/recommend"
RECOMMEND_FRAME_PATH = "/web/frame/recommend/"
DEFAULT_CHROME_PORTS = [9222, 9229, 9333]

class RecommendVerifier:
    def __init__(self, config_provider, cdp_url=None, wait_timeout=15.0, cookie_loader=None,
                 account_pause_handler=None):
        self.config_provider = config_provider
        self.inputs = load_backend(config_provider())
        self._cdp_url = cdp_url
        self._wait_timeout = wait_timeout
        self._target_id = self._bound_target_id()
        self._cookie_loader = cookie_loader
        self._account_pause_handler = account_pause_handler

    @contextmanager
    def _page_guard(self, page):
        failure = []

        def response_received(response):
            parsed = urlparse(response.url)
            host = parsed.hostname or ''
            if not (host == 'zhipin.com' or host.endswith('.zhipin.com')):
                return
            security_page = page_url_kind(response.url)
            if not (parsed.path.startswith('/wapi/') or RECOMMEND_FRAME_PATH in parsed.path
                    or parsed.path in {'/web/chat/recommend', '/web/chat/index'} or security_page):
                return
            headers = response.headers
            location = headers.get('location', '')
            verification = location and page_url_kind(urljoin(response.url, location))
            if not (security_page or response.status in {403, 429}
                    or (300 <= response.status < 400 and verification)):
                return
            if not failure:
                failure.append(AccountPauseError('招聘页面请求被拒绝、限流或要求验证；已暂停，请在浏览器核实账号',
                    code=security_page or verification or 'platform_restricted',
                    diagnostics={'http_status':response.status,'page_url':response.url}))
                if self._account_pause_handler:
                    self._account_pause_handler(refusal_cooldown(headers.get('retry-after', '')))

        def check():
            if failure:
                raise failure[0]
            if getattr(self, 'cancel_check', None):
                self.cancel_check()
            issue = next((page_url_kind(url) for url in [page.url, *(frame.url for frame in page.frames)] if page_url_kind(url)), '')
            if issue:
                failure.append(page_issue_error(issue, diagnostics={'page_url':page.url, 'target_bound':bool(self._target_id)}))
                if self._account_pause_handler:
                    self._account_pause_handler(refusal_cooldown(''))
                raise failure[0]
            issue = page.evaluate(PAGE_STATUS)
            if isinstance(issue, str) and issue:
                if issue in {'login_required', 'verification_required', 'platform_restricted'}:
                    failure.append(page_issue_error(issue, diagnostics={'page_url':page.url, 'target_bound':bool(self._target_id)}))
                    if self._account_pause_handler:
                        self._account_pause_handler(refusal_cooldown(''))
                raise page_issue_error(issue, diagnostics={'page_url':page.url, 'target_bound':bool(self._target_id)})

        page.on('response', response_received)
        try:
            check()
            yield check
            check()
        except AccountPauseError:
            if not failure and self._account_pause_handler:
                self._account_pause_handler(refusal_cooldown(''))
            raise
        except Exception:
            check()
            raise
        finally:
            page.remove_listener('response', response_received)

    def _chrome_ports(self):
        config = self.config_provider() or {}
        browser = config.get("browser", {}) if isinstance(config, dict) else {}
        ports = browser.get("chrome_ports", DEFAULT_CHROME_PORTS)
        if not isinstance(ports, list) or not ports:
            return DEFAULT_CHROME_PORTS
        return [int(p) for p in ports]

    def _find_cdp_url(self):
        if self._cdp_url:
            return self._cdp_url
        candidates = []
        for port in self._chrome_ports():
            url = f"http://127.0.0.1:{port}"
            try:
                response = httpx.get(f"{url}/json/version", timeout=2, trust_env=False)
                if response.status_code == 200 and response.json().get("webSocketDebuggerUrl"):
                    if self._target_id:
                        targets = httpx.get(f'{url}/json/list', timeout=2, trust_env=False).json()
                        if not any(t.get('id') == self._target_id for t in targets):
                            continue
                    candidates.append(url)
            except (httpx.HTTPError, ValueError):
                continue
        if len(candidates) != 1:
            raise BrowserError('Chrome 调试实例无法唯一确定，请绑定目标标签页；不会选取其他实例')
        self._cdp_url = candidates[0]
        return self._cdp_url

    def _bound_target_id(self):
        config = self.config_provider() or {}
        browser = config.get("browser", {}) if isinstance(config, dict) else {}
        return browser.get("recruiting_target_id") or None

    def _cdp_page_targets(self):
        """通过 CDP /json/list 读取当前调试实例的所有 page target（含 id 与 url）。"""
        cdp_url = self._find_cdp_url()
        try:
            response = httpx.get(f"{cdp_url}/json/list", timeout=2, trust_env=False)
            response.raise_for_status()
            return [t for t in response.json() if isinstance(t, dict) and t.get("type") == "page"]
        except (httpx.HTTPError, ValueError):
            return []

    def _recommend_page(self, browser):
        """确定唯一要操作的推荐页：优先用绑定的 recruiting_target_id，否则要求唯一。

        多窗口/多推荐页/多账号时不再「取第一个」，避免连错页面；目标失效或出现
        多个可选页面就停止，不自动换另一页。
        """
        contexts = browser.contexts if hasattr(browser, 'contexts') else [browser]
        pages = [p for c in contexts for p in c.pages if p.url and
                 urlparse(p.url).scheme == 'https' and urlparse(p.url).hostname == 'www.zhipin.com'
                 and urlparse(p.url).path.rstrip('/') == '/web/chat/recommend']
        target_id = self._target_id
        if target_id:
            for context in contexts:
                for page in context.pages:
                    if page_url_kind(page.url) and self._page_target_id(page) == target_id:
                        if self._account_pause_handler:
                            self._account_pause_handler(refusal_cooldown(''))
                        raise page_issue_error(page_url_kind(page.url), diagnostics={'page_url':page.url,'target_bound':True})
            for page in pages:
                if self._page_target_id(page) == target_id:
                    return page
            raise BrowserError('绑定的推荐页已关闭或离开推荐页；不会切换到其他标签页')
        if len(pages) == 1:
            self._target_id = self._page_target_id(pages[0])
            return pages[0]
        if not pages:
            raise BrowserError("请先在 Chrome 打开 BOSS 牛人推荐页（/web/chat/recommend）")
        raise BrowserError("发现多个推荐页，无法唯一确定；请关闭多余页面或配置 browser.recruiting_target_id")

    @staticmethod
    def _page_target_id(page):
        session = page.context.new_cdp_session(page)
        try:
            return session.send('Target.getTargetInfo')['targetInfo']['targetId']
        finally:
            session.detach()

    @staticmethod
    def _recommend_frame(page):
        for frame in page.frames:
            if frame.url and urlparse(frame.url).hostname == 'www.zhipin.com' and urlparse(frame.url).path.startswith(RECOMMEND_FRAME_PATH):
                return frame
        return None

    def _verify_session(self, page):
        if self._cookie_loader is None:
            return
        # Compare only in memory; never persist or return cookies in diagnostics.
        expected = {(c.name, c.value) for c in self._cookie_loader() if c.name in {'wt2', 'zp_at'}}
        actual = {(c['name'], c['value']) for c in page.context.cookies(['https://www.zhipin.com'])
                  if c['name'] in {'wt2', 'zp_at'}}
        if not expected or expected != actual:
            raise AccountPauseError('推荐页登录账号与本地已核实的登录会话不一致，停止操作', code='account_mismatch')

    @staticmethod
    def _job_id_from_frame(frame):
        # 选中岗位在推荐页 iframe 顶部的职位下拉框里（li.job-item.curr 的 value 属性），
        # 不在 iframe URL 的 jobid 参数里（该参数加载后为 null）。
        value = frame.evaluate(READ_JOB_ID)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def _wait_greet_text(self, frame, uid, check=lambda: None):
        """等待按钮文案明确变为「继续沟通」，才视为发送成功。"""
        deadline = time.time() + self._wait_timeout
        while time.time() < deadline:
            check()
            text = frame.evaluate(READ_GREET_TEXT.replace("__UID__", json.dumps(uid)))
            if isinstance(text, str) and "继续沟通" in text:
                return text
            frame.page.wait_for_timeout(500)
        check()
        return frame.evaluate(READ_GREET_TEXT.replace("__UID__", json.dumps(uid)))

    def _input_driver(self):
        return self.inputs.start_driver()

    def _greet_locator(self, frame, uid):
        return self.inputs.greet_locator(frame, uid)

    def _select_job_input(self, page, frame, tracker, job_id, check):
        return self.inputs.select_job(self, page, frame, tracker, job_id, check)

    @browser_operation('提交主动招呼')
    def greet(self, uid, expected_job_id=None, preflight=None):
        """对已通过跨刷新验证的候选人点「打招呼」，BOSS 自动发默认招呼语。

        返回 {sent, job_id, reason}。先读 jobid（读不到就不点，避免发了却无法记录），
        若传了 expected_job_id 则点击前核对其与页面当前岗位一致（不一致就不点，避免
        误点错误岗位的候选人），再点击，最后确认按钮文案明确变为「继续沟通」。
        """
        cdp_url = self._find_cdp_url()
        pw = None
        attempted = False
        try:
            pw = self._input_driver()
            browser = pw.chromium.connect_over_cdp(cdp_url)
            page = self._recommend_page(browser)
            with self._page_guard(page) as check:
                self._verify_session(page)
                check()
                frame = self._recommend_frame(page)
                if not frame:
                    raise BrowserError('未找到推荐页 iframe，请刷新推荐页后重试', code='recommend_frame_missing',
                        diagnostics={'page_url':page.url,'frame_found':False,'target_bound':bool(self._target_id)})
                job_id = self._job_id_from_frame(frame)
                if not job_id:
                    raise BrowserError("推荐页未选择岗位，无法记录招呼；请先在下拉框选择岗位")
                if expected_job_id and str(job_id) != str(expected_job_id):
                    raise BrowserError("推荐页当前岗位与任务岗位不一致，已停止招呼")
                if preflight:
                    preflight()
                self._verify_session(page)
                check()
                def mark_attempt():
                    nonlocal attempted
                    attempted = True
                state = self.inputs.greet(self, page, frame, uid, job_id, preflight, check, mark_attempt)
                if state == "missing":
                    raise BrowserError("未找到该候选人的卡片，页面可能已刷新，请重新验证")
                if state == "already":
                    if '继续沟通' not in str(frame.evaluate(READ_GREET_TEXT.replace('__UID__', json.dumps(uid)))):
                        raise BrowserError('无法确认此前招呼结果，请人工核实')
                    return {"sent": True, "job_id": job_id, "reason": "该候选人已打过招呼（按钮已不是「打招呼」）"}
                if state != 'clicked':
                    raise BrowserError('打招呼按钮不可用，未执行发送')
                text = self._wait_greet_text(frame, uid, check)
                sent = isinstance(text, str) and "继续沟通" in text
                return {"sent": sent, "job_id": job_id,
                        "reason": "已发出招呼，按钮变为「继续沟通」" if sent else "点击后未确认按钮变化，结果待核实"}
        except TaskCancelled as exc:
            if attempted:
                raise BrowserError('已进入招呼点击步骤，停止时结果尚未核实；请人工核实，禁止自动重试',
                                   code='greeting_uncertain') from exc
            raise
        finally:
            if pw is not None:
                pw.stop()

    @browser_operation('切换并核验岗位')
    def select_job(self, job_id):
        """在推荐页下拉框选中指定岗位；切换岗位会刷新候选人列表。"""
        cdp_url = self._find_cdp_url()
        pw = None
        try:
            pw = self._input_driver()
            browser = pw.chromium.connect_over_cdp(cdp_url)
            page = self._recommend_page(browser)
            with self._page_guard(page) as check:
                self._verify_session(page)
                check()
                frame = self._recommend_frame(page)
                if not frame:
                    raise BrowserError('未找到推荐页 iframe，请刷新推荐页后重试', code='recommend_frame_missing',
                        diagnostics={'page_url':page.url,'frame_found':False,'target_bound':bool(self._target_id)})
                tracker = frame.evaluate_handle(WATCH_JOB_LIST)
                try:
                    initial = tracker.evaluate('state => state.read()')
                    already_selected = initial.get('job_id') == str(job_id)
                    if not already_selected:
                        self._select_job_input(page, frame, tracker, job_id, check)
                    deadline = time.monotonic() + self._wait_timeout
                    stable_since, revision = None, None
                    while time.monotonic() < deadline:
                        check()
                        current = tracker.evaluate('state => state.read()')
                        ready = (current.get('job_id') == str(job_id) and current.get('ready') is True
                                 and (already_selected or current.get('changed') is True))
                        if not ready or current.get('revision') != revision:
                            stable_since = None
                        revision = current.get('revision')
                        if ready:
                            stable_since = stable_since if stable_since is not None else time.monotonic()
                            if time.monotonic() - stable_since >= .5:
                                self._verify_session(page)
                                check()
                                return True
                        page.wait_for_timeout(100)
                    raise BrowserError('岗位切换未完成：选中岗位或候选列表尚未核实，停止读取；请检查推荐页',
                        code='job_transition_timeout', diagnostics={'page_url':page.url,'frame_url':frame.url,
                            'job_matches':current.get('job_id') == str(job_id), 'list_ready':current.get('ready') is True,
                            'list_changed':current.get('changed') is True, 'loading':current.get('loading') is True,
                            'candidate_count':current.get('count', 0), 'frame_found':True})
                finally:
                    try:
                        tracker.evaluate('state => { state.disarmClick?.(); state.observer.disconnect(); }')
                    except Exception:
                        pass  # The document may have been destroyed; preserve the actual failure.
                    try:
                        tracker.dispose()
                    except Exception:
                        pass
        finally:
            if pw is not None:
                pw.stop()

    @browser_operation('读取推荐列表')
    def read_candidates(self, expected_job_id=None):
        """读取推荐页当前展示的全部候选人（name + uid + 是否可招呼）。"""
        cdp_url = self._find_cdp_url()
        pw = None
        try:
            pw = self._input_driver()
            browser = pw.chromium.connect_over_cdp(cdp_url)
            page = self._recommend_page(browser)
            with self._page_guard(page) as check:
                self._verify_session(page)
                check()
                frame = self._recommend_frame(page)
                if not frame:
                    raise BrowserError('未找到推荐页 iframe，请刷新推荐页后重试', code='recommend_frame_missing',
                        diagnostics={'page_url':page.url,'frame_found':False,'target_bound':bool(self._target_id)})
                if expected_job_id and self._job_id_from_frame(frame) != str(expected_job_id):
                    raise BrowserError('读取前岗位已变化，停止读取推荐列表', code='job_identity_changed')
                value = frame.evaluate(READ_CANDIDATES)
                if expected_job_id and self._job_id_from_frame(frame) != str(expected_job_id):
                    raise BrowserError('读取期间岗位已变化，丢弃推荐列表', code='job_identity_changed')
                if not isinstance(value, list):
                    raise BrowserError('推荐列表结构不符合预期，停止读取', code='dom_structure_changed')
                return [{"name": str(c.get("name") or "").strip(), "uid": str(c.get("uid") or "").strip(),
                         "greetable": bool(c.get("greetable"))} for c in value if isinstance(c, dict) and c.get("uid")]
        finally:
            if pw is not None:
                pw.stop()

    @browser_operation('刷新推荐页')
    def reload(self):
        """刷新推荐页以获取新的候选人列表（BOSS 只在真实刷新时更新推荐）。

        刷新后岗位下拉框会恢复到默认第一个岗位，调用方需在刷新后重新 select_job
        选中目标岗位，再 read_candidates。
        """
        cdp_url = self._find_cdp_url()
        pw = None
        try:
            pw = self._input_driver()
            browser = pw.chromium.connect_over_cdp(cdp_url)
            page = self._recommend_page(browser)
            with self._page_guard(page) as check:
                self._verify_session(page)
                check()
                page.reload(wait_until="domcontentloaded")
                # 等推荐 iframe 重新加载出来，供后续 select_job/read_candidates 使用
                deadline = time.time() + self._wait_timeout
                while time.time() < deadline and not self._recommend_frame(page):
                    # Sync Playwright dispatches frame-attached/navigated events during
                    # its own API calls. A Python sleep leaves page.frames stale.
                    page.wait_for_timeout(500)
                    check()
                if not self._recommend_frame(page):
                    raise BrowserError('刷新后未找到推荐页 iframe，请确认推荐页已重新加载', code='recommend_frame_missing',
                        diagnostics={'page_url':page.url,'frame_found':False,'target_bound':bool(self._target_id)})
                return True
        finally:
            if pw is not None:
                pw.stop()
