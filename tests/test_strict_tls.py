"""Real HTTPS evidence for the optional strict OpenSSL host profile."""
import contextlib
import http.server
import importlib.util
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def command(directory, *args):
    subprocess.run(["openssl", *args], cwd=directory, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=60)


def chain(directory, weak):
    # 2047 differs from the rounded strength estimate accepted at level 2.
    for name in ("root", "intermediate", "leaf"):
        bits = "2047" if name == weak else "3072"
        command(directory, "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:" + bits,
                "-out", name + ".key")
        command(directory, "req", "-new", "-key", name + ".key", "-out", name + ".csr", "-subj", "/CN=" + name)
        ca = name != "leaf"
        (directory / (name + ".ext")).write_text(
            "basicConstraints=critical,CA:" + ("TRUE" if ca else "FALSE") + "\n" +
            ("keyUsage=critical,keyCertSign,cRLSign\n" if ca else
             "keyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=IP:127.0.0.1\n"))
        issuer = "root" if name != "leaf" else "intermediate"
        signing = ["-signkey", "root.key"] if name == "root" else ["-CA", issuer + ".pem", "-CAkey", issuer + ".key"]
        command(directory, "x509", "-req", "-in", name + ".csr", *signing,
                "-set_serial", str(("root", "intermediate", "leaf").index(name) + 1),
                "-days", "1", "-sha256", "-extfile", name + ".ext", "-out", name + ".pem")
    (directory / "chain.pem").write_bytes((directory / "leaf.pem").read_bytes() +
                                         (directory / "intermediate.pem").read_bytes())


class StrictTlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="strict-tls-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        for weak in ("none", "leaf", "intermediate", "root"):
            path = cls.directory / weak
            path.mkdir()
            chain(path, weak)

    @contextlib.contextmanager
    def endpoint(self, weak, tls):
        path = self.directory / weak
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.set_ciphers("DEFAULT:@SECLEVEL=0")  # Only the synthetic weak-key server.
        context.minimum_version = context.maximum_version = tls
        context.load_cert_chain(path / "chain.pem", path / "leaf.key")
        requests = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                requests.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with patch.dict(os.environ, {"SSL_CERT_FILE": str(path / "root.pem"),
                                        "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}):
                yield "https://127.0.0.1:" + str(server.server_port), requests
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_actual_openers_reject_2047_bit_keys_before_credentials(self):
        for tls in (ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3):
            for weak in ("none", "leaf", "intermediate", "root"):
                with self.subTest(tls=tls, weak=weak), self.endpoint(weak, tls) as (url, requests):
                    with patch.dict(os.environ, {"OTTPLAY_TLS_SECURITY_LEVEL": "3"}):
                        openers = self.openers(url)
                    for name, opener in openers.items():
                        with self.subTest(client=name):
                            request = Request(url, headers={"Authorization": "Bearer synthetic-only"})
                            if weak == "none":
                                with opener.open(request, timeout=5) as response:
                                    self.assertEqual(response.read(), b"{}")
                            else:
                                with self.assertRaises(URLError):
                                    opener.open(request, timeout=5)
                    self.assertEqual(requests, ["Bearer synthetic-only"] * len(openers) if weak == "none" else [])

    def test_strict_profile_still_rejects_a_wrong_hostname(self):
        with self.endpoint("none", ssl.TLSVersion.TLSv1_3) as (url, requests):
            with patch.dict(os.environ, {"OTTPLAY_TLS_SECURITY_LEVEL": "3", "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}):
                openers = self.openers(url)
                for name, opener in openers.items():
                    with self.subTest(client=name), self.assertRaises(URLError):
                        opener.open(Request(url.replace("127.0.0.1", "localhost"),
                                            headers={"Authorization": "Bearer synthetic-only"}), timeout=5)
            self.assertEqual(requests, [])

    def test_policy_is_opt_in_validated_and_never_reduces_platform_level(self):
        for module in self.modules():
            with self.subTest(module=module.__name__):
                with patch.dict(os.environ, {"OTTPLAY_TLS_SECURITY_LEVEL": "1"}):
                    with self.assertRaises(ValueError): module.https_handler()
                with patch.dict(os.environ, {"OTTPLAY_TLS_SECURITY_LEVEL": "3"}):
                    context = module.https_handler()._context
                    self.assertGreaterEqual(context.security_level, 3)
                    self.assertTrue(context.check_hostname)
                    self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
                    self.assertGreaterEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)
                with patch.dict(os.environ, {"OTTPLAY_TLS_SECURITY_LEVEL": "2"}):
                    strict = ssl.create_default_context()
                    strict.set_ciphers("ECDHE-RSA-AES128-GCM-SHA256:@SECLEVEL=3")
                    original = strict.get_ciphers()
                    with patch.object(module.ssl, "create_default_context", return_value=strict), \
                            patch.object(strict, "set_ciphers", wraps=strict.set_ciphers) as change:
                        self.assertEqual(module.https_handler()._context.security_level, 3)
                        self.assertEqual(strict.get_ciphers(), original)
                        change.assert_not_called()
                with patch.dict(os.environ, {"OTTPLAY_TLS_SECURITY_LEVEL": "3"}):
                    selected = ssl.create_default_context()
                    selected.set_ciphers("ECDHE-RSA-AES128-GCM-SHA256:@SECLEVEL=2")
                    selected.minimum_version = ssl.TLSVersion.TLSv1_3
                    original = selected.get_ciphers()
                    with patch.object(module.ssl, "create_default_context", return_value=selected):
                        result = module.https_handler()._context
                        self.assertEqual(result.security_level, 3)
                        self.assertEqual(result.minimum_version, ssl.TLSVersion.TLSv1_3)
                        self.assertEqual(result.get_ciphers(), original)

    @staticmethod
    def modules():
        import sys
        with patch.object(sys, "path", [str(ROOT / "scripts"), *sys.path]):
            return [load("strict_retain", "scripts/retain_runtime.py"), load("strict_probe", "scripts/check-epg.py")]

    def openers(self, url):
        retain, probe = self.modules()
        return {"owner": retain.OwnerReader("synthetic", "synthetic", "test-only").opener,
                "epg": probe.urlopen.__self__}
