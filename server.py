"""Browser UI for Hamlet's Tapestry: Virtual Explore and Scan Tapestry.

    .venv\\Scripts\\python.exe server.py            # then open http://localhost:8000

The desktop UI is served on http://localhost only (a secure context, so the browser may use the webcam). A
second HTTPS listener on the local network serves just the phone camera page and its upload endpoint, behind a
per-run pairing key carried in the QR code. Its certificate is self-signed and generated on first run.
"""

import argparse
import datetime
import ipaddress
import json
import mimetypes
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import cv2

from config import CONFIG, ROOT

WEB = ROOT / "web"
TLS_DIR = ROOT / ".cache" / "tls"
MAX_UPLOAD = 16 * 1024 * 1024
PHONE_FILES = {"/phone": "phone.html", "/static/phone.js": "js/phone.js", "/static/phone.css": "phone.css",
               "/static/favicon.svg": "favicon.svg"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}


def lan_addresses():
    """IPv4 addresses a phone on the same Wi-Fi can reach, the default route first."""
    found = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    out = []
    for ip in found:
        if ip not in out and not ip.startswith(("127.", "169.254.", "0.")):
            out.append(ip)
    return out


def openssl_path():
    exe = shutil.which("openssl")
    if exe:
        return exe
    for p in (r"C:\Program Files\Git\usr\bin\openssl.exe", r"C:\Program Files\Git\mingw64\bin\openssl.exe"):
        if Path(p).exists():
            return p
    return None


