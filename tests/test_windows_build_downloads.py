"""Exercise build downloads with real HTTPResponse parsing and no network calls."""
import hashlib
from http.client import HTTPResponse
import importlib.util
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch
from urllib.error import URLError


SPEC = importlib.util.spec_from_file_location(
    'windows_download_support', Path(__file__).resolve().parents[1] / 'packaging/windows/build_support.py')
support = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(support)


def response(body, *, length=None, status=200):
    headers = f'HTTP/1.1 {status} Test\r\nContent-Length: {len(body) if length is None else length}\r\n\r\n'
    socket = Mock()
    socket.makefile.return_value = io.BytesIO(headers.encode('ascii') + body)
    result = HTTPResponse(socket)
    result.begin()
    return result


class BuildDownloadTests(TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        pause = patch.object(support.time, 'sleep')
        self.pause = pause.start()
        self.addCleanup(pause.stop)
        output = patch('builtins.print')
        self.output = output.start()
        self.addCleanup(output.stop)
        self.payload = b'complete synthetic archive'
        self.digest = hashlib.sha256(self.payload).hexdigest()
        self.target = self.root / 'node.zip'

    def download(self, **kwargs):
        return support.download_build_file('https://nodejs.org/dist/v22.1.2/node.zip', self.target,
                                           expected_sha256=self.digest, label='Node archive', **kwargs)

    def test_complete_verified_download_uses_timeout_and_publishes_no_partial_file(self):
        with patch.object(support, 'urlopen', return_value=response(self.payload)) as opened:
            self.assertEqual(self.download(), self.target)
        self.assertEqual(self.target.read_bytes(), self.payload)
        self.assertEqual(list(self.root.iterdir()), [self.target])
        self.assertEqual(opened.call_args.kwargs['timeout'], 30)
        self.assertEqual(opened.call_args.args[0].get_header('Cache-control'), 'no-cache')

    def test_verified_cache_is_reused_without_network(self):
        self.target.write_bytes(self.payload)
        with patch.object(support, 'urlopen') as opened:
            self.download()
        opened.assert_not_called()

    def test_corrupt_cache_is_replaced_only_after_retry_verifies_payload(self):
        self.target.write_bytes(b'old corrupt cache')
        def fetch(*args, **kwargs):
            self.assertEqual(self.target.read_bytes(), b'old corrupt cache')
            return response(b'interrupted payload') if opened.call_count == 1 else response(self.payload)
        with patch.object(support, 'urlopen', side_effect=fetch) as opened:
            self.download()
        self.assertEqual(opened.call_count, 2)
        self.assertEqual(self.target.read_bytes(), self.payload)
        self.assertEqual(list(self.root.iterdir()), [self.target])

    def test_persistent_wrong_hash_never_publishes_or_reuses_download(self):
        with patch.object(support, 'urlopen', side_effect=lambda *a, **k: response(b'<html>gateway</html>')) as opened:
            with self.assertRaisesRegex(RuntimeError, '3 attempts.*SHA-256 mismatch.*expected=.*actual='):
                self.download()
        self.assertEqual(opened.call_count, 3)
        self.assertEqual(self.pause.call_count, 2)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_truncated_content_length_is_not_promoted(self):
        with patch.object(support, 'urlopen', side_effect=lambda *a, **k: response(self.payload, length=200)):
            with self.assertRaisesRegex(RuntimeError, 'Incomplete download'):
                self.download()
        self.assertFalse(self.target.exists())
        self.assertFalse(list(self.root.glob('*.part')))

    def test_timeout_retries_are_bounded_and_do_not_erase_old_cache(self):
        self.target.write_bytes(b'old invalid cache')
        with patch.object(support, 'urlopen', side_effect=URLError('synthetic timeout')) as opened:
            with self.assertRaisesRegex(RuntimeError, '3 attempts.*synthetic timeout'):
                self.download()
        self.assertEqual(opened.call_count, 3)
        self.assertEqual(self.target.read_bytes(), b'old invalid cache')
        self.assertFalse(list(self.root.glob('*.part')))

    def test_partial_http_status_cannot_be_accepted_even_with_matching_hash(self):
        with patch.object(support, 'urlopen', side_effect=lambda *a, **k: response(self.payload, status=206)):
            with self.assertRaisesRegex(RuntimeError, 'Unexpected HTTP status 206'):
                self.download()
        self.assertFalse(self.target.exists())

    def test_checksum_response_size_limit_prevents_large_or_misrouted_response(self):
        with patch.object(support, 'urlopen', side_effect=lambda *a, **k: response(self.payload)):
            with self.assertRaisesRegex(RuntimeError, 'allowed size'):
                support.download_build_file('https://nodejs.org/sums', self.target, max_bytes=2)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_transfer_time_limit_stops_even_when_socket_keeps_receiving(self):
        def fetch(*args, **kwargs):
            return response(self.payload)
        with patch.object(support, 'urlopen', side_effect=fetch), \
                patch.object(support.time, 'monotonic', side_effect=[0, 61, 100, 161, 200, 261]):
            with self.assertRaisesRegex(RuntimeError, 'transfer time limit'):
                self.download(deadline_seconds=60)
        self.assertFalse(self.target.exists())

    def test_interrupted_build_cleans_up_its_partial_file_without_retry(self):
        with patch.object(support, 'urlopen', side_effect=KeyboardInterrupt) as opened:
            with self.assertRaises(KeyboardInterrupt):
                self.download()
        opened.assert_called_once()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_invalid_digest_is_rejected_before_network(self):
        with patch.object(support, 'urlopen') as opened:
            with self.assertRaisesRegex(ValueError, 'Invalid expected'):
                support.download_build_file('https://nodejs.org/file', self.target, expected_sha256='invalid')
        opened.assert_not_called()

    def test_node_archive_uses_exact_version_url_after_latest_manifest(self):
        name = 'node-v22.23.3-win-x64.zip'
        manifest = f'{self.digest}  {name}\n' + '0' * 64 + '  node-v22.23.3-win-arm64.zip\n'
        result = self.root / 'node-download.json'
        with patch.object(support, 'urlopen', side_effect=[response(manifest.encode()), response(self.payload)]) as opened:
            support.main(['--download-node', str(self.root), str(result)])
        urls = [call.args[0].full_url for call in opened.call_args_list]
        self.assertEqual(urls, ['https://nodejs.org/dist/latest-v22.x/SHASUMS256.txt',
                               f'https://nodejs.org/dist/v22.23.3/{name}'])
        selected = json.loads(result.read_text())
        self.assertEqual(Path(selected['archive']).read_bytes(), self.payload)
        self.assertEqual(Path(selected['folder']), self.root.resolve() / 'node-v22.23.3-win-x64')

    def test_malformed_manifest_invalidates_previous_result_and_never_downloads_archive(self):
        result = self.root / 'node-download.json'
        result.write_text('{"archive":"old-unverified.zip"}')
        with patch.object(support, 'urlopen', return_value=response(b'<html>error</html>')) as opened:
            with self.assertRaisesRegex(ValueError, 'Cannot identify one official'):
                support.download_node(self.root, result)
        opened.assert_called_once()
        self.assertFalse(result.exists())

    def test_manifest_rejects_ambiguous_entries_and_unsafe_filenames(self):
        entries = [f'{self.digest}  node-v22.1.2-win-x64.zip\n' * 2,
                   f'{self.digest}  ../node-v22.1.2-win-x64.zip\n',
                   f'{self.digest}  node-v24.1.2-win-x64.zip\n',
                   f'{self.digest}  node-v22.1.2-win-arm64.zip\n']
        for text in entries:
            with self.subTest(text=text), self.assertRaises(ValueError):
                support.node_archive_entry(text)

    def test_archive_failure_cannot_leave_success_result_for_powershell(self):
        result = self.root / 'node-download.json'
        result.write_text('old result')
        manifest = f'{self.digest}  node-v22.1.2-win-x64.zip\r\n'.encode()
        responses = [response(manifest)] + [response(b'wrong') for _ in range(3)]
        with patch.object(support, 'urlopen', side_effect=responses):
            with self.assertRaises(RuntimeError):
                support.download_node(self.root, result)
        self.assertFalse(result.exists())
        self.assertFalse(list(self.root.glob('*.zip')))
