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
const styles = [];    // every style property assignment, tagged with the element id
const toggles = [];   // every classList.toggle(cls, val) call, tagged with the element id
function el(id) {
  return {
    id,
    style: new Proxy({}, {
      set: (t, k, val) => { t[k] = val; styles.push({ id, prop: k, val }); return true; },
      get: (t, k) => t[k] || '',
    }),
    classList: {
      // add/remove are recorded as toggles too, so a test can read the resulting state whichever
      // call the page happened to use to get there.
      add(...cs){ cs.forEach(cls => toggles.push({ id, cls, val: true })); },
      remove(...cs){ cs.forEach(cls => toggles.push({ id, cls, val: false })); },
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
    """Phrased at render time from the direction, not stored as a finished sentence — see
    test_client_i18n for why that distinction is load-bearing."""
    assert cue_for("tiltDirection = 'down'; tiltFallback = 'engine wording';") == \
        "Empty space above — aim a little lower"


def test_an_unphraseable_direction_falls_back_to_the_engines_wording():
    assert cue_for("tiltDirection = 'sideways'; tiltFallback = 'Engine wording';") == \
        "Engine wording"


def test_straightening_takes_priority_over_the_tilt_hint():
    cue = cue_for("needsStraightening = true; liveLean = 20; tiltDirection = 'down';")
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


# ── the horizon level, driven by real gravity vectors ───────────────────────
#
# onMotion had no tests at all: every cue test sets `liveLean` by hand, which walks straight past
# the function that computes it. That is how it shipped reading pitch as though it were roll, and
# how the sign stayed wrong on iOS.

POSE = """
function pose(rollDeg, pitchDeg) {
  // Gravity as a non-iOS device reports it: a vector pointing along world-up, in device axes.
  // Rotating the phone CLOCKWISE, as the person holding it sees it, swings that vector toward -x.
  const G = 9.81, rad = d => d * Math.PI / 180;
  return { x: -G * Math.sin(rad(rollDeg)) * Math.cos(rad(pitchDeg)),
           y:  G * Math.cos(rad(rollDeg)) * Math.cos(rad(pitchDeg)),
           z:  G * Math.sin(rad(pitchDeg)) };
}
function hold(rollDeg, pitchDeg) {
  onMotion({ accelerationIncludingGravity: pose(rollDeg, pitchDeg) });
  const rot = styles.filter(s => s.id === 'camLevelLine' && s.prop === 'transform');
  const op  = styles.filter(s => s.id === 'camLevelLine' && s.prop === 'opacity');
  const lvl = toggles.filter(t => t.id === 'camLevel' && t.cls === 'level');
  return { angle: rot.length ? parseFloat(rot[rot.length - 1].val.match(/-?[0-9.]+/)[0]) : null,
           opacity: op.length ? op[op.length - 1].val : null,
           level: lvl.length ? lvl[lvl.length - 1].val : null,
           liveLean: Math.round(liveLean) };
}
"""


def test_held_upright_the_line_is_flat_and_green():
    out = run(POSE + """
      console.log(JSON.stringify(hold(0, 0)));
    """)
    assert out["angle"] == 0
    assert out["level"] is True


def test_the_line_lies_along_the_horizon_not_mirrored_against_it():
    """Rotate the phone clockwise and the horizon in the frame swings anticlockwise, so the line
    must too. Get this backwards and the indicator mirrors the real horizon — still zero when level,
    so it looks plausible, but it leans the wrong way exactly when you need it."""
    out = run(POSE + """
      console.log(JSON.stringify({ cw: hold(20, 0), ccw: hold(-20, 0) }));
    """)
    assert out["cw"]["angle"] == pytest.approx(-20, abs=0.2), "clockwise phone, anticlockwise line"
    assert out["ccw"]["angle"] == pytest.approx(20, abs=0.2)
    assert out["cw"]["level"] is False and out["ccw"]["level"] is False


def test_aiming_up_or_down_leaves_the_level_alone():
    """The regression that started this. A level answers one question — is the horizon straight —
    and pitch is not part of it. The reading used to be the angle from world-vertical, so aiming
    down to frame a shot climbed the number and killed the green. camera_tilt actively asks the user
    to aim lower, so the app broke its own indicator by being obeyed."""
    out = run(POSE + """
      console.log(JSON.stringify({ down25: hold(0, 25), down45: hold(0, 45), up30: hold(0, -30) }));
    """)
    for name, row in out.items():
        assert row["angle"] == pytest.approx(0, abs=0.2), f"{name} leaked pitch into the level"
        assert row["level"] is True, f"{name} lost the green while perfectly level"


def test_the_level_band_is_the_one_the_straighten_cue_clears_at():
    """Both sit at 3 degrees on purpose: the line should go green exactly as the cue goes away,
    not a couple of degrees either side of it."""
    out = run(POSE + """
      console.log(JSON.stringify({ inside: hold(2, 0), outside: hold(4, 0) }));
    """)
    assert out["inside"]["level"] is True
    assert out["outside"]["level"] is False


def test_a_phone_lying_flat_hides_the_line_rather_than_claiming_level():
    """Face up, almost no gravity is left in the screen plane, so roll is two noisy numbers handed
    to atan2 — it swings and can read a confident 0. It must also not touch liveLean, or a phone put
    down on a table would satisfy the straighten cue's latch."""
    out = run(POSE + """
      liveLean = 999;                       // sentinel: a flat reading must not overwrite it
      const flat = hold(0, 90);
      console.log(JSON.stringify({ flat, liveLeanAfter: liveLean }));
    """)
    assert out["flat"]["opacity"] == "0", "a meaningless angle must not be drawn as a horizon"
    assert out["flat"]["level"] is False
    assert out["liveLeanAfter"] == 999, "a flat phone quietly satisfied the straighten latch"


@pytest.mark.parametrize("roll", [0, 12, -12, 35])
def test_the_line_is_the_same_whichever_sign_convention_the_phone_uses(roll):
    """iOS reports accelerationIncludingGravity negated compared with everyone else — face-up on a
    table it reads z = -9.8 where Android reads +9.8 — which shifts the computed angle by exactly
    180 degrees. A line drawn at t and at t+180 looks the same, so folding the angle into (-90, 90]
    makes the difference vanish rather than trying to detect the platform.

    Detection was tried first and does not work: DeviceMotionEvent.requestPermission is defined by
    Chromium as well as by Safari, so the feature test that supposedly means "iOS" reports iOS on a
    Windows desktop. This asserts the property instead — same pose, either convention, same line.
    """
    out = run(POSE + f"""
      const p = pose({roll}, 0);
      const angle = () => {{
        const rot = styles.filter(s => s.id === 'camLevelLine' && s.prop === 'transform');
        return parseFloat(rot[rot.length - 1].val.match(/-?[0-9.]+/)[0]);
      }};
      onMotion({{ accelerationIncludingGravity: p }});
      const android = angle();
      onMotion({{ accelerationIncludingGravity: {{ x: -p.x, y: -p.y, z: -p.z }} }});
      const ios = angle();
      console.log(JSON.stringify({{ android, ios }}));
    """)
    assert out["android"] == pytest.approx(out["ios"], abs=0.2), (
        f"the line leans differently on the two conventions: {out}"
    )
    assert out["android"] == pytest.approx(-roll, abs=0.2), "clockwise phone, anticlockwise line"


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
      console.log(JSON.stringify({ coachingActive, tiltDirection, tiltFallback,
                                   needsStraightening, hints }));
    """)
    assert out["coachingActive"] is True
    # The direction is kept, not a finished sentence; the cue is phrased from it each frame, which
    # is what lets a language switch after the scan reach it. Read before running a frame, because
    # a frame with the phone held level would latch needsStraightening back off.
    assert out["tiltDirection"] == "down"
    assert out["tiltFallback"] == "Empty space above — aim a little lower"
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
      const afterScan = { tiltDirection, tiltFallback, needsStraightening, hint: hints[hints.length - 1] };
      resetCoaching();
      retake();
      const afterRetake = { tiltDirection, tiltFallback, needsStraightening, hint: hints[hints.length - 1] };
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
