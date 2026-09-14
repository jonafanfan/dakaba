"""Runtime checks on the viewfinder overlay drawing code.

The client had no tests at all, and that let a real bug ship: a refactor changed
`drawStandMarker(tf, ctx, ...)` to `drawStandMarker(W, H, ctx, ...)` but left one `tf.H` behind in
the caption's font line. `tf` was no longer in scope, so the function threw a ReferenceError on
every frame — after drawing the marker but before drawing its caption. The exception escaped the
whole coaching block, so `setCue()` never ran and every cue froze on the placeholder text baked
into the HTML. Silently, and only on a device.

`node --check` cannot catch that: it is valid syntax. Only *running* the function does. These
tests extract the real function from the shipped page and execute it against a stub canvas, so an
out-of-scope identifier fails here instead of on someone's phone.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[1] / "web" / "index.html"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def cue_reserved_px():
    """How much of the viewfinder bottom the cue pill occupies.

    Read from the page rather than restated here: when the controls moved out of the viewfinder
    this number changed, and a test carrying its own copy would have gone on asserting the old
    geometry while passing.
    """
    html = PAGE.read_text(encoding="utf-8")
    m = re.search(r"const CUE_RESERVED_PX = (\d+);", html)
    assert m, "CUE_RESERVED_PX not found in the page"
    return int(m.group(1))


def function_source(name):
    """Pull one top-level function out of the page, plus the constants it closes over."""
    html = PAGE.read_text(encoding="utf-8")
    match = re.search(r"^    (?:async )?function " + name + r"\(.*?^    \}$", html, re.S | re.M)
    assert match, f"{name} not found in {PAGE.name}"
    preamble = "const CUE_RESERVED_PX = %d;\n" % cue_reserved_px()
    return preamble + match.group(0)


def run_js(script):
    """Run a snippet in node. Returns whatever it prints as JSON on the last line."""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        # encoding matters: text=True alone uses the platform locale, mangling non-ASCII.
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert result.returncode == 0, (
        f"node exited {result.returncode}\n--- stderr ---\n{result.stderr.strip()}"
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


STUB_CTX = """
const called = [];
const ctx = {
  save(){}, restore(){}, beginPath(){}, ellipse(){}, fill(){}, stroke(){},
  moveTo(){}, lineTo(){}, setLineDash(){},
  measureText(s){ return { width: s.length * 7 }; },
  fillText(t, x, y){ called.push({ t, x, y }); },
};
"""


def test_marker_draws_without_throwing():
    """The regression itself: any out-of-scope identifier surfaces as a non-zero exit."""
    out = run_js(function_source("drawStandMarker") + STUB_CTX + """
      drawStandMarker(390, 844, ctx, 0.667, 0.667, false, 'Light falls on your face');
      console.log(JSON.stringify({ captions: called.length }));
    """)
    assert out["captions"] == 1, "the caption should have been drawn exactly once"


def test_marker_draws_with_no_caption():
    """No reason text is a normal state — older responses carry none."""
    out = run_js(function_source("drawStandMarker") + STUB_CTX + """
      drawStandMarker(390, 844, ctx, 0.333, 0.667, true, '');
      console.log(JSON.stringify({ captions: called.length }));
    """)
    assert out["captions"] == 0


def test_caption_stays_inside_the_frame():
    """A marker near the edge must not push its own caption off screen."""
    out = run_js(function_source("drawStandMarker") + STUB_CTX + """
      const W = 390;
      const seen = [];
      for (const x of [0.02, 0.333, 0.667, 0.98]) {
        called.length = 0;
        drawStandMarker(W, 844, ctx, x, 0.667, false, 'Stand in front of the blue door');
        const c = called[0];
        seen.push({ x, left: c.x - ctx.measureText('Stand in front of the blue door').width / 2,
                       right: c.x + ctx.measureText('Stand in front of the blue door').width / 2 });
      }
      console.log(JSON.stringify({ seen, W }));
    """)
    for row in out["seen"]:
        assert row["left"] >= -1, f"caption runs off the left at x={row['x']}"
        assert row["right"] <= out["W"] + 1, f"caption runs off the right at x={row['x']}"


def test_marker_maps_normalised_coords_straight_to_the_canvas():
    """No cover transform here — engine coords are already relative to the visible crop.

    Re-introducing one is exactly what put the marker off screen before, so this pins the mapping.
    """
    out = run_js(function_source("drawStandMarker") + STUB_CTX + """
      drawStandMarker(400, 800, ctx, 0.5, 0.5, false, 'x');
      console.log(JSON.stringify({ y: called[0].y }));
    """)
    # caption sits just under the footprint: fy + rh + 10, with fy = 0.5*800 and rh = 0.028*800
    assert out["y"] == pytest.approx(0.5 * 800 + 0.028 * 800 + 10)


def test_visible_crop_matches_the_screen_aspect():
    """The crop fed to the engine must be exactly what the viewfinder shows, for any track shape.

    When these disagreed, the engine was analysing scenery the user could not see and the marker
    was drawn off screen.
    """
    out = run_js(function_source("visibleCrop") + """
      const rows = [];
      for (const [vw, vh] of [[1920,1440],[1440,1920],[1440,1440],[640,480]]) {
        const c = visibleCrop({ videoWidth: vw, videoHeight: vh, clientWidth: 390, clientHeight: 520 });
        rows.push({ vw, vh, ...c });
      }
      console.log(JSON.stringify(rows));
    """)
    for row in out:
        assert row["sw"] <= row["vw"] and row["sh"] <= row["vh"], "crop must fit inside the track"
        assert row["sx"] >= 0 and row["sy"] >= 0
        assert row["sw"] / row["sh"] == pytest.approx(390 / 520, rel=0.01), (
            f"crop aspect must match the frame for a {row['vw']}x{row['vh']} track"
        )


# ── caption placement vs the cue pill ────────────────────────────────────────

def test_caption_flips_above_the_marker_when_the_cue_pill_would_cover_it():
    """placement.y runs 0.84-0.90, i.e. the footprint sits near the bottom edge, so the caption
    below it is always under the cue pill and always has to flip. The reserved height comes from
    the page rather than being restated here."""
    out = run_js(function_source("drawStandMarker") + STUB_CTX + """
      const H = 844, rows = [];
      for (const y of [0.84, 0.88, 0.90]) {
        called.length = 0;
        drawStandMarker(390, H, ctx, 0.667, y, false, 'Light falls on your face');
        rows.push({ y, capY: called[0].y, footY: y * H });
      }
      console.log(JSON.stringify(rows));
    """)
    reserved = cue_reserved_px()
    for row in out:
        clear_of_pill = row["capY"] <= 844 - reserved
        above_marker = row["capY"] < row["footY"]
        assert clear_of_pill or above_marker, (
            f"at y={row['y']} the caption sits at {row['capY']}, under the cue pill"
        )


def test_a_high_marker_still_captions_below():
    """Flipping is a last resort — below the footprint reads better, so keep it where it fits.

    Off the engine's band on purpose: nothing produces 0.60 today, but the rule is "below when
    there is room", not "below when y is small", and a test that only used live values could not
    tell the two apart.
    """
    out = run_js(function_source("drawStandMarker") + STUB_CTX + """
      drawStandMarker(390, 844, ctx, 0.667, 0.60, false, 'Cleaner background here');
      console.log(JSON.stringify({ capY: called[0].y, footY: 0.60 * 844 }));
    """)
    assert out["capY"] > out["footY"], "should still sit below when there is room"


def test_the_body_box_reaches_the_top_of_a_standing_person():
    """The dotted figure is drawn UP from the footprint, so it has to grow as the feet drop — at
    the old 0.6 height a marker at 0.88 described someone from the chest down."""
    html = PAGE.read_text(encoding="utf-8")
    box = re.search(r"boxH = ([0-9.]+) \* H", html)
    assert box, "the body box height is no longer a literal multiple of the frame height"
    feet, head = 0.88, 0.88 - float(box.group(1))
    assert 0.08 <= head <= 0.22, (
        f"a figure standing at {feet} would have its head at {head:.2f} of the frame"
    )


# ── the photo area and the controls are separate regions ─────────────────────

def camera_section():
    html = PAGE.read_text(encoding="utf-8")
    m = re.search(r'<section id="camera".*?</section>', html, re.S)
    assert m, "camera section not found"
    return m.group(0)


def region(name):
    """The markup inside one region of the camera screen.

    Depth starts at 1, not 0: the opening <div> sits before the class attribute we search from, so
    counting from zero stops at the first *nested* close and silently returns a truncated region.
    That version made two of these tests pass by looking at the wrong markup.
    """
    section = camera_section()
    start = section.index('class="' + name + '"')
    depth, i = 1, section.index(">", start)
    while i < len(section):
        if section.startswith("<div", i):
            depth += 1
        elif section.startswith("</div>", i):
            depth -= 1
            if depth == 0:
                return section[start:i]
        i += 1
    raise AssertionError("unbalanced markup reading " + name)


def test_the_video_is_inside_the_frame():
    """visibleCrop measures the video element, so the video element has to BE the photo area.
    While it filled the whole screen, the capture included a strip hidden behind the controls."""
    assert 'id="video"' in region("cam-frame")


def test_the_controls_are_outside_the_viewfinder():
    """The bug: the tinted panel with Take Photo sat over the bottom of a full-screen video, so
    the shot extended past what the user could see. Someone framed to the visible edge came out
    higher in the photo than they had been placed."""
    viewfinder = region("cam-frame")
    for control in ('id="tipPanel"', 'id="camIdle"', 'id="lensToggle"', 'id="takePhotoBtn"'):
        assert control not in viewfinder, f"{control} is back inside the photo area"


def test_the_controls_region_exists_and_holds_them():
    controls = region("cam-controls")
    for control in ('id="tipPanel"', 'id="camIdle"', 'id="lensToggle"'):
        assert control in controls, f"{control} is not in the controls strip"


def test_the_controls_do_not_float():
    """A control positioned absolutely would drift back over the image even from outside the
    viewfinder markup, which is how this regressed in the first place."""
    html = PAGE.read_text(encoding="utf-8")
    for selector in (r"\.cam-controls", r"\.tip-panel", r"\.cam-idle"):
        rule = re.search(selector + r"\s*\{([^}]*)\}", html)
        assert rule, f"no CSS rule for {selector}"
        assert "position: absolute" not in rule.group(1), (
            f"{selector} is absolutely positioned and can cover the photo again"
        )


def test_only_transient_huds_overlay_the_image():
    """A cue, a badge and a level bar over the picture are normal camera behaviour and stay. The
    test is that nothing opaque and tall joins them."""
    viewfinder = region("cam-frame")
    allowed = {
        "camFrame",                                     # the container itself
        "video", "liveOverlay", "gridOverlay",          # the image and what is drawn on it
        "coachCue", "coachText", "statusChip",          # transient text
        "sceneBadgeTop",                                # small label
        "camLevel", "camLevelRef", "camLevelLine",      # the horizon level
    }
    found = set(re.findall(r'id="([\w-]+)"', viewfinder))
    assert "video" in found, "the region helper is not reading the frame"
    unexpected = found - allowed
    assert not unexpected, (
        f"{sorted(unexpected)} added over the photo area — is it meant to be in the shot?"
    )


def test_the_wordmark_outweighs_the_start_button():
    """The button had grown into the biggest thing on the home screen, which put the emphasis on
    "press this" rather than on the mark. Compares the smallest size the wordmark can take against
    the button's full height, so it holds on the narrowest phone too."""
    html = PAGE.read_text(encoding="utf-8")
    char = re.search(r"--mark-size:\s*clamp\((\d+)px", html)
    assert char, "the wordmark size token is gone, or is no longer clamped"
    button = re.search(r"\.start-btn\s*\{[^}]*height:\s*(\d+)px", html)
    assert button, "no height on the start button"
    assert int(char.group(1)) > int(button.group(1)) * 1.4, (
        f"wordmark {char.group(1)}px vs button {button.group(1)}px — the button dominates again"
    )


