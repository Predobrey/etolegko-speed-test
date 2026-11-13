import ssl
import threading
import unittest
from unittest.mock import patch
from urllib.error import URLError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from speed_test import REQUEST_COUNT, download_once, run_speed_test, validate_url

PAYLOAD = b"speed-test-payload-" * 4096


class CountingHandler(BaseHTTPRequestHandler):
	request_count = 0

	def do_GET(self) -> None:  # noqa: N802 — имя метода задано классом BaseHTTPRequestHandler
		type(self).request_count += 1
		self.send_response(200)
		self.send_header("Content-Type", "application/octet-stream")
		self.send_header("Content-Length", str(len(PAYLOAD)))
		self.end_headers()
		self.wfile.write(PAYLOAD)

	def log_message(self, _format: str, *_args: object) -> None:
		# Не выводим служебные сообщения HTTP-сервера во время тестов.
		return


class SpeedTestTests(unittest.TestCase):
	def test_rejects_non_http_url(self) -> None:
		with self.assertRaises(ValueError):
			validate_url("ftp://example.com/file")

	def test_reports_tls_certificate_verification_errors_clearly(self) -> None:
		tls_error = ssl.SSLCertVerificationError(
			1, "certificate verify failed: unable to get local issuer certificate"
		)

		with patch("speed_test.urlopen", side_effect=URLError(tls_error)):
			with self.assertRaisesRegex(RuntimeError, "TLS-сертификат сервера 'example.com'"):
				download_once("https://example.com/file", number=1, timeout=5)

	def test_downloads_ten_responses_sequentially_and_counts_body_bytes(self) -> None:
		CountingHandler.request_count = 0
		server = ThreadingHTTPServer(("127.0.0.1", 0), CountingHandler)
		server_thread = threading.Thread(target=server.serve_forever, daemon=True)
		server_thread.start()

		try:
			host, port = server.server_address
			results, total_elapsed = run_speed_test(f"http://{host}:{port}/file")
		finally:
			server.shutdown()
			server.server_close()
			server_thread.join(timeout=2)

		self.assertEqual(len(results), REQUEST_COUNT)
		self.assertEqual(CountingHandler.request_count, REQUEST_COUNT)
		self.assertEqual(sum(result.downloaded_bytes for result in results), len(PAYLOAD) * REQUEST_COUNT)
		self.assertTrue(all(result.status_code == 200 for result in results))
		self.assertGreater(total_elapsed, 0)


if __name__ == "__main__":
	unittest.main()
