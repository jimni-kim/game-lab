"""The browser surface through real local Chromium on a synthetic page: no external game or paid model is used."""
from io import BytesIO

from PIL import Image
import pytest

from gamelab.environments.web import WebSurface, browser


@pytest.fixture
async def page():
    async with browser(viewport=[1280, 1000]) as opened:
        yield opened


async def test_inputs_reach_the_page_in_its_pixels_and_a_look_may_be_cut_to_a_region(page):
    await page.set_content("<body style='margin:0'><script>window.seen=[];"
                           "document.addEventListener('keydown',e=>window.seen.push(['key',e.key]));"
                           "document.addEventListener('mousedown',e=>window.seen.push(['down',e.clientX,e.clientY]));"
                           "document.addEventListener('mouseup',e=>window.seen.push(['up',e.clientX,e.clientY]));"
                           "</script></body>")
    surface = WebSurface(page)
    await surface.key("ArrowLeft")
    await surface.tap(100, 200, 60)
    await surface.drag(300, 400, 500, 420, 120)
    assert await page.evaluate("window.seen") == [["key", "ArrowLeft"], ["down", 100, 200], ["up", 100, 200],
                                                  ["down", 300, 400], ["up", 500, 420]]
    assert await surface.size() == [1280, 1000]
    with Image.open(BytesIO(await surface.capture([10, 20, 110, 70]))) as image:
        assert image.format == "PNG" and image.size == (100, 50)


async def test_elements_are_read_by_selector_with_their_own_text_and_never_a_field(page):
    await page.set_content("<div class='score a b'>12<span>+4</span></div><input class='score' value='secret'>"
                           "<p class='tile'>x</p><p class='tile'>y</p>")
    found = await WebSurface(page).elements([{"name": "score", "selector": ".score", "max": 2},
                                             {"name": "tiles", "selector": ".tile", "max": 1},
                                             {"name": "none", "selector": ".absent", "max": 1}])
    assert found["score"] == {"count": 2, "truncated": False,
                              "items": [{"classes": ["score", "a", "b"], "text": "12"}, {"protected": True}]}
    assert found["tiles"] == {"count": 2, "truncated": True, "items": [{"classes": ["tile"], "text": "x"}]}
    assert found["none"] == {"count": 0, "truncated": False, "items": []}
    assert await WebSurface(page).elements([{"name": "bad", "selector": "::", "max": 1}]) is None