def test_the_controls_bar_adds_nothing_over_the_surround():
    """Twice now the bar has been given a panel treatment and twice it read as a separate band.

    It sits on the same full-screen blurred feed as the strip above the frame, so anything it
    paints — a tint, however light, or a blur of its own at a second radius — makes the bottom of
    the screen differ from the top. Transparent is the only version guaranteed to match, because
    it is then literally the same pixels.
    """
    html = PAGE.read_text(encoding="utf-8")
    rule = re.search(r"\.cam-controls\s*\{([^}]*)\}", html).group(1)
    # Comments explain what the rule must NOT do, and naming a property there is not declaring it.
    rule = re.sub(r"/\*.*?\*/", "", rule, flags=re.S)
    background = re.search(r"background:\s*([^;]+);", rule)
    assert background, "no background declared — is the rule still here?"
    assert background.group(1).strip() == "transparent", (
        f"the bar paints {background.group(1).strip()} over the surround"
    )
    assert "backdrop-filter" not in rule, (
        "a second blur at a different radius cannot match the one already behind it"
    )
    assert "border-top" not in rule, "a border draws a line the surround does not have"


def test_the_controls_text_survives_losing_its_tint():
    """Legibility came from the tint. Without one it has to come from the text itself, or small
    labels vanish against a bright scene."""
    html = PAGE.read_text(encoding="utf-8")
    rule = re.search(r"\.cam-controls\s*\{([^}]*)\}", html).group(1)
    assert "text-shadow" in rule, "nothing keeps the controls legible over a bright frame"


