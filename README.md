# 打卡吧！ (Dǎkǎ ba!)

A mobile web app that tells you **where to stand** for a good check-in photo.

Point the camera at an empty scene and tap. A vision model looks at the frame — the light, the
furniture, the doorway or bench worth using — and returns one plain sentence saying where to stand,
in the app's current language: *"Stand next to the drawer"* / *"站在抽屉旁边"*. The app shows it once
above the shutter, your subject stands there, you shoot.

There used to be an in-frame marker here, computed from OpenCV heuristics (visual balance,
background clutter, blown-out regions) with a live MediaPipe pose model tracking the subject
against it. Removed — see **Known issues** for why — in favour of the single sentence above.

The guiding idea: **subject positioning, framing and basic colour grading are what make the photo.**
Posing is left to the person being photographed.

---

## Live

| | |
|---|---|
| Frontend | <https://dakaba.pages.dev> (Cloudflare Pages — static files only, no build step) |
| Backend | <https://daka-backend-9bfz.onrender.com> (Render, free plan) |
| Health check | `GET /health` → `{"status": "ok"}` |

The backend is on Render's free plan, so it **spins down when idle** and the first request after a
cold start takes ~50s. A cron job pings `/health` to keep it warm. The frontend already handles the
slow case with a 60s timeout and a "server may be waking up" message.

---

## The flow

```
Home  →  camera  →  point at an empty scene, tap shutter
                         ↓
                    POST /analyze  (frame capped at 1024px, JPEG q0.7)
                         ↓
         capture gate: reject if lighting is Poor or the frame is blurry
                         ↓
    placement_hint shown as static text above the shutter ("Stand next to the drawer")
                         ↓
   subject stands there  →  straighten/tilt cues from the phone's own sensors  →  take the photo
                         ↓
         results: filter preview, hashtag pills, share/save
```

---

## How it works

| Piece | File | Notes |
|---|---|---|
| Frontend (all of it) | [`web/index.html`](web/index.html) | ~850 lines, HTML + CSS + JS inline. No build step. |
| API | [`api_server.py`](api_server.py) | FastAPI. Two routes: `/health`, `POST /analyze`. |
| Engine | [`scene_analysis.py`](scene_analysis.py) | OpenCV measurements + one vision-model call. |
| Response contract | [`CONTRACT.md`](CONTRACT.md) | **Read this before changing the response shape.** |

### The engine is deliberately split in two

**OpenCV does the measurable half** — brightness, colour balance, Canny edge density, Laplacian
blur, spectral-residual saliency, and Hough-line horizon tilt. This feeds `lighting`, `composition`,
`blueprint`, the blur gate, and `camera_tilt` (aim off a dead third of the frame).

**The vision model does the rest** — scene name, filter choice, hashtags, and, since the marker was
removed, the entire standing guide (`placement_hint`). That used to be a smaller, subjective half;
removing the OpenCV-computed marker moved positioning itself into this half, which is the one
consequence worth knowing before touching either side — see the callout below.

A vision-call failure still returns a usable scan with `scene_type: "Unknown"` rather than failing
outright — the OpenCV-computed fields never needed the model. But **positioning guidance now needs
the model too**: a degraded scan has `placement_hint: ""` and nothing to show above the shutter,
where it previously still had a marker computed from OpenCV alone. See
[`CONTRACT.md`](CONTRACT.md) §3.6 and §3.8. One further consequence for consumers — **a `200` is
not proof the model ran.**

There used to be a `_compute_placement` heuristic here — visual balance (stand opposite the
scene's focal mass), background cleanliness (prefer the emptier side), light direction (stand on
the dimmer side so light falls on your face), plus a hard backlight veto. It was removed along with
the marker it drove; see **Known issues** below for why, and `git show` on the commit that removed
it if the heuristic itself is ever wanted back.

---

## Local development

Requires **Python 3.13** (pinned in [`.python-version`](.python-version)).

### Backend

```bash
python -m venv .venv

# Windows
.venv/Scripts/python -m pip install -r requirements.txt -r requirements-dev.txt
# macOS / Linux
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt

# run it
.venv/Scripts/python -m uvicorn api_server:app --reload
```

Serves on <http://127.0.0.1:8000>. It **starts without an API key** — the OpenAI client is
constructed lazily — so `/health` works immediately and you only need a key to actually scan:

