"""Browser input providers; the registry controls runtime selection.

Do not eagerly import providers: Chrome must not require CloakBrowser or its
Playwright monkey patches to be installed.
"""
from importlib import import_module
from ..errors import BrowserError


PROVIDERS = {'chrome': '.chrome', 'cloakbrowser': '.cloakbrowser'}
BROWSER_LABELS = {'chrome': 'Google Chrome', 'cloakbrowser': 'CloakBrowser'}


def browser_choices():
    return {kind: BROWSER_LABELS[kind] for kind in PROVIDERS}


def load_backend(config):
    settings = (config or {}).get('browser', {})
    kind = settings.get('recruiting_browser_kind', 'chrome')
    module = PROVIDERS.get(kind) if isinstance(kind, str) else None
    if module is None:
        raise BrowserError('所选浏览器未启用或已移除，请重新选择浏览器', code='browser_backend_unavailable')
    try:
        provider = import_module(module, __name__)
    except ImportError as exc:
        raise BrowserError('所选浏览器组件不可用，请安装对应依赖或重新选择浏览器',
                           code='browser_backend_unavailable') from exc
    return provider.Backend(settings)
