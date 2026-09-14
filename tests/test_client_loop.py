"""Runs the real render loop against a stubbed DOM.

Two bugs shipped to a phone in consecutive PRs, both the same shape: an identifier that was not in
scope, inside the overlay code. `tf.H` after a signature change, then `ts` after adding subject
detection to a loop that takes no timestamp parameter. Both are valid JavaScript, so `node --check`
passed and CI was green. Both only failed when the function actually ran.

`test_client_overlay.py` executes individual drawing functions. This file executes the *loop* —
the thing that decides whether anything gets drawn at all — because that is where both bugs lived
and neither would have been caught by testing the drawing functions alone.

The stub is deliberately dumb: it records calls and returns plausible values. It is not a browser
and cannot tell you the marker looks right. What it can tell you is that a frame completes without
throwing, and that the marker and cue are actually reached — which is exactly what was broken.
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
const drawn = [];      // every marker / caption the overlay draws
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
        save(){}, restore(){}, beginPath(){}, ellipse(){ drawn.push('marker'); },
        fill(){}, stroke(){}, moveTo(){}, lineTo(){}, setLineDash(){},
        clearRect(){}, drawImage(){}, putImageData(){},
        fillText(t){ drawn.push('caption:' + t); },
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
    js = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    # The trailing $('id').addEventListener(...) wiring runs fine against the stub, but the two
    # IIFEs and the dynamic import of MediaPipe do not belong in a unit test.
    js = js.replace("import(MP)", "Promise.reject(new Error('no cdn'))")
    return js


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
  standPos = { x: 0.667, y: 0.667 };
  standReason = 'Light falls on your face';
  coachingActive = true;
  liveActive = true;
"""


def test_a_frame_completes_without_throwing():
    """The regression both bugs would have failed. Neither was a syntax error."""
    out = run(SCAN + """
      liveLoop(1234);
      console.log(JSON.stringify({ drawn, cues }));
    """)
    assert out["drawn"], "nothing was drawn — the loop threw before reaching the marker"


def test_the_marker_is_drawn():
    out = run(SCAN + """
      liveLoop(1234);
      console.log(JSON.stringify({ drawn }));
    """)
    assert "marker" in out["drawn"], f"marker missing; drew {out['drawn']}"


def test_the_caption_is_drawn():
    """This is what the `tf` bug broke: the marker drew, the caption did not."""
    out = run(SCAN + """
      liveLoop(1234);
      console.log(JSON.stringify({ drawn }));
    """)
    captions = [d for d in out["drawn"] if d.startswith("caption:")]
    assert captions == ["caption:Light falls on your face"], f"got {captions}"


def test_a_cue_is_set_every_frame():
    """This is what BOTH bugs broke: the exception escaped before setCue ran, so the cue froze on
    whatever text the HTML shipped with."""
    out = run(SCAN + """
      liveLoop(1234);
      console.log(JSON.stringify({ cues }));
    """)
    assert out["cues"], "no cue was set — the loop threw before reaching setCue"


def test_the_loop_keeps_scheduling_itself():
    out = run(SCAN + """
      liveLoop(1234);
      console.log(JSON.stringify({ rafCount }));
    """)
    assert out["rafCount"] == 1, "the loop must re-arm even on a frame that draws nothing"


def test_it_survives_a_frame_with_no_scan_yet():
    """Before any scan there is no standPos. The loop still has to run and re-arm."""
    out = run("""
      liveActive = true;
      liveLoop(1234);
      console.log(JSON.stringify({ rafCount, drawn }));
    """)
    assert out["rafCount"] == 1
    assert out["drawn"] == [], "nothing should be drawn before a scan"


def test_it_survives_the_tracker_never_loading():
    """The CDN is blocked in this harness, so this is the real degradation path."""
    out = run(SCAN + """
      tracker.failed = true;
      liveLoop(1234);
      console.log(JSON.stringify({ drawn, cues }));
    """)
    assert "marker" in out["drawn"], "the marker must still draw without subject detection"
    assert out["cues"], "a fallback cue must still be set"


# ── the coaching flow: one cue at a time, in the right order ─────────────────
#
# The requested order is subject -> position -> camera. These pin it down, because the order is a
# product decision rather than an implementation detail: a crooked frame nobody is standing in is
# not worth complaining about yet.

