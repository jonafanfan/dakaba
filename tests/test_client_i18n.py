"""The language switch, exercised by running the page's own code.

Hashtags used to come back in Chinese on some scans and English on others, so the app now states a
language instead of hoping. The half that matters here is the client: the string table, the switch
itself, and the tilt cue, which is translated from the machine key (`camera_tilt.direction`) the
engine sends rather than by asking the model again.

The marker caption used to be translated the same way, from `placement.reason`. Both the marker and
that field are gone — the standing guide is now `placement_hint`, free text the model writes
directly in the chosen language, so there is no key to translate for it.

The harness is `test_client_loop`'s — a stubbed DOM and real node. It cannot tell you the Chinese
reads well; it can tell you nothing is left in English by accident, which is the failure the user
actually saw.
"""
import re
import shutil

import pytest

from test_client_loop import PAGE, SCAN, run

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")

CJK = re.compile(r"[一-鿿]")


def strings(lang):
    return run(f"lang = '{lang}'; console.log(JSON.stringify(STRINGS['{lang}']));")


def test_both_languages_define_exactly_the_same_keys():
    """A key present in one and missing from the other is a sentence that silently falls back to
    English — which is the bug this whole change is about."""
    en, zh = strings("en"), strings("zh")
    assert set(en) == set(zh), (
        f"only in en: {sorted(set(en) - set(zh))}; only in zh: {sorted(set(zh) - set(en))}"
    )


def test_every_chinese_string_is_actually_in_chinese():
    """Guards against a key being copied across untranslated, which reads as a missing translation
    rather than as a deliberate choice."""
    for key, value in strings("zh").items():
        assert CJK.search(value), f"{key} is not translated: {value!r}"


def test_the_english_table_is_not_empty_of_the_things_it_must_cover():
    """Guard on the guards above: if the table stopped being found, every other test here passes
    while checking nothing."""
    en = strings("en")
    assert len(en) > 30, f"only found {len(en)} strings — is the table still being parsed?"
    for prefix in ("cue.", "tilt.", "res.", "toast.", "err."):
        assert any(k.startswith(prefix) for k in en), f"no {prefix} strings found"


def test_the_page_falls_back_to_english_for_an_unknown_language():
    out = run("lang = 'fr'; console.log(JSON.stringify({ cue: tr('cue.straighten') }));")
    assert out["cue"] == "Straighten the camera"


def test_an_unknown_key_returns_the_key_rather_than_blank():
    """A blank cue is invisible on a phone; a key name is at least a visible fault."""
    out = run("console.log(JSON.stringify({ v: tr('nope.missing') }));")
    assert out["v"] == "nope.missing"


# ── the switch ───────────────────────────────────────────────────────────────

def test_switching_language_changes_the_live_cues():
    """The cue is the product. This is the end-to-end check that a switch reaches it."""
    out = run(SCAN + """
      needsStraightening = true; liveLean = 20;
      applyLang('en'); liveLoop();
      const english = cues[cues.length - 1];
      needsStraightening = true;            // the loop latches it off once level; reopen it
      applyLang('zh'); liveLoop();
      console.log(JSON.stringify({ english, chinese: cues[cues.length - 1] }));
    """)
    assert out["english"] == "Straighten the camera"
    assert CJK.search(out["chinese"]), f"cue stayed English after the switch: {out['chinese']!r}"


def test_the_choice_survives_a_reload():
    out = run("""
      const store = {};
      globalThis.localStorage = { getItem: k => store[k] || null, setItem: (k, v) => { store[k] = v; } };
      applyLang('zh');
      console.log(JSON.stringify({ saved: store['daka.lang'] }));
    """)
    assert out["saved"] == "zh"


def test_a_browser_that_refuses_storage_still_switches():
    """Safari private mode throws on setItem rather than returning. The switch has to work for the
    session even when it cannot be remembered."""
    out = run("""
      globalThis.localStorage = { getItem() { throw new Error('denied'); },
                                  setItem() { throw new Error('denied'); } };
      applyLang('zh');
      console.log(JSON.stringify({ lang, cue: tr('cue.straighten') }));
    """)
    assert out["lang"] == "zh"
    assert CJK.search(out["cue"])


def test_the_wordmark_and_the_start_button_are_never_translated():
    """打卡吧！ and 开始打卡 are the brand, not copy — they read the same in both languages, and a
    data-i18n on either would quietly replace the logo."""
    page = PAGE.read_text(encoding="utf-8")
    start = re.search(r'<button class="start-btn" id="startBtn"[^>]*>', page).group(0)
    assert "data-i18n" not in start, "the start button is branding — it must not be translated"
    assert '<span class="char">打</span>' in page