def ensure_certificate(hosts, folder=TLS_DIR):
    """Self-signed certificate for the phone page, regenerated when this computer's addresses change."""
    folder.mkdir(parents=True, exist_ok=True)
    cert, key, meta = folder / "phone-cert.pem", folder / "phone-key.pem", folder / "phone-cert.json"
    want = sorted(set(hosts) | {"127.0.0.1"})
    if cert.exists() and key.exists() and meta.exists():
        try:
            if set(want) <= set(json.loads(meta.read_text())["hosts"]):
                return cert, key
        except (ValueError, KeyError, OSError):
            pass
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
    except ImportError:
        exe = openssl_path()
        if exe is None:
            raise RuntimeError("Install the 'cryptography' package (pip install -r requirements.txt) to enable the phone link")
        san = ",".join(["DNS:localhost"] + [f"IP:{h}" for h in want])
        subprocess.run([exe, "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
                        "-keyout", str(key), "-out", str(cert), "-days", "397", "-subj", "/CN=Hamlet Tapestry phone link",
                        "-addext", f"subjectAltName={san}"], check=True, capture_output=True)
    else:
        pk = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Hamlet Tapestry phone link")])
        now = datetime.datetime.now(datetime.timezone.utc)
        alt = [x509.DNSName("localhost")] + [x509.IPAddress(ipaddress.ip_address(h)) for h in want]
        c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(pk.public_key())
             .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
             .not_valid_after(now + datetime.timedelta(days=397))
             .add_extension(x509.SubjectAlternativeName(alt), critical=False)
             .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
             .sign(pk, hashes.SHA256()))
        key.write_bytes(pk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        cert.write_bytes(c.public_bytes(serialization.Encoding.PEM))
    meta.write_text(json.dumps({"hosts": want}))
    return cert, key


def qr_png(text, scale=8):
    encoder = cv2.QRCodeEncoder.create()
    img = encoder.encode(text)
    img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    img = cv2.copyMakeBorder(img, 4 * scale, 4 * scale, 4 * scale, 4 * scale, cv2.BORDER_CONSTANT, value=255)
    return cv2.imencode(".png", img)[1].tobytes()


class App:
    """State shared by both listeners."""

    def __init__(self, engine, port, phone_port, verbose=False):
        self.engine = engine
        self.port = port
        self.phone_port = phone_port
        self.verbose = verbose
        self.token = secrets.token_urlsafe(9)
        self.phone_urls = []
        self.phone_error = None

    def phone_info(self):
        return {"enabled": bool(self.phone_urls), "urls": self.phone_urls, "error": self.phone_error,
                "local": f"http://localhost:{self.port}/phone?k={self.token}"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "HamletTapestry/1.0"

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        if self.app.verbose:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---- responses ----------------------------------------------------------------------------------------
    def send(self, status, body=b"", ctype="application/json", headers=None, cache="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json(self, obj, status=HTTPStatus.OK):
        self.send(status, json.dumps(obj).encode("utf-8"))

    def error(self, status, message):
        self.json({"error": message}, status)

    def image(self, data, meta, ctype="image/jpeg"):
        self.send(HTTPStatus.OK, data, ctype, {"X-Meta": json.dumps(meta)})

    def file(self, path):
        path = Path(path)
        if not path.is_file():
            return self.error(HTTPStatus.NOT_FOUND, "Not found")
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix in (".js", ".mjs"):
            ctype = "text/javascript"
        cache = "public, max-age=86400" if "vendor" in path.parts else "no-cache"
        self.send(HTTPStatus.OK, path.read_bytes(), ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""), cache=cache)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD:
            raise ValueError("Upload is too large")
        return self.rfile.read(n) if n else b""

    # ---- routing -----------------------------------------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def route(self, method):
        url = urlsplit(self.path)
        q = {k: v[-1] for k, v in parse_qs(url.query).items()}
        try:
            if self.server.public:
                return self.phone_route(method, url.path, q)
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].lower()
            if host not in LOCAL_HOSTS:
                return self.error(HTTPStatus.FORBIDDEN, "Open the UI at http://localhost")
            if method == "POST" and url.path.startswith("/api/") and not url.path.startswith("/api/phone/") \
                    and self.headers.get("X-Tapestry") != "1":
                return self.error(HTTPStatus.FORBIDDEN, "Missing X-Tapestry header")
            return self.desktop_route(method, url.path, q)
        except ValueError as e:
            self.error(HTTPStatus.BAD_REQUEST, str(e))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def phone_route(self, method, path, q):
        if path == "/" and method == "GET":
            return self.send(HTTPStatus.FOUND, b"", headers={"Location": "/phone"})
        if path in PHONE_FILES and method in ("GET", "HEAD"):
            return self.file(WEB / PHONE_FILES[path])
        if path.startswith("/api/phone/"):
            return self.phone_api(method, path, q)
        self.error(HTTPStatus.NOT_FOUND, "Not found")

    def phone_api(self, method, path, q):
        if not secrets.compare_digest(q.get("k", ""), self.app.token):
            return self.error(HTTPStatus.FORBIDDEN, "This link has expired. Scan the QR code on the computer again.")
        phone = self.app.engine.phone
        device = self.headers.get("X-Device", "")
        if path == "/api/phone/frame" and method == "POST":
            data = self.body()
            if not data:
                raise ValueError("Empty frame")
            return self.json({"active": phone.push(data, device)})
        if path == "/api/phone/status":
            return self.json({"active": phone.touch(device)})
        self.error(HTTPStatus.NOT_FOUND, "Not found")

    def desktop_route(self, method, path, q):
        e = self.app.engine
        if method == "GET" or method == "HEAD":
            if path in ("/", "/index.html"):
                return self.file(WEB / "index.html")
            if path in PHONE_FILES:
                return self.file(WEB / PHONE_FILES[path])
            if path.startswith("/static/"):
                target = (WEB / path[len("/static/"):]).resolve()
                if WEB.resolve() not in target.parents:
                    return self.error(HTTPStatus.NOT_FOUND, "Not found")
                return self.file(target)
            if path == "/api/info":
                return self.json({**e.info(), "phone": self.app.phone_info()})
            if path == "/api/camera-path":
                return self.json(e.camera_path())
            if path == "/api/audio.wav":
                return self.send(HTTPStatus.OK, e.wav, "audio/wav", cache="no-cache")
            if path == "/api/desk.jpg":
                return self.send(HTTPStatus.OK, e.desk_jpeg, "image/jpeg", cache="no-cache")
            if path == "/api/virtual":
                data, meta = e.virtual(float(q.get("t", 0)), q.get("detail", "standard"))
                return self.image(data, meta)
            if path == "/api/source":
                return self.json(e.source_status())
            if path == "/api/phone-qr.png":
                urls = self.app.phone_urls
                i = int(q.get("i", 0))
                if not urls or not 0 <= i < len(urls):
                    return self.error(HTTPStatus.NOT_FOUND, "The phone link is not available")
                return self.send(HTTPStatus.OK, qr_png(urls[i]), "image/png")
            if path.startswith("/api/phone/"):
                return self.phone_api(method, path, q)
        if method == "POST":
            if path == "/api/scan":
                return self.scan(q)
            if path == "/api/source":
                body = json.loads(self.body() or b"{}")
                return self.json(e.set_source(body.get("kind", "none"), body.get("source")))
            if path.startswith("/api/phone/"):
                return self.phone_api(method, path, q)
        self.error(HTTPStatus.NOT_FOUND, "Not found")

    def scan(self, q):
        e = self.app.engine
        source = q.get("source", "browser")
        data = self.body()
        if source == "browser":
            from web_engine import decode
            frame, fresh = decode(data), True
        else:
            frame, fresh = e.source_frame()
        status = e.source_status()
        if frame is None:
            return self.json({"frame": False, **status})
        img, meta = e.scan(frame, float(q.get("t", 0)), q.get("follow", "1") == "1", q.get("overlay") == "1")
        meta.update(fresh=fresh, source=status)
        self.image(img, meta)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = sys.platform != "win32"

    def __init__(self, addr, app, public=False, tls=None):
        self.app, self.public, self.tls = app, public, tls
        super().__init__(addr, Handler)

    def finish_request(self, request, client_address):
        if self.tls is None:
            return super().finish_request(request, client_address)
        try:
            request.settimeout(10)
            conn = self.tls.wrap_socket(request, server_side=True)
        except (ssl.SSLError, OSError):
            return
        try:
            conn.settimeout(30)
            self.RequestHandlerClass(conn, client_address, self)
        finally:
            try:
                conn.close()
            except OSError:
                pass


def start_phone_listener(app, host, port):
    hosts = lan_addresses()
    if not hosts:
        app.phone_error = "This computer has no network address a phone can reach. Join the same Wi-Fi as the phone."
        return None
    try:
        cert, key = ensure_certificate(hosts)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        server = Server((host, port), app, public=True, tls=ctx)
    except (OSError, RuntimeError, subprocess.CalledProcessError, ssl.SSLError) as e:
        app.phone_error = f"Phone link unavailable: {e}"
        return None
    app.phone_urls = [f"https://{h}:{port}/phone?k={app.token}" for h in hosts]
    threading.Thread(target=server.serve_forever, name="tapestry-phone", daemon=True).start()
    return server


def main():
    ap = argparse.ArgumentParser(description="Browser UI for Hamlet's Tapestry")
    ap.add_argument("--port", type=int, default=8000, help="desktop UI port on localhost (default 8000)")
    ap.add_argument("--phone-port", type=int, default=8443, help="HTTPS port for the phone camera page (default 8443)")
    ap.add_argument("--no-phone", action="store_true", help="do not open the phone camera listener on the network")
    ap.add_argument("--beats", default=None)
    ap.add_argument("--config", default=str(CONFIG))
    ap.add_argument("--quality", default="live", choices=["live", "render"], help="renderer preset (render is sharper but slower)")
    ap.add_argument("--open", action="store_true", help="open the UI in the default browser")
    ap.add_argument("--verbose", action="store_true", help="log every request")
    args = ap.parse_args()

    from web_engine import WebEngine
    print("Loading the show...", flush=True)
    engine = WebEngine(args.beats, args.config, args.quality)
    app = App(engine, args.port, args.phone_port, args.verbose)
    try:
        desktop = Server(("127.0.0.1", args.port), app)
    except OSError as e:
        raise SystemExit(f"Port {args.port} is busy ({e}). Close the other server or pass --port 8001.")
    phone = None if args.no_phone else start_phone_listener(app, "0.0.0.0", args.phone_port)
    if args.no_phone:
        app.phone_error = "The phone link is off (server started with --no-phone)."
    url = f"http://localhost:{args.port}"
    print(f"\n  Hamlet's Tapestry is ready:  {url}\n")
    if phone is not None:
        print("  Phone camera page (same Wi-Fi; accept the certificate warning once):")
        for u in app.phone_urls:
            print(f"    {u}")
        print()
    elif app.phone_error:
        print(f"  {app.phone_error}\n")
    print("  Press Ctrl+C to stop.\n", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        desktop.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        desktop.server_close()
        if phone is not None:
            phone.shutdown()
            phone.server_close()
        engine.close()


if __name__ == "__main__":
    main()