def cue_for(setup):
    out = run(SCAN + """
      tracker.landmarker = {};              // pretend the model loaded
      streak = CONFIRM_FRAMES;              // and that detection has been confirmed
      """ + setup + """
      liveLoop(1234);
      console.log(JSON.stringify({ cues }));
    """)
    return out["cues"][-1]


def test_no_subject_asks_them_into_frame():
    assert cue_for("subject.seen = false;") == "Get them in frame"


def test_subject_off_to_one_side_is_told_which_way():
    assert cue_for("subject.seen = true; subject.x = 0.30; subject.y = 0.667;") == "Move them right"
    assert cue_for("subject.seen = true; subject.x = 0.95; subject.y = 0.667;") == "Move them left"


def test_subject_too_far_or_too_near_is_told_so():
    """Feet higher in frame than the marker means further away."""
    assert cue_for("subject.seen = true; subject.x = 0.667; subject.y = 0.40;") == "Bring them closer"
    assert cue_for("subject.seen = true; subject.x = 0.667; subject.y = 0.95;") == "Send them back"


def test_position_is_settled_before_the_camera_is_mentioned():
    """Someone still walking into place must not also be told to straighten the camera."""
    cue = cue_for("""
      subject.seen = true; subject.x = 0.30; subject.y = 0.667;
      needsStraightening = true; liveLean = 20; tiltDirection = 'down';
    """)
    assert cue == "Move them right", f"camera cue jumped the queue: {cue!r}"


def test_dead_space_is_cued_when_the_horizon_is_fine():
    """Phrased at render time from the direction, not stored as a finished sentence — see
    test_client_i18n for why that distinction is load-bearing."""
    assert cue_for("subject.seen = true; subject.x = 0.667; subject.y = 0.667; tiltDirection = 'down'; tiltFallback = 'engine wording';") == \
        "Empty space above — aim a little lower"


def test_an_unphraseable_direction_falls_back_to_the_engines_wording():
    assert cue_for("subject.seen = true; subject.x = 0.667; subject.y = 0.667; tiltDirection = 'sideways'; tiltFallback = 'Engine wording';") == \
        "Engine wording"


def test_straightening_takes_priority_over_the_tilt_hint():
    cue = cue_for("subject.seen = true; subject.x = 0.667; subject.y = 0.667; needsStraightening = true; liveLean = 20; tiltDirection = 'down';")
    assert cue == "Straighten the camera", f"tilt hint jumped the queue: {cue!r}"


def test_the_needs_fix_class_follows_the_straighten_cue():
    out = run(SCAN + """
      tracker.landmarker = {}; streak = CONFIRM_FRAMES;
      subject.seen = true; subject.x = 0.667; subject.y = 0.667;
      needsStraightening = true; liveLean = 20;
      liveLoop(1234);
      needsStraightening = false;
      liveLoop(1235);
      const fixToggles = toggles.filter(t => t.id === 'camLevel' && t.cls === 'needs-fix');
      console.log(JSON.stringify({ fixToggles }));
    """)
    vals = [t["val"] for t in out["fixToggles"]]
    assert vals == [True, False], f"needs-fix should track the straighten cue, got {vals}"


def test_camera_cues_come_once_they_are_on_the_marker():
    on_marker = "subject.seen = true; subject.x = 0.667; subject.y = 0.667;"
    assert cue_for(on_marker + "needsStraightening = true; liveLean = 20;") == "Straighten the camera"
    assert cue_for(on_marker + "tiltDirection = 'down'; tiltFallback = 'engine wording';") == \
        "Empty space above — aim a little lower"


def test_everything_satisfied_says_take_the_photo():
    assert cue_for("subject.seen = true; subject.x = 0.667; subject.y = 0.667;") \
        == "Perfect — take the photo"


def test_the_marker_is_green_only_when_someone_is_on_it():
    """Orange means 'not yet' at a glance — the whole point of the colour."""
    out = run(SCAN + """
      tracker.landmarker = {};
      subject.seen = true; subject.x = 0.667; subject.y = 0.667;
      liveLoop(1234);
      const wasOn = onMarker;
      subject.x = 0.20;
      liveLoop(1235);
      console.log(JSON.stringify({ wasOn, nowOn: onMarker }));
    """)
    assert out["wasOn"] is True and out["nowOn"] is False


