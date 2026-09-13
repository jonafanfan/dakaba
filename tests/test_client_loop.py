"""Runs the real render loop against a stubbed DOM.

Two bugs shipped to a phone in consecutive PRs, both the same shape: an identifier that was not in
scope, inside the overlay code that used to track a subject against a standing marker. Both were
valid JavaScript, so `node --check` passed and CI was green. Both only failed when the function
actually ran — which is why this file exists: it executes the *loop*, not just individual drawing
functions.

The marker and the MediaPipe subject-tracking behind it are gone now (see CONTRACT.md / README's
Known issues): the app's only positioning guidance is a plain-English sentence from the model,
shown once as static text — see renderTips. What is still genuinely live is the camera
tilt/straighten guidance, which reads the phone's own sensors; these tests now cover that loop.

The stub is deliberately dumb: it records calls and returns plausible values. It is not a browser
and cannot tell you the app looks right. What it can tell you is that a frame completes without
throwing, and that the cue and the class toggles it drives are actually reached.
"""
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[1] / "web" / "index.html"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")

# Everything the page touches at load time and during a frame. Anything missing here shows up as a
# thrown error rather than a silent pass, which is the point.
DOM_STUB = r"""
const cues = [];      // every #coachText assignment, in order
const hints = [];     // every #placeHint assignment, in order
const toggles = [];   // every classList.toggle(cls, val) call, tagged with the element id
function el(id) {
  return {
    id,
    style: new Proxy({}, { set: () => true, get: () => '' }),
    classList: {
      add(){}, remove(){},
      toggle(cls, val){ toggles.push({ id, cls, val }); },
      contains(){ return false; },
    },
    addEventListener(){}, removeEventListener(){}, click(){},
    querySelectorAll(){ return []; }, appendChild(){}, remove(){},
    set textContent(v) {
      if (id === 'coachText') cues.push(v);
      if (id === 'placeHint') hints.push(v);
    },
    get textContent() { return ''; },
    innerHTML: '', value: '', files: [],
    clientWidth: 390, clientHeight: 844, width: 390, height: 844,
    // Real enough for the controls bar, which measures itself to animate its own height.
    getBoundingClientRect() { return { width: 390, height: 140, top: 0, left: 0, right: 390, bottom: 140 }; },
    offsetHeight: 140,
    videoWidth: 1080, videoHeight: 1920, srcObject: null,
    getContext() {
      return {
        save(){}, restore(){}, beginPath(){}, ellipse(){},
        fill(){}, stroke(){}, moveTo(){}, lineTo(){}, setLineDash(){},
        clearRect(){}, drawImage(){}, putImageData(){}, fillText(){},
        measureText(s){ return { width: s.length * 7 }; },
        getImageData(w, h){ return { data: new Uint8ClampedArray(64 * 64 * 4) }; },
      };
    },
    toBlob(cb){ cb({}); }, toDataURL(){ return 'data:image/jpeg;base64,x'; },
    play(){ return Promise.resolve(); },
  };
}
const document = {
  getElementById: el,
  createElement: el,
  querySelectorAll(){ return []; },
  addEventListener(){},
};
let rafCount = 0;
const window = {
  addEventListener(){},
  DeviceMotionEvent: undefined,
  DeviceOrientationEvent: undefined,
  innerWidth: 390, innerHeight: 844,
};
const navigator = { mediaDevices: { getUserMedia(){ return Promise.reject(new Error('no cam')); },
                                    enumerateDevices(){ return Promise.resolve([]); } } };
const requestAnimationFrame = () => { rafCount++; return 1; };
const cancelAnimationFrame = () => {};
const performance = { now: () => 1000 };
const fetch = () => Promise.reject(new Error('offline'));
"""


def page_script():
    """The page's inline script, minus the top-level wiring that needs a live DOM."""
    html = PAGE.read_text(encoding="utf-8")
    return re.search(r"<script>(.*?)</script>", html, re.S).group(1)


