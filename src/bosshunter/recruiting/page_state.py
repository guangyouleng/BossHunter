"""Known BOSS page states. Never suppress navigation or solve verification."""
from .dom import PAGE_STATUS

from urllib.parse import urlsplit, parse_qs
from .errors import AccountPauseError, BrowserError

from .dom import PAGE_RULES
SECURITY_PATHS = frozenset(PAGE_RULES['security_paths'])


def page_url_kind(url):
    try:
        parsed = urlsplit(str(url)); host = parsed.hostname or ''
        if parsed.scheme not in ('http', 'https') or not (host == 'zhipin.com' or host.endswith('.zhipin.com')):
            return ''
        path = parsed.path.rstrip('/') or '/'
        if path in SECURITY_PATHS or (path == '/' and '_security_check' in parse_qs(parsed.query, keep_blank_values=True)):
            return 'verification_required'
        if path in PAGE_RULES['login_paths']:
            return 'login_required'
        return ''
    except ValueError:
        return ''


def platform_security_url(url):
    return page_url_kind(url) == 'verification_required'


ISSUES = {
    'login_required': ('BOSS 登录已失效或需要登录，已暂停，请在 Chrome 人工登录', True),
    'verification_required': ('BOSS 要求安全验证，已暂停，请在 Chrome 人工处理', True),
    'platform_restricted': ('BOSS 显示限制或辅助工具提醒，已暂停，请在 Chrome 核实账号', True),
    'paywall': ('BOSS 显示付费或权益不足提示，未继续操作；请人工核对', False),
    'modal_blocked': ('BOSS 当前有未处理弹窗，未继续操作；请在 Chrome 人工处理', False),
}


def page_issue_error(kind, *, diagnostics=None):
    message, pause = ISSUES.get(kind, ('BOSS 页面状态无法核实，停止本次操作', False))
    return (AccountPauseError if pause else BrowserError)(message, code=kind,
        diagnostics={**(diagnostics or {}), 'page_kind':kind})


# Return categories and structural facts only, never modal/body/chat text.