def test_every_data_i18n_key_exists_in_the_table():
    """A typo'd key shows the key name to the user, on the home screen, in both languages."""
    page = PAGE.read_text(encoding="utf-8")
    keys = set(re.findall(r'data-i18n="([^"]+)"', page))
    assert keys, "no data-i18n attributes found — the markup half is not wired up"
    missing = keys - set(strings("en"))
    assert not missing, f"markup asks for strings that do not exist: {sorted(missing)}"


def test_every_tr_key_in_the_script_exists_in_the_table():
    """The test above covers the markup half; this is the script half, which is the larger one.

    A typo'd key renders as the key itself — `cue.striaghten` sitting over the viewfinder, in both
    languages, because tr() returns the key rather than blank when it misses. `test_client_loop`'s
    cue scan used to catch this as a side effect of checking the movement cues' wording; those cues
    went with the standing marker, so the keys are checked here directly instead.

    Only the English table is compared, because test_both_languages_define_exactly_the_same_keys
    already pins the two to the same key set.
    """
    page = PAGE.read_text(encoding="utf-8")
    keys = set(re.findall(r"tr\('([^']+)'\)", page))
    assert len(keys) > 10, f"only found {sorted(keys)} — has tr() been renamed or inlined?"
    missing = keys - set(strings("en"))
    assert not missing, f"the script asks for strings that do not exist: {sorted(missing)}"


# ── the engine's own sentences ───────────────────────────────────────────────

@pytest.mark.parametrize("direction", ["up", "down"])
def test_the_tilt_cue_is_translated_from_its_direction(direction):
    """The engine sends both a machine key and an English sentence. Translating from the key is what
    makes the cue Chinese without a second paid call to relabel a scene already analysed."""
    out = run(f"""
      applyLang('zh');
      analysisResult = {{
        camera_tilt: {{ direction: '{direction}', reason: 'English fallback' }},
        composition: {{}}, lighting: {{}}, hashtags: [], filter: 'Vivid',
      }};
      beginCoaching(analysisResult);
      console.log(JSON.stringify({{ tiltHint }}));
    """)
    assert CJK.search(out["tiltHint"]), f"tilt cue stayed English: {out['tiltHint']!r}"


def test_an_unknown_tilt_direction_keeps_the_engines_own_wording():
    """Forward compatibility: a direction added to the engine before the client knows about it must
    still say something true, not fall back to a generic line."""
    out = run("""
      applyLang('zh');
      analysisResult = {
        camera_tilt: { direction: 'newly_invented', reason: 'Something new' },
        composition: {}, lighting: {}, hashtags: [], filter: 'Vivid',
      };
      beginCoaching(analysisResult);
      console.log(JSON.stringify({ tiltHint }));
    """)
    assert out["tiltHint"] == "Something new"


def test_the_standing_guide_is_shown_as_the_model_wrote_it():
    """placement_hint is free text the model already wrote in the chosen language, so the client
    must render it verbatim rather than looking for a key that does not exist."""
    out = run("""
      applyLang('zh');
      analysisResult = {
        camera_tilt: { direction: 'ok', reason: '' },
        composition: {}, lighting: {}, hashtags: [], filter: 'Vivid',
        placement_hint: '站在抽屉旁边',
      };
      beginCoaching(analysisResult);
      console.log(JSON.stringify({ hint: hints[hints.length - 1] }));
    """)
    assert out["hint"] == "站在抽屉旁边"


# ── what goes to the server ──────────────────────────────────────────────────

def test_the_scan_tells_the_server_which_language_to_write_in():
    """Without this the model picks for itself, which is where the mixed hashtags came from."""
    out = run("""
      applyLang('zh');
      const sent = [];
      globalThis.FormData = class { append(k, v) { sent.push([k, String(v)]); } };
      globalThis.fetch = () => Promise.reject(new Error('offline'));
      postAnalyze({}).catch(() => {
        console.log(JSON.stringify({ sent }));
      });
    """)
    assert ["lang", "zh"] in [list(p) for p in out["sent"]]


def test_the_filter_names_stay_english_on_the_wire():
    """The strip label is translated; the value the server sends and FILTER_CSS looks up is not.
    Translating the key would silently disable every filter."""
    out = run("""
      applyLang('zh');
      console.log(JSON.stringify({
        keys: Object.keys(FILTER_CSS),
        label: filterLabel('Vivid Warm'),
      }));
    """)
    assert "Vivid Warm" in out["keys"]
    assert CJK.search(out["label"]), "the visible label should be translated"


def test_every_filter_has_a_chinese_label():
    out = run("""
      console.log(JSON.stringify({
        names: [NO_FILTER, ...Object.keys(FILTER_CSS)],
        labels: FILTER_LABELS.zh,
      }));
    """)
    missing = [n for n in out["names"] if n not in out["labels"]]
    assert not missing, f"no Chinese label for: {missing}"
