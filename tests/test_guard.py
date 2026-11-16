"""The addresses a page a request named may reach (PLAN §14, D3), through real local Chromium and a local
server: names resolve by a fake, and every address the proxy is let connect to is this Mac's test server, so
nothing leaves the machine."""
import asyncio

from aiohttp import web
import pytest

from gamelab.environments.web import browser
from gamelab.eval import guard
from gamelab.eval import web as web_eval

OUTSIDE, INSIDE = "93.184.216.34", "10.0.0.5"


class Names:
    """Name resolution as a fake: each name's answers in turn (the last again), IP addresses as themselves."""

    def __init__(self, **names):
        self.names = {name.replace("_", "."): list(answers) for name, answers in names.items()}
        self.asked = []

    async def __call__(self, host, port):
        self.asked.append(host)
        if host in self.names:
            answers = self.names[host]
            return answers.pop(0) if len(answers) > 1 else answers[0]
        return await guard.resolve(host, port)


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://192.168.0.1/", "http://localhost:8080/", "http://[::1]/",
                                 "file:///etc/passwd", "data:text/html,<p>", "http://100.64.1.2/", "http://169.254.169.254/",
                                 "http://[fe80::1]/", "http://[fd00::1]/", "http://[::ffff:10.0.0.1]/", "http://0.0.0.0/",
                                 "ftp://game.test/", "http://nowhere.test/", "http://mixed.test/"])
async def test_an_address_off_the_public_internet_is_not_opened(url):
    names = Names(game_test=[[OUTSIDE]], mixed_test=[[OUTSIDE, INSIDE]], nowhere_test=[[]])
    assert not await guard.allowed(url, names)


async def test_a_public_address_is_opened():
    names = Names(game_test=[[OUTSIDE]])
    assert await guard.allowed("https://game.test/play?level=1", names)
    assert await guard.allowed("http://93.184.216.34:8080/", names)
    assert guard.public("2606:4700::1111") and not guard.public("not an address")


@pytest.fixture
async def server():
    """A local server the test's names all lead to: a page, a resource, a redirect inside and a WebSocket."""
    async def page(request):
        return web.Response(text="<p>game</p>", content_type="text/html")

    async def data(request):
        return web.Response(text="data", headers={"Access-Control-Allow-Origin": "*"})

    async def inward(request):
        raise web.HTTPFound(f"http://inside.test:{request.url.port}/data")

    async def socket(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_str("hello")
        await ws.close()
        return ws

    app = web.Application()
    for path, handler in (("/", page), ("/data", data), ("/inward", inward), ("/ws", socket)):
        app.router.add_get(path, handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield site._server.sockets[0].getsockname()[1]
    await runner.cleanup()


def to_server(port):
    async def connect(address, _):
        # the only address checked as public in these tests: the local server stands in for it
        assert address == OUTSIDE
        return await asyncio.open_connection("127.0.0.1", port)
    return connect


FETCH = "(url) => fetch(url).then(r => r.text(), () => 'blocked')"
SOCKET = "(url) => new Promise(done => { const ws = new WebSocket(url); ws.onmessage = e => done(e.data); ws.onerror = () => done('blocked'); })"


async def test_what_the_page_loads_afterwards_is_held_to_public_addresses_redirects_and_sockets_too(server):
    names = Names(game_test=[[OUTSIDE]], inside_test=[[INSIDE]])
    site = {"url": f"http://game.test:{server}/", "asked": True}
    async with web_eval.guarded(site, names, to_server(server)) as held:
        async with browser(viewport=[800, 600], **held) as page:
            await page.goto(site["url"])
            assert await page.text_content("p") == "game"
            assert await page.evaluate(FETCH, f"http://game.test:{server}/data") == "data"
            for url in (f"http://inside.test:{server}/data", f"http://127.0.0.1:{server}/data", f"http://game.test:{server}/inward"):
                assert await page.evaluate(FETCH, url) == "blocked", url
            assert await page.evaluate(SOCKET, f"ws://game.test:{server}/ws") == "hello"
            assert await page.evaluate(SOCKET, f"ws://inside.test:{server}/ws") == "blocked"
            assert await page.evaluate(SOCKET, f"ws://127.0.0.1:{server}/ws") == "blocked"


async def test_a_name_that_resolves_inside_on_a_later_connection_is_refused_then(server):
    # DNS rebinding: public when the page is opened, this network's own address afterwards
    names = Names(game_test=[[OUTSIDE], [OUTSIDE], [INSIDE]])
    site = {"url": f"http://game.test:{server}/", "asked": True}
    async with web_eval.guarded(site, names, to_server(server)) as held:
        async with browser(viewport=[800, 600], **held) as page:
            await page.goto(site["url"])
            assert await page.evaluate(FETCH, f"http://game.test:{server}/data") == "blocked"
    # asked for the request's address, the page's and the later fetch's: each connection is resolved again
    assert names.asked.count("game.test") == 3


async def test_a_request_for_an_address_inside_is_refused_before_a_browser_opens():
    with pytest.raises(guard.Refused):
        async with web_eval.guarded({"url": "http://192.168.0.1/", "asked": True}):
            pass
    async with web_eval.guarded({"url": "http://192.168.0.1/"}) as held:
        assert held == {}           # a site of sites.json is opened as it is
