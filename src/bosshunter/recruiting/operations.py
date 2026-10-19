"""Short browser leases shared by Web/worker; no HTTP pacing waits under a lease."""
from contextlib import contextmanager, nullcontext
from functools import wraps
from threading import local
from uuid import uuid4
from .errors import BrowserError, TaskCancelled
from .store import encode, now


class BrowserCoordinator:
    def __init__(self, store):
        self.store = store
        self._local = local()

    @contextmanager
    def operation(self, label):
        if getattr(self._local, 'depth', 0):
            self._local.depth += 1
            try:
                yield
            finally:
                self._local.depth -= 1
            return
        token = self.store.owner.rsplit(':', 1)[0] + ':' + uuid4().hex
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            busy = self.store.task_active(db, 'browser-dom')
            if not busy:
                db.execute('INSERT OR REPLACE INTO task_claims VALUES (?,?,?)', ('browser-dom', token, now()))
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',
                           ('browser_operation', encode({'status':'running', 'operation':label, 'started_at':now()})))
        if busy:
            exc = BrowserError('浏览器正在执行其他招聘操作，请稍后重试；未执行本次操作',
                               code='browser_busy', stage=label)
            # Background batch acquisition has no decorated adapter or API catch.
            # Save only after releasing the transaction; never nest SQLite writers.
            self.store.record_browser_failure(exc)
            raise exc
        self._local.depth = 1
        status = 'done'
        try:
            yield
        except BaseException:
            status = 'failed'
            raise
        finally:
            self._local.depth = 0
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT owner FROM task_claims WHERE key=?', ('browser-dom',)).fetchone()
                if row and row['owner'] == token:
                    db.execute('DELETE FROM task_claims WHERE key=? AND owner=?', ('browser-dom',token))
                    db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',
                               ('browser_operation', encode({'status':status,'operation':label,'finished_at':now()})))


def browser_operation(label):
    def decorate(fn):
        @wraps(fn)
        def wrapped(self, *args, **kwargs):
            scope = getattr(self, 'operation_scope', None)
            try:
                with scope(label) if scope else nullcontext():
                    return fn(self, *args, **kwargs)
            except (TaskCancelled, ValueError, PermissionError):
                raise
            except Exception as original:
                exc = original if isinstance(original, BrowserError) else BrowserError(
                    '浏览器操作中断，请检查页面状态；未自动重试', code='browser_operation_failed',
                    diagnostics={'exception_type': type(original).__name__})
                exc.stage = exc.stage or label
                handler = getattr(self, 'diagnostic_handler', None)
                if handler and not exc.diagnostic_id:
                    handler(exc)
                if exc is original:
                    raise
                raise exc from original
        return wrapped
    return decorate