def test_the_controls_bar_animates_its_height():
    """The three states hold different controls, so the bar changes height and the frame centred
    above it moves with it. Without the transition that move is an instant snap."""
    html = PAGE.read_text(encoding="utf-8")
    rule = re.search(r"\.cam-controls\s*\{([^}]*)\}", html).group(1)
    assert "transition:" in rule and "height" in rule, "the bar's height change is not animated"
    assert "overflow: hidden" in rule, "an animated height needs its content clipped"
    state = re.search(r"function setCamState\(s\) \{(.*?)\n    \}", html, re.S).group(1)
    assert "resizeControls()" in state, "nothing re-measures the bar when the state changes"


def test_the_level_is_out_of_the_scene_badge_s_way():
    """Both were once centred at the top of the frame, so the level sat across the scene label.

    The level is a horizon line at mid-frame now rather than a bar in a corner, so the separation
    is vertical where it used to be horizontal. Same requirement either way, and still worth
    pinning: the badge owns the top of the frame, so the level must not be there.

    Asserted from the CSS rather than by rendering: there is no browser here to measure with.
    """
    html = PAGE.read_text(encoding="utf-8")
    lines = re.search(r"#camLevelRef, #camLevelLine\s*\{([^}]*)\}", html, re.S)
    assert lines, "no rule for the level's lines"
    assert "top: calc(50%" in lines.group(1), (
        "the level has left mid-frame — check it cannot reach the scene badge again"
    )
    badge = re.search(r"\.scene-badge-top\s*\{([^}]*)\}", html, re.S)
    assert badge, "no .scene-badge-top rule"
    assert "top: calc(50%" not in badge.group(1) and "top: 50%" not in badge.group(1), (
        "the badge has moved to mid-frame, where the level line now lies"
    )


