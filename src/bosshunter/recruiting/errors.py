"""Structured recruiting failures; diagnostic data must never contain page text."""
from urllib.parse import urlsplit


class BrowserError(RuntimeError):
    def __init__(self, message, *, code='browser_unavailable', stage='', diagnostics=None):
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.diagnostics = safe_diagnostics(diagnostics or {})
        self.diagnostic_id = None


class TaskCancelled(BrowserError):
    """Stop requested before the next browser operation."""


class ConversationNotSelected(BrowserError):
    """The bound conversation must be opened manually."""


class AccountPauseError(BrowserError):
    """Verification, login or identity requires human attention."""
    def __init__(self, message, **kwargs):
        kwargs.setdefault('code', 'account_paused')
        super().__init__(message, **kwargs)


def safe_url(value):
    """Keep a route, never URL credentials, query, fragment or candidate identifiers."""
    try:
        u = urlsplit(str(value))
        if u.scheme not in ('https', 'http') or not u.hostname:
            return ''
        parts = u.path.split('/')
        if len(parts) > 4 and parts[1:3] == ['web', 'frame']:
            parts = parts[:4] + ['[id]']
        return u.scheme + '://' + u.hostname + '/'.join(parts)[:160]
    except ValueError:
        return ''


def safe_diagnostics(data):
    out = {}
    for key in ('page_url', 'frame_url'):
        if key in data:
            out[key] = safe_url(data[key])
    for key in ('target_bound', 'frame_found', 'job_matches', 'list_ready', 'list_changed', 'loading'):
        if type(data.get(key)) is bool:
            out[key] = data[key]
    for key in ('candidate_count', 'http_status'):
        if type(data.get(key)) is int and 0 <= data[key] <= 100000:
            out[key] = data[key]
    for key in ('page_kind', 'exception_type'):
        value = data.get(key)
        if isinstance(value, str) and value.replace('_', '').isalnum():
            out[key] = value[:60]
    return out
