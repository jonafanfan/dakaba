"""The light/dark switch, and the token structure that makes it possible.

The page followed `prefers-color-scheme` and offered no way out of it. Adding a choice means three
states, not two — auto (nothing stamped), an explicit light, and an explicit dark — and each has a
well-known way of going wrong: a media query that beats an explicit choice, a token that exists
only inside one theme's block, a `color-scheme` left pointing at the OS. These check the structure
rather than the appearance, because appearance needs a browser and structure is where the bugs are.
"""
import re
import shutil

import pytest

from test_client_loop import PAGE, run

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def css():
    return re.search(r"<style>(.*?)</style>", PAGE.read_text(encoding="utf-8"), re.S).group(1)


def block(selector):
    """The declarations of one rule, by exact selector text."""
    m = re.search(re.escape(selector) + r"\s*\{(.*?)\n    \}", css(), re.S)
    assert m, f"no rule for {selector}"
    return m.group(1)


def tokens(text):
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", text))


# ── token structure ──────────────────────────────────────────────────────────

def test_every_token_is_declared_in_the_bare_root():
    """The classic unreadable-page bug: a colour whose only definition sits inside a media query
    or a [data-theme] block is undefined in the un-stamped state, so the page renders one theme's
    text on the other theme's ground."""
    base = set(tokens(block(":root")))
    used = set(re.findall(r"var\((--[\w-]+)", css()))
    assert used, "no custom properties in use — has the stylesheet changed shape?"
    assert used <= base, f"used but never declared in :root: {sorted(used - base)}"


def test_the_dark_blocks_only_redefine_what_light_already_defines():
    """A token introduced in a dark block alone has no light counterpart, so switching to light
    leaves it unset rather than changing it back."""
    base = set(tokens(block(":root")))
    for selector in ('@media (prefers-color-scheme: dark) {\n      :root:not([data-theme="light"])',
                     ':root[data-theme="dark"]'):
        dark = set(tokens(block(selector)))
        assert dark, f"{selector} defines nothing"
        assert dark <= base, f"{selector} introduces {sorted(dark - base)}"


def test_the_two_dark_blocks_agree():
    """One is for a dark OS, the other for an explicit choice. If they drift, the app looks
    different depending on how you arrived at dark — which nobody would ever think to test by
    hand."""
    auto = tokens(block('@media (prefers-color-scheme: dark) {\n      :root:not([data-theme="light"])'))
    chosen = tokens(block(':root[data-theme="dark"]'))
    assert auto == chosen, (
        "the two dark palettes differ: "
        f"{ {k: (auto.get(k), chosen.get(k)) for k in set(auto) | set(chosen) if auto.get(k) != chosen.get(k)} }"
    )


def test_a_dark_phone_cannot_override_an_explicit_light_choice():
    """Without the :not() guard the media query wins on a dark phone, so choosing light does
    nothing at all — and only on the devices where it matters."""
    media = re.search(r"@media \(prefers-color-scheme: dark\) \{\s*([^\s{]+)", css())
    assert media, "no dark media query found"
    assert 'not([data-theme="light"])' in media.group(1), (
        f"dark media query is unguarded: {media.group(1)}"
    )


def test_color_scheme_follows_the_choice():
    """Left at the OS value, the browser keeps painting scrollbars, form controls and the
    overscroll gutter in the theme the page is no longer in."""
    text = css()
    for value in ("dark", "light"):
        assert re.search(r':root\[data-theme="%s"\][^{]*\{[^}]*color-scheme:\s*%s' % (value, value), text), (
            f"no color-scheme for an explicit {value}"
        )


# ── behaviour ────────────────────────────────────────────────────────────────

THEME_STUB = """
  const root = { dataset: {}, };
  document.documentElement = root;
  const metas = [{ attrs: { media: 'x', content: '#fff' }, removeAttribute(k){ delete this.attrs[k]; },
                   setAttribute(k, v){ this.attrs[k] = v; }, remove(){ this.removed = true; } }];
  const origQSA = document.querySelectorAll;
  document.querySelectorAll = (sel) => sel.includes('theme-color') ? metas : origQSA(sel);
"""


def test_choosing_a_theme_stamps_the_document():
    out = run(THEME_STUB + """
      applyTheme('dark');
      const afterDark = root.dataset.theme;
      applyTheme('light');
      console.log(JSON.stringify({ afterDark, afterLight: root.dataset.theme }));
    """)
    assert out["afterDark"] == "dark"
    assert out["afterLight"] == "light"


def test_the_choice_survives_a_reload():
    out = run(THEME_STUB + """
      const store = {};
      globalThis.localStorage = { getItem: k => store[k] || null, setItem: (k, v) => { store[k] = v; } };
      applyTheme('dark');
      console.log(JSON.stringify({ saved: store['daka.theme'] }));
    """)
    assert out["saved"] == "dark"


def test_no_choice_leaves_the_phone_in_charge():
    """Auto stays the default. Stamping a theme on first load would override the OS setting for
    everyone who never asked for anything."""
    out = run(THEME_STUB + """
      applyTheme();
      console.log(JSON.stringify({ stamped: root.dataset.theme ?? null }));
    """)
    assert out["stamped"] is None


def test_an_unknown_theme_is_ignored():
    out = run(THEME_STUB + """
      applyTheme('solarized');
      console.log(JSON.stringify({ stamped: root.dataset.theme ?? null }));
    """)
    assert out["stamped"] is None


def test_a_browser_that_refuses_storage_still_switches():
    out = run(THEME_STUB + """
      globalThis.localStorage = { getItem() { throw new Error('denied'); },
                                  setItem() { throw new Error('denied'); } };
      applyTheme('dark');
      console.log(JSON.stringify({ stamped: root.dataset.theme }));
    """)
    assert out["stamped"] == "dark"


def test_the_browser_chrome_follows_the_choice():
    """The theme-color metas are media-scoped, so they answer to the OS. Once a choice exists they
    have to stop doing that, or the status bar stays the colour of the theme you just left."""
    out = run(THEME_STUB + """
      applyTheme('dark');
      console.log(JSON.stringify({ media: metas[0].attrs.media ?? null, content: metas[0].attrs.content }));
    """)
    assert out["media"] is None, "the meta is still scoped to the OS setting"
    assert out["content"] == "#14110D"
