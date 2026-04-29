import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = PROJECT_ROOT / "reports"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_POST(self):
        self.send_error(405, "Method not allowed")

    def do_PUT(self):
        self.send_error(405, "Method not allowed")

    def do_DELETE(self):
        self.send_error(405, "Method not allowed")


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser(description="Serve Appium MVP reports over HTTP.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8876)
    args = parser.parse_args()

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    handler = partial(QuietHandler, directory=str(REPORT_ROOT))
    server = ReusableThreadingHTTPServer((args.host, args.port), handler)
    print(f"Serving {REPORT_ROOT} at http://{args.host}:{args.port}/")
    server.serve_forever()


if __name__ == "__main__":
    main()