def test_a_long_scene_name_stays_within_its_own_badge():
    """Kept from when the level sat in the top corner and a wide enough label could still reach it.
    The level has moved to mid-frame since, so this no longer guards a collision — but an unbounded
    badge that grows to the frame edge is its own bug, and "Riverside Promenade" is a real scene
    name, so the cap stays pinned."""
    html = PAGE.read_text(encoding="utf-8")
    badge = re.search(r"\.scene-badge-top\s*\{([^}]*)\}", html)
    assert badge, "no .scene-badge-top rule"
    assert "max-width" in badge.group(1), "an unbounded badge can grow under the level bar"
    assert "text-overflow: ellipsis" in badge.group(1), "a capped badge must truncate, not clip"


# ── native capture shape ─────────────────────────────────────────────────────

def test_the_frame_is_a_standard_photo_shape():
    """3:4, the shape a phone sensor natively produces and every camera app shows.

    Letting the frame fill whatever space was left over gave an arbitrary tall rectangle matching
    no standard photo, and meant a second crop off an already-cropped stream.
    """
    html = PAGE.read_text(encoding="utf-8")
    rule = re.search(r"\.cam-frame\s*\{([^}]*)\}", html)
    assert rule, "no .cam-frame rule"
    assert "aspect-ratio: 3 / 4" in rule.group(1), "the frame is no longer a standard photo shape"


def test_the_camera_is_asked_for_a_matching_aspect():
    """16:9 is already a crop of a 4:3 sensor, so asking for it threw pixels away before the frame
    had even cropped. Every getUserMedia call should ask for 4:3."""
    html = PAGE.read_text(encoding="utf-8")
    requests = re.findall(r"width: \{ ideal: (\d+) \}, height: \{ ideal: (\d+) \}", html)
    assert requests, "no camera resolution constraints found"
    for w, h in requests:
        ratio = int(w) / int(h)
        assert ratio == pytest.approx(4 / 3, rel=0.01), (
            f"camera asked for {w}x{h} ({ratio:.2f}), which is not the sensor's 4:3"
        )


def test_the_home_screen_frame_is_not_the_camera_frame():
    """Both were once called .viewfinder, so the camera rule leaked onto the home screen — its
    corner marks sit outside their box and overflow:hidden clipped them, on a black background."""
    html = PAGE.read_text(encoding="utf-8")
    home = re.search(r"^    \.viewfinder\s*\{([^}]*)\}", html, re.M)
    assert home, "home .viewfinder rule not found"
    for leaked in ("overflow: hidden", "background: #000", "aspect-ratio"):
        assert leaked not in home.group(1), f"camera styling has leaked onto the home frame: {leaked}"
