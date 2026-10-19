"""Chrome input provider: retain the existing Patchright and DOM actions."""
from patchright.sync_api import sync_playwright
from .common import DomInputs


class Backend(DomInputs):
    kind = 'chrome'

    def __init__(self, settings):
        pass

    @staticmethod
    def start_driver():
        return sync_playwright().start()