# ── false-positive resistance ────────────────────────────────────────────────

def test_a_single_detection_frame_is_not_believed():
    """A person-shaped object produces one good frame. A person produces many.

    Without this the marker turned green the instant the model guessed, which it will do on an
    empty room because it is trained to find someone rather than to decide whether anyone is there.
    """
    out = run(SCAN + """
      tracker.landmarker = {};
      streak = 1;                      // one confirming frame so far
      subject.seen = false;
      liveLoop(1234);
      console.log(JSON.stringify({ cues, onMarker }));
    """)
    assert out["cues"][-1] == "Get them in frame"
    assert out["onMarker"] is False, "must not go green on a single frame"


def test_confirmation_is_required_before_the_marker_can_go_green():
    out = run(SCAN + """
      tracker.landmarker = {};
      subject.seen = true; subject.x = 0.667; subject.y = 0.667;
      streak = CONFIRM_FRAMES;
      liveLoop(1234);
      console.log(JSON.stringify({ onMarker }));
    """)
    assert out["onMarker"] is True


def test_the_marker_stays_orange_when_nobody_has_been_seen():
    """Found by mutation testing: dropping `subject.seen` from the onMarker test left every other
    test green, because they all set it true before checking the distance.

    The stale coordinates are the trap. subject.x/.y keep their last values after someone walks
    out of shot, so position alone still reads as "on the spot" — and a marker that goes green at
    an empty room is the exact failure the confirmation logic exists to prevent."""
    out = run(SCAN + """
      tracker.landmarker = {}; streak = CONFIRM_FRAMES;
      subject.seen = false;             // nobody in shot ...
      subject.x = 0.667; subject.y = 0.667;   // ... but the last sighting was right on the marker
      liveLoop(1234);
      console.log(JSON.stringify({ onMarker, cue: cues[cues.length - 1] }));
    """)
    assert out["onMarker"] is False, "marker went green with nobody in frame"
    assert out["cue"] == "Get them in frame"


# The <video> is shown cropped, so landmarks normalised to the full track have to be converted
# before they can be compared with a marker that lives in the visible crop. Skipping the
# conversion is what once drew the marker hundreds of pixels off screen.
POSE_LANDMARKS = """
function landmarksAt(x, footY, headY) {
  const lm = [];
  for (let i = 0; i < 33; i++) lm.push({ x, y: footY, visibility: 0.9 });
  lm[0]  = { x, y: headY, visibility: 0.9 };   // nose
  lm[11] = { x, y: headY + 0.1, visibility: 0.9 };
  lm[12] = { x, y: headY + 0.1, visibility: 0.9 };
  lm[27] = { x, y: footY, visibility: 0.9 };   // ankles
  lm[28] = { x, y: footY, visibility: 0.9 };
  return lm;
}
"""


def test_landmarks_are_converted_into_the_visible_crop():
    """The track is 1080x1920 shown in a 390x844 box, so `cover` keeps a centre slice about 887px
    wide. An ankle at x=0.8 of the full frame is therefore further right than 0.8 of what is on
    screen — reporting the raw value would put the subject somewhere nobody can see."""
    out = run(POSE_LANDMARKS + SCAN + """
      tracker.landmarker = { detectForVideo: () => ({ landmarks: [landmarksAt(0.8, 0.9, 0.2)] }) };
      streak = CONFIRM_FRAMES - 1;      // this frame is the confirming one
      subject.seen = false;
      detectSubject($('video'), 1234);
      console.log(JSON.stringify({ x: subject.x, seen: subject.seen }));
    """)
    assert out["seen"] is True, "a confirmed pose was not accepted"
    assert abs(out["x"] - 0.865) < 0.01, (
        f"landmark used raw instead of crop-relative: {out['x']:.3f} (raw would be 0.800)"
    )


