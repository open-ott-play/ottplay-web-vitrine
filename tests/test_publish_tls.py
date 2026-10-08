"""Run the real wrapper and pinned publisher against synthetic loopback TLS."""
import http.server
import json
import os
from pathlib import Path
import re
import select
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import unittest

from retention_fixture import ROOT, make_stage

PIN = "8cf033ed53b82c0c67b16359c8c431f99e111d04"


class Tunnel(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_CONNECT(self):
        if self.path != "here.now:443":
            self.send_error(403)
            return
        with socket.create_connection(self.server.destination, timeout=5) as upstream:
            self.send_response(200)
            self.end_headers()
            self.wfile.flush()
            streams = [self.connection, upstream]
            while True:
                readable, _, _ = select.select(streams, [], [], 10)
                if not readable:
                    return
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    (upstream if source is self.connection else self.connection).sendall(data)


def openssl(directory, *args):
    subprocess.run(["openssl", *map(str, args)], cwd=directory, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=20)


def certificates(directory, weak):
    def key(name, bits):
        openssl(directory, "genpkey", "-algorithm", "RSA", "-pkeyopt",
                f"rsa_keygen_bits:{bits}", "-out", name + ".key")
    key("root", 1024 if weak == "root" else 2048)
    openssl(directory, "req", "-new", "-x509", "-sha256", "-days", "1", "-key", "root.key",
            "-out", "root.pem", "-subj", "/CN=Synthetic root", "-addext",
            "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    issuer = "root"
    if weak == "intermediate":
        key("intermediate", 1024)
        openssl(directory, "req", "-new", "-key", "intermediate.key", "-out", "intermediate.csr",
                "-subj", "/CN=Synthetic intermediate")
        (directory / "intermediate.ext").write_text(
            "basicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\n")
        openssl(directory, "x509", "-req", "-in", "intermediate.csr", "-CA", "root.pem",
                "-CAkey", "root.key", "-set_serial", "2", "-days", "1", "-sha256",
                "-extfile", "intermediate.ext", "-out", "intermediate.pem")
        issuer = "intermediate"
    key("leaf", 1024 if weak == "leaf" else 2048)
    openssl(directory, "req", "-new", "-key", "leaf.key", "-out", "leaf.csr",
            "-subj", "/CN=here.now")
    (directory / "leaf.ext").write_text(
        "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\nsubjectAltName=DNS:here.now,IP:127.0.0.1\n")
    openssl(directory, "x509", "-req", "-in", "leaf.csr", "-CA", issuer + ".pem",
            "-CAkey", issuer + ".key", "-set_serial", "3", "-days", "1", "-sha256",
            "-extfile", "leaf.ext", "-out", "leaf.pem")
    chain = (directory / "leaf.pem").read_bytes()
    if issuer != "root":
        chain += (directory / (issuer + ".pem")).read_bytes()
    (directory / "chain.pem").write_bytes(chain)
    return directory / "chain.pem", directory / "leaf.key"


class PublishTlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.publisher = Path(os.environ.get("HERENOW_PUBLISH_SCRIPT", str(
            ROOT / ".ci-tools/herenow/here-now/scripts/publish.sh"))).resolve()
        if not cls.publisher.is_file():
            raise AssertionError("Check out the pinned heredotnow/skill publisher")
        revision = subprocess.check_output(["git", "-C", str(cls.publisher.parents[2]),
                                            "rev-parse", "HEAD"], text=True).strip()
        if revision != PIN:
            raise AssertionError("TLS tests require the actual pinned publisher")
        cls.curl = shutil.which(os.environ.get("PUBLISH_TLS_TEST_CURL", "curl"))
        if cls.curl is None:
            raise AssertionError("curl is required for the publisher TLS tests")
        version = subprocess.check_output([cls.curl, "--disable", "--version"], text=True).splitlines()[0]
        if not re.search(r"(?:^|\s)OpenSSL/3\.\d+\.\d+(?:\s|$)", version):
            raise AssertionError("Set PUBLISH_TLS_TEST_CURL to an OpenSSL 3 curl for real TLS tests")
        cls.temporary = tempfile.TemporaryDirectory(prefix="publisher TLS fixtures ")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.chains = {}
        for name in ("strong", "leaf", "intermediate", "root"):
            directory = cls.root / name
            directory.mkdir()
            cls.chains[name] = certificates(directory, name)
        cls.trust = cls.root / "trust.pem"
        cls.trust.write_bytes(b"\n".join((cls.root / name / "root.pem").read_bytes()
                                        for name in cls.chains))

    def publish(self, weak="strong", stage="create", tls=ssl.TLSVersion.TLSv1_2,
                level="2", insecure_stage=None):
        with tempfile.TemporaryDirectory(prefix="publisher TLS run ") as temporary:
            base = Path(temporary)
            site = base / "site with spaces"
            make_stage(site)
            binary = base / "bin"
            binary.mkdir()
            (binary / "curl").symlink_to(self.curl)
            config = base / "curl-config"
            config.mkdir()
            # Old publisher behavior accepts every weak fixture with this file.
            # The production wrapper must isolate all curl invocations from it.
            curlrc = config / ".curlrc"
            curlrc.write_text('insecure\nciphers = "DEFAULT:@SECLEVEL=0"\n')
            initial = "strong" if stage != "create" else weak
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.set_ciphers("DEFAULT:@SECLEVEL=0")  # Synthetic server only.
            context.minimum_version = context.maximum_version = tls
            context.load_cert_chain(*self.chains[initial])
            requests = []
            chains = self.chains

            class Api(http.server.BaseHTTPRequestHandler):
                def log_message(self, *_):
                    pass

                def do_POST(self):
                    self.reply()

                def do_PUT(self):
                    self.reply()

                def reply(self):
                    body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    requests.append((self.command, self.path, bool(body),
                                     self.headers.get("Authorization")))
                    if self.path == "/api/v1/publish":
                        payload = {"slug": "synthetic", "siteUrl": "https://here.now/synthetic",
                                   "upload": {"versionId": "synthetic-pending",
                                              "finalizeUrl": (plaintext_origin if insecure_stage == "finalize"
                                                              else "https://here.now") + "/finalize",
                                              "uploads": [{"path": "demo/pattern0.ts",
                                                           "url": (plaintext_origin if insecure_stage == "upload"
                                                                   else "https://here.now") + "/upload",
                                                           "headers": {"Content-Type": "video/mp2t"}}]}}
                        if stage == "upload":
                            context.load_cert_chain(*chains[weak])
                    elif self.path == "/upload":
                        payload = {}
                        if stage == "finalize":
                            context.load_cert_chain(*chains[weak])
                    elif self.path == "/finalize":
                        payload = {"currentVersionId": "synthetic-final"}
                    else:
                        self.send_error(404)
                        return
                    raw = json.dumps(payload).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)

            api = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Api)
            api.socket = context.wrap_socket(api.socket, server_side=True)
            proxy = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Tunnel)
            proxy.destination = api.server_address
            servers = [api, proxy]
            plaintext_origin = None
            if insecure_stage:
                plaintext = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Api)
                plaintext_origin = "http://127.0.0.1:" + str(plaintext.server_port)
                servers.append(plaintext)
            threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in servers]
            for thread in threads:
                thread.start()
            try:
                proxy_url = "http://127.0.0.1:" + str(proxy.server_port)
                env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ["PATH"],
                           CURL_HOME=str(config), CURL_CA_BUNDLE=str(self.trust),
                           HTTPS_PROXY=proxy_url, https_proxy=proxy_url, HTTP_PROXY=proxy_url,
                           http_proxy=proxy_url, ALL_PROXY="", all_proxy="",
                           NO_PROXY="127.0.0.1", no_proxy="127.0.0.1",
                           HERENOW_API_KEY="synthetic-wrapper-only", HERENOW_SITE_SLUG="",
                           HERENOW_EXPECTED_VERSION="", HERENOW_WORKSPACE="test", OVERWRITE="0",
                           HERENOW_PUBLISH_SCRIPT=str(self.publisher), OTTPLAY_CURL_SECURITY_LEVEL=level)
                result = subprocess.run(["bash", str(ROOT / "scripts/publish-herenow.sh"), str(site)],
                                        cwd=base, env=env, capture_output=True, text=True, timeout=40)
                self.assertEqual(curlrc.read_text(), 'insecure\nciphers = "DEFAULT:@SECLEVEL=0"\n')
                self.assertNotIn("synthetic-wrapper-only", result.stdout + result.stderr)
                return result, requests
            finally:
                for server in reversed(servers):
                    server.shutdown()
                    server.server_close()
                for thread in threads:
                    thread.join()

    def test_strong_chains_complete_all_publisher_stages(self):
        for tls in (ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3):
            with self.subTest(tls=tls):
                result, requests = self.publish(tls=tls)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual([row[:2] for row in requests], [
                    ("POST", "/api/v1/publish"), ("PUT", "/upload"), ("POST", "/finalize")])
                self.assertEqual([row[3] for row in requests], [
                    "Bearer synthetic-wrapper-only", None, "Bearer synthetic-wrapper-only"])

    def test_weak_chain_rejected_before_each_publisher_stage_despite_insecure_curlrc(self):
        for tls in (ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3):
            for weak in ("leaf", "intermediate", "root"):
                for stage, expected_count in (("create", 0), ("upload", 1), ("finalize", 2)):
                    with self.subTest(tls=tls, weak=weak, stage=stage):
                        result, requests = self.publish(weak=weak, stage=stage, tls=tls)
                        self.assertNotEqual(result.returncode, 0)
                        self.assertEqual(len(requests), expected_count, result.stderr)

    def test_stricter_level_three_rejects_rsa2048_before_authentication(self):
        result, requests = self.publish(level="3")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(requests, [])

    def test_cleartext_upload_and_finalize_targets_rejected_before_data(self):
        for stage, expected_count in (("upload", 1), ("finalize", 2)):
            with self.subTest(stage=stage):
                result, requests = self.publish(insecure_stage=stage)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(requests), expected_count, result.stderr)


if __name__ == "__main__":
    unittest.main()