```bash
export OPENAI_API_KEY=sk-...        # bash
$env:OPENAI_API_KEY = "sk-..."      # PowerShell
```

Never commit the key. `.env`, `.env.*`, `*.key` and `daka_openai_key.txt` are all gitignored.

### Frontend

It's one static file with no build step, but **don't open it with `file://`** — the camera and
sensor APIs need a secure context, and a `file://` page sends `Origin: null`, which CORS can never
match. Serve it over HTTP:

```bash
cd web && python -m http.server 8080
```

### Pointing the frontend at your local backend

Two things have to change, and neither is obvious:

1. **The API URL is hardcoded** at [`web/index.html:325`](web/index.html#L325). Change it to
   `http://localhost:8000`.
2. **CORS will reject your origin.** The backend only allows the production domain by
   default, so set this in the shell running your *local* backend:

   ```bash
   export ALLOWED_ORIGINS=http://localhost:8080
   ```

If you skip step 2, every scan fails with a generic network error — browsers don't report CORS
failures usefully, so the app just toasts "Analysis failed" with nothing pointing at the cause.
It's a confusing hour if you don't know to look for it.

### Testing on a phone

Camera, `devicemotion` and `deviceorientation` all need HTTPS on a real device. The path of least
resistance is to push a branch and use the Cloudflare Pages branch preview — but note that
**preview URLs are a different origin and are CORS-blocked** by the production backend, so a
preview can load the UI but not scan. To scan from a preview you'd need to add its URL to
`ALLOWED_ORIGINS` in the Render dashboard.

---

## Tests

```bash
.venv/Scripts/python -m pytest        # or bare `pytest`
```

**255 tests, ~3 seconds.** No API key needed and no network calls — the OpenAI client is faked, and
the fixture makes constructing a real one a test failure.

The three `test_client_*` files run the page's own JavaScript in node against a stubbed DOM. They
exist because two bugs reached a phone that `node --check` could not see — both were valid syntax,
both were out-of-scope identifiers that only failed when the code actually ran.

| File | Tests | Covers |
|---|---|---|
| [`test_openai_paths.py`](tests/test_openai_paths.py) | 81 | moderation, every degradation path, request shapes, `_encode_image`, the `lang` prompt |
| [`test_assessments.py`](tests/test_assessments.py) | 40 | lighting, composition, blueprint — thresholds at their boundaries |
| [`test_api.py`](tests/test_api.py) | 31 | endpoint guards: size cap, rate limit, error mapping, CORS, `lang` |
| [`test_guidance.py`](tests/test_guidance.py) | 30 | dead-space tilt, the model's standing sentence, the client/engine filter agreement |
| [`test_client_i18n.py`](tests/test_client_i18n.py) | 18 | **the language switch, run for real in node** — both string tables, every key the markup and script ask for, the tilt cue's key |
| [`test_client_overlay.py`](tests/test_client_overlay.py) | 15 | `visibleCrop` maths and the camera-region layout invariants |
| [`test_filters.py`](tests/test_filters.py) | 14 | pixel-baked filters match the CSS preview exactly |
| [`test_client_loop.py`](tests/test_client_loop.py) | 13 | **the render loop and the tilt/straighten coaching flow, run for real in node** |
| [`test_features.py`](tests/test_features.py) | 13 | `extract_features` on synthetic scenes, all three blur regimes |

CI runs the suite on every PR to `main` ([`.github/workflows/tests.yml`](.github/workflows/tests.yml)).
Test-only dependencies live in `requirements-dev.txt` so Render's build stays lean.

### Conventions worth keeping

**Mutation-test new tests.** A green suite proves nothing about whether it *can* fail. Break the
thing on purpose, confirm a test catches it, revert. This caught three pieces of unreachable or
redundant code, and one test guard that had silently stopped working. **Commit before you start** —
the revert step is `git checkout --`, which will happily delete uncommitted work.

**The response contract is additive.** Never rename, remove or retype an existing field without
bumping the version in [`CONTRACT.md`](CONTRACT.md) and telling the team.

**Assert real values, not tautologies.** `assert x in (a, b)` where those are the only two possible
values tests nothing. Two such assertions shipped here before mutation testing found them.

---

## Deployment

Both sides auto-deploy from `main`.

| | Config | Notes |
|---|---|---|
| Backend | [`render.yaml`](render.yaml) | Build `pip install -r requirements.txt`, start `uvicorn api_server:app`. Health check `/health`. |
| Frontend | Cloudflare Pages dashboard | No config in-repo; no build command, output directory is `web/`. |

### Environment variables

Set in the Render dashboard (Environment tab):

| Variable | Required | Default | Notes |
|---|---|---|---|
| `OPENAI_API_KEY` | **yes** | — | Never committed (`sync: false` in `render.yaml`). |
| `ALLOWED_ORIGINS` | no | `https://dakaba.pages.dev` | Comma-separated. **Replaces** the default rather than adding to it, so keep the production origin in the list. |

Dependencies are pinned exactly (`==`) in `requirements.txt`, and Python is pinned in
`.python-version` — which CI reads too, so CI and production can't drift apart. To upgrade
something, bump one line and run the tests; don't bulk-refresh.

---

## Security posture

`/analyze` is **unauthenticated by design** — this is a school project and adding auth was
considered and declined. What protects it is cost control, not access control:

- **CORS** pinned to the live frontend origin. Browser-enforced only; does nothing against a direct `curl`.
- **Rate limit** 20 requests / 60s per IP. In-memory, so it resets on every cold start, and it keys
  on a spoofable `X-Forwarded-For`.
- **Size cap** 8 MB, enforced while reading in chunks so a hostile body can't be buffered first.
- **Error bodies** are fixed user-safe strings; exception detail is logged, never returned.

Each `/analyze` costs two OpenAI calls (moderation + vision), so the endpoint spends money. If the
URL ever leaks widely, the rate limit is a speed bump, not a wall.

**Moderation fails open**: if the moderation call itself errors, the image is treated as safe. That
was judged better than refusing every scan during an outage, but it is a bypass. It's logged.

---

## Known issues

**The standing marker and its subject tracking are gone.** The app used to draw an in-frame
footprint at an OpenCV-computed `placement` point, with a MediaPipe pose model tracking the
subject's ankles against it live (green when on the spot, cues like "Move them left" otherwise).
Removed: positioning guidance is now a single sentence from the vision model
(`placement_hint`, e.g. "Stand next to the drawer"), shown once as static text above the shutter.
This trades a live, self-correcting cue for a simpler one that costs nothing extra (the same vision
call already ran) but cannot tell the user whether they're actually standing in the right spot, and
— see the OpenAI-dependency callout above — no longer degrades gracefully to *any* positioning
guidance when the vision call fails. The removed code (`_compute_placement`, the MediaPipe
integration, the marker-drawing and subject-detection loop in `web/index.html`) is recoverable from
git history if live tracking is ever wanted back.

**No "you've moved since scanning" warning.** There used to be a two-dot framing lock for this, but
it was removed because it couldn't work, before the marker itself was later removed too. It read
yaw from `deviceorientation.alpha`, and a phone held upright with the rear camera on the horizon
sits at `beta ≈ 90°` — the gimbal-lock singularity of the W3C `Z-X'-Y''` angle sequence, where
`alpha` and `gamma` become degenerate. `alpha` swung wildly while the phone was nearly still, so the
dots jumped and never settled. Not a tuning problem and not a sign error: the sensor can't separate
yaw from roll in exactly the pose this app is used in.

A warning like this would need a different signal — integrating `devicemotion.rotationRate` over
the short scan-to-shoot window. Gravity can measure pitch and roll reliably but cannot measure yaw
at all, and panning is the main way people re-aim. The level slider is unaffected either way: it
reads gravity, not orientation.

**Extreme blur escapes the blur gate.** Past a point every edge smears below Canny's threshold, edge
density hits zero, and the "too plain to judge" escape hatch passes the frame — a plain wall and a
destroyed image are indistinguishable by edge density alone. Probably narrower on real broadband
scenes than on synthetic tests. Characterised in `test_features.py`.

**One bit of dead-but-harmless code**, found by mutation testing and documented in the tests: the
`if not content` guard in `_analyze_with_gpt` is redundant with the parse guard below it.

**Generated but never displayed:** `lighting.tip` and `blueprint.notes`. Both free (pure OpenCV, no
tokens). `blueprint.notes` speaks directly to framing, so it's the most natural thing to surface
next — `composition.horizon` already made that jump, driving the live straighten cue.

`pose_tips` was removed in contract `0.10` — see [`CONTRACT.md`](CONTRACT.md) for why. The prompt is
recoverable from `git show 3878ccb` if it's ever wanted back.