def all_cue_strings():
    """Every cue the page can show, in English, including the ones inside ternaries.

    Matching only `setCue('...')` misses `setCue(a ? 'x' : 'y')` — which is where the movement
    cues live, i.e. exactly the ones this file cares about. That version of the check passed while
    testing nothing.

    Cues are now table keys rather than literals, so the keys are collected from the call sites and
    resolved through the page's own English table. Reading the table instead of restating it means
    a cue that is reworded here is still checked; a cue that is *deleted* disappears from both, so
    the guard test below keeps this from silently returning nothing.
    """
    page = PAGE.read_text(encoding="utf-8")
    keys = set()
    for call in re.findall(r"setCue\(([^;]*?)\);", page):
        keys |= set(re.findall(r"tr\('([^']+)'\)", call))
    english = run("console.log(JSON.stringify(STRINGS.en));")
    return {english[k] for k in keys if k in english}


def test_the_cue_scan_finds_the_movement_cues():
    """Guard on the guard: if this ever returns nothing, the test below is vacuous."""
    cues = all_cue_strings()
    assert len(cues) >= 6, f"expected the full cue set, found {sorted(cues)}"
    assert any(c.startswith("Move") for c in cues), "movement cues not found — check the regex"


def test_every_movement_cue_says_who_moves():
    """The reader is holding the phone. A cue telling *them* to move left, when it is the subject
    who should move — and screen-left at that — sends everyone the wrong way."""
    for cue in all_cue_strings():
        if cue.split()[0] in ("Move", "Bring", "Send", "Step", "Come"):
            assert " them" in cue, f"ambiguous about who moves: {cue!r}"


# ── the confirmation logic itself, driven by a fake pose ─────────────────────
#
# The flow tests above pre-set `subject.seen` and never enter detectSubject, so they say nothing
# about whether a detection is *believed*. These feed real landmark data through it.

FAKE_POSE = """
function pose(opts) {
  const o = Object.assign({ vis: 0.9, noseY: 0.25, ankleY: 0.85 }, opts || {});
  const lm = [];
  for (let i = 0; i < 33; i++) lm.push({ x: 0.5, y: 0.5, z: 0, visibility: o.vis });
  lm[0]  = { x: 0.5, y: o.noseY,  z: 0, visibility: o.vis };   // nose
  lm[11] = { x: 0.45, y: 0.40, z: 0, visibility: o.vis };      // shoulders
  lm[12] = { x: 0.55, y: 0.40, z: 0, visibility: o.vis };
  lm[27] = { x: 0.48, y: o.ankleY, z: 0, visibility: o.vis };  // ankles
  lm[28] = { x: 0.52, y: o.ankleY, z: 0, visibility: o.vis };
  return lm;
}
function fakeTracker(opts) {
  return { detectForVideo: () => ({ landmarks: [pose(opts)] }) };
}
"""


def detect_frames(n, opts="{}", start=1000):
    """Run n detections, spaced past the 100ms throttle, and report what was believed."""
    return run(SCAN + FAKE_POSE + f"""
      tracker.landmarker = fakeTracker({opts});
      let t = {start};
      for (let i = 0; i < {n}; i++) {{ t += 150; detectSubject($('video'), t); }}
      console.log(JSON.stringify({{ seen: subject.seen, streak }}));
    """)


def test_one_good_frame_is_not_enough():
    """A person-shaped object gives you one. This is the false-green fix."""
    out = detect_frames(1)
    assert out["seen"] is False, "believed a single frame"
    assert out["streak"] == 1


def test_three_good_frames_are_believed():
    out = detect_frames(3)
    assert out["seen"] is True, f"still not believed after 3 frames: {out}"


def test_a_low_confidence_pose_is_never_believed():
    """Landmark visibility below the floor means the model is guessing at background clutter."""
    out = detect_frames(6, opts="{ vis: 0.3 }")
    assert out["seen"] is False
    assert out["streak"] == 0, "a rejected frame must reset the streak, not bank it"


def test_a_pose_too_short_to_be_a_standing_person_is_rejected():
    """Head barely above the feet — the model stretched a pose over something that is not a person."""
    out = detect_frames(6, opts="{ noseY: 0.70, ankleY: 0.85 }")
    assert out["seen"] is False


def test_a_realistic_standing_height_is_accepted():
    out = detect_frames(3, opts="{ noseY: 0.30, ankleY: 0.88 }")
    assert out["seen"] is True


