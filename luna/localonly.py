"""Keep Luna's web server for you alone: refuse requests that other web pages could make through your browser.

Luna listens on 127.0.0.1, so no other computer can reach it. But *your browser* can reach it, and a web page you happen to have open
in another tab can make the browser do so. Two checks close that gap (both are about the request's headers, which a web page can't forge):

* **Host.** A page on another domain can be tricked ("DNS rebinding") into reaching 127.0.0.1 under its own domain name; the request then
  names that domain in `Host`. Only `127.0.0.1` and `localhost` are answered.
* **Origin.** A page on another site can make your browser *send* a request that changes something (a POST), even if it can't read the
  answer. Browsers say where such a request comes from in `Origin`, so a request that changes something, and names an origin other than
  this very server (same host *and* port, so another program on your computer doesn't count either), is refused. Requests with no
  `Origin` (scripts, `curl`) are fine: only a browser can be used against you this way.

Reading requests (GET) change nothing in Luna, and the answers carry no permission for other sites to read them, so they need only the Host check.
"""
from __future__ import annotations

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

LOCAL_HOSTS = ("127.0.0.1", "localhost")
READING = ("GET", "HEAD", "OPTIONS")        # methods that change nothing


class LocalOnly:
    """ASGI middleware: answer only requests addressed to this computer (`hosts`) and, for those that change something, sent by our own page."""

    def __init__(self, app, hosts=LOCAL_HOSTS):
        """Wrap the app; `hosts` are the names this computer may be addressed by."""
        self.app = app
        self.hosts = hosts

    async def __call__(self, scope, receive, send):
        """Pass the request on, or refuse it with 403 and a message the interface can show."""
        if scope["type"] == "http":
            headers = Headers(scope=scope)
            host = headers.get("host", "")
            origin = headers.get("origin")
            name, _, port = host.partition(":")
            if name not in self.hosts or not (port == "" or port.isdigit()):
                return await self._refuse(scope, receive, send, "Luna only answers requests addressed to 127.0.0.1 or localhost.")
            if scope["method"] not in READING and origin is not None and origin != f"http://{host}":
                return await self._refuse(scope, receive, send, "Luna refused a change requested by another web page.")
        await self.app(scope, receive, send)

    @staticmethod
    async def _refuse(scope, receive, send, message):
        """Send the 403 reply."""
        await JSONResponse({"detail": message}, status_code=403)(scope, receive, send)