def run(extra):
    """Write the harness to a file and run it. Passing it via `node -e` blows the Windows
    command-line length limit, which fails as a confusing FileNotFoundError."""
    script = DOM_STUB + page_script() + extra
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "harness.mjs"
        path.write_text(script, encoding="utf-8")
        result = subprocess.run(
            # encoding is not optional: text=True alone decodes with the platform locale, which on
            # Windows is cp1252 and mangles every non-ASCII character the cues contain.
            ["node", str(path)], capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
    assert result.returncode == 0, (
        f"the page threw while running a frame\n--- stderr ---\n{result.stderr.strip()[:2000]}"
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


SCAN = """
  coachingActive = true;
  liveActive = true;
"""


def test_a_frame_completes_without_throwing():
    """The regression both bugs would have failed. Neither was a syntax error."""
    out = run(SCAN + """
      liveLoop();
      console.log(JSON.stringify({ rafCount }));
    """)
    assert out["rafCount"] == 1, "the loop must re-arm even on a frame that draws nothing"


def test_it_survives_a_frame_before_any_scan():
    """Before any scan, coachingActive is false. The loop still has to run and re-arm."""
    out = run("""
      liveActive = true;
      liveLoop();
      console.log(JSON.stringify({ rafCount, cues }));
    """)
    assert out["rafCount"] == 1
    assert out["cues"] == [], "no cue should be set before a scan"


# ── camera guidance: tilt/straighten, the only live coaching left ────────────

def cue_for(setup):
    out = run(SCAN + setup + """
      liveLoop();
      console.log(JSON.stringify({ cues }));
    """)
    return out["cues"][-1] if out["cues"] else None


def test_no_cue_when_nothing_needs_fixing():
    assert cue_for("") is None


def test_a_crooked_horizon_asks_to_straighten():
    assert cue_for("needsStraightening = true; liveLean = 20;") == "Straighten the camera"


def test_straightening_up_clears_the_cue():
    """Gravity is what decides the cue is satisfied — liveLean back under the 3-degree band."""
    assert cue_for("needsStraightening = true; liveLean = 1;") is None


def test_dead_space_is_cued_when_the_horizon_is_fine():
    assert cue_for("tiltHint = 'Empty space above — aim a little lower';") == \
        "Empty space above — aim a little lower"


def test_straightening_takes_priority_over_the_tilt_hint():
    cue = cue_for("needsStraightening = true; liveLean = 20; tiltHint = 'Empty space above';")
    assert cue == "Straighten the camera", f"tilt hint jumped the queue: {cue!r}"


def test_the_needs_fix_class_follows_the_straighten_cue():
    out = run(SCAN + """
      needsStraightening = true; liveLean = 20;
      liveLoop();
      needsStraightening = false;
      liveLoop();
      const fixToggles = toggles.filter(t => t.id === 'camLevel' && t.cls === 'needs-fix');
      console.log(JSON.stringify({ fixToggles }));
    """)
    vals = [t["val"] for t in out["fixToggles"]]
    assert vals == [True, False], f"needs-fix should track the straighten cue, got {vals}"


# ── retake: another shot of the same setup ───────────────────────────────────

ANALYSIS = """
  analysisResult = {
    scene_type: 'Cafe',
    camera_tilt: { direction: 'down', reason: 'Empty space above — aim a little lower' },
    composition: { horizon: 'Tilted' },
    placement_hint: 'Stand next to the drawer',
    lighting: { quality: 'Good' },
    hashtags: [],
    filter: 'Vivid',
  };
"""


def test_retake_restores_the_coaching_state():
    """The whole point: the scene has not changed, so the same guidance should come straight back."""
    out = run(ANALYSIS + """
      motionGranted = true;
      resetCoaching();                 // as stopLive() does on the way to the results screen
      retake();
      console.log(JSON.stringify({ coachingActive, tiltHint, needsStraightening, hints }));
    """)
    assert out["coachingActive"] is True
    # Phrased from `direction` now, not copied from `reason` — same sentence in English, and the
    # only thing that makes it translatable without another call to the model.
    assert out["tiltHint"] == "Empty space above — aim a little lower"
    assert out["needsStraightening"] is True
    assert out["hints"][-1] == "Stand next to the drawer"


def test_retake_costs_no_api_call():
    """The reason it exists. Re-scanning an unchanged scene costs a wait and two OpenAI calls to
    produce the same placement_hint over again."""
    out = run(ANALYSIS + """
      let fetches = 0;
      globalThis.fetch = () => { fetches++; return Promise.reject(new Error('x')); };
      resetCoaching();
      retake();
      console.log(JSON.stringify({ fetches }));
    """)
    assert out["fetches"] == 0, "retake must not re-analyse the scene"


def test_retake_does_nothing_without_an_analysis():
    """Defensive: the button is only reachable from the results screen, but a stale tap or a
    restored session should not put the app into a coaching state with nothing to show."""
    out = run("""
      analysisResult = null;
      retake();
      console.log(JSON.stringify({ coachingActive }));
    """)
    assert out["coachingActive"] is False


def test_a_scan_and_a_retake_produce_the_same_coaching_state():
    """Both go through beginCoaching, so they cannot drift apart. This is why it was extracted."""
    out = run(ANALYSIS + """
      motionGranted = true;
      beginCoaching(analysisResult);
      const afterScan = { tiltHint, needsStraightening, hint: hints[hints.length - 1] };
      resetCoaching();
      retake();
      const afterRetake = { tiltHint, needsStraightening, hint: hints[hints.length - 1] };
      console.log(JSON.stringify({ afterScan, afterRetake }));
    """)
    assert out["afterScan"] == out["afterRetake"]


def test_resetting_clears_the_stale_hint():
    """A failed rescan must not leave the PREVIOUS scene's instruction sitting above the shutter,
    describing a scene the user has walked away from."""
    out = run(ANALYSIS + """
      beginCoaching(analysisResult);
      resetCoaching();
      console.log(JSON.stringify({ hint: hints[hints.length - 1] }));
    """)
    assert out["hint"] == ""
