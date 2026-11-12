"""Vercel serverless function: exposes the Flask `app` (every URL is rewritten here by vercel.json).

Vercel now hands the function the *rewritten* path (/api/index) instead of the browser's path, so vercel.json passes the original path in
the `__p` query parameter and this wrapper puts it back before Flask routes the request."""
import os
import sys
from urllib.parse import parse_qsl, urlencode

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from leabee.serverless import app as flask_app  # noqa: E402

PARAM = "__p"


class RestorePath:
    def __init__(self, wsgi):
        self.wsgi = wsgi

    def __call__(self, environ, start_response):
        pairs = parse_qsl(environ.get("QUERY_STRING", ""), keep_blank_values=True)
        orig = next((v for k, v in pairs if k == PARAM), None)
        if orig is not None or environ.get("PATH_INFO", "").startswith("/api/index"):
            rest = [(k, v) for k, v in pairs if k != PARAM]
            path = "/" + (orig or "").lstrip("/")
            if path.startswith("//") or "\\" in path or "\n" in path or "\r" in path:
                path = "/"
            environ["PATH_INFO"] = path
            environ["QUERY_STRING"] = urlencode(rest)
            environ["REQUEST_URI"] = path + ("?" + environ["QUERY_STRING"] if rest else "")
        return self.wsgi(environ, start_response)


app = RestorePath(flask_app)