def test_a_pose_with_hidden_legs_is_rejected():
    """Legs and torso are checked separately, and this is what the leg check is for.

    A pose with a clear torso but invisible legs is the model guessing where someone's feet are
    behind furniture. Since the marker is a footprint, an invented foot position is exactly the
    wrong thing to trust. Written with a visible torso on purpose: a test that dims *everything*
    passes on the torso check alone and says nothing about the leg check.
    """
    out = run(SCAN + FAKE_POSE + """
      tracker.landmarker = { detectForVideo: () => {
        const lm = pose({ vis: 0.9 });
        lm[27].visibility = 0.2; lm[28].visibility = 0.2;   // ankles hidden
        lm[25].visibility = 0.2; lm[26].visibility = 0.2;   // knees hidden
        return { landmarks: [lm] };
      } };
      let t = 1000;
      for (let i = 0; i < 6; i++) { t += 150; detectSubject($('video'), t); }
      console.log(JSON.stringify({ seen: subject.seen, streak }));
    """)
    assert out["seen"] is False, "trusted a foot position the model could not actually see"


def test_the_model_is_configured_with_raised_confidence_floors():
    """Asserted statically, not behaviourally: the fake tracker replaces the model entirely, so
    nothing here can exercise MediaPipe's own thresholds. The defaults are 0.5, which is tuned for
    finding a person rather than deciding whether one is present."""
    page = PAGE.read_text(encoding="utf-8")
    for option in ("minPoseDetectionConfidence", "minPosePresenceConfidence", "minTrackingConfidence"):
        match = re.search(option + r":\s*([0-9.]+)", page)
        assert match, f"{option} is not set — the 0.5 default is too eager for this use"
        assert float(match.group(1)) >= 0.7, f"{option} dropped to {match.group(1)}"


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
    placement: { x: 0.333, y: 0.70, reason: 'light', reason_text: 'Light falls on your face' },
    camera_tilt: { direction: 'down', reason: 'Empty space above — aim a little lower' },
    composition: { horizon: 'Tilted' },
    placement_hint: 'Stand in front of the blue door',
    lighting: { quality: 'Good' },
    hashtags: [],
    filter: 'Vivid',
  };
"""


def test_retake_restores_the_coaching_state():
    """The whole point: the scene has not changed, so the same marker should come straight back."""
    out = run(ANALYSIS + """
      motionGranted = true;
      resetCoaching();                 // as stopLive() does on the way to the results screen
      retake();
      console.log(JSON.stringify({
        coachingActive, standPos, standReason, tiltDirection, tiltFallback,
        needsStraightening, hints,
      }));
    """)
    assert out["coachingActive"] is True
    assert out["standPos"] == {"x": 0.333, "y": 0.70}
    assert out["standReason"] == "Light falls on your face"
    # The direction is kept, not a finished sentence; the cue is phrased from it each frame, which
    # is what lets a language switch after the scan reach it. Read before running a frame, because
    # a frame with the phone held level would latch needsStraightening back off.
    assert out["tiltDirection"] == "down"
    assert out["tiltFallback"] == "Empty space above — aim a little lower"
    assert out["needsStraightening"] is True


def test_retake_costs_no_api_call():
    """The reason it exists. Re-scanning an unchanged scene costs a wait and two OpenAI calls to
    put the marker in exactly the same place."""
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
    restored session should not put the app into coaching with no marker to show."""
    out = run("""
      analysisResult = null;
      retake();
      console.log(JSON.stringify({ coachingActive, standPos }));
    """)
    assert out["coachingActive"] is False
    assert out["standPos"] is None


def test_a_scan_and_a_retake_produce_the_same_coaching_state():
    """Both go through beginCoaching, so they cannot drift apart. This is why it was extracted."""
    out = run(ANALYSIS + """
      motionGranted = true;
      beginCoaching(analysisResult);
      const afterScan = { standPos: { ...standPos }, standReason, tiltDirection, tiltFallback, needsStraightening, hint: hints[hints.length - 1] };
      resetCoaching();
      retake();
      const afterRetake = { standPos: { ...standPos }, standReason, tiltDirection, tiltFallback, needsStraightening, hint: hints[hints.length - 1] };
      console.log(JSON.stringify({ afterScan, afterRetake }));
    """)
    assert out["afterScan"] == out["afterRetake"]
