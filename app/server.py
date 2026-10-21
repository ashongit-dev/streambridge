import atexit
import hmac
import io
import json
import os
import threading
import time
import wave
from functools import wraps
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

import requests
from flask import Flask, Response, jsonify, request, send_file, send_from_directory
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError
from .obs import OBS, OBSRequestError
from .policy import choose_source
from .health import FeedHealth

DATA = Path(os.getenv("DATA_DIR", "/data"))
DATA.mkdir(parents=True, exist_ok=True)
PASSWORD = os.environ["ADMIN_PASSWORD"]
if len(PASSWORD) < 16:
    raise RuntimeError("ADMIN_PASSWORD must contain at least 16 characters")
MEDIA = os.getenv("MEDIA_URL", "http://media:9997")
HLS = os.getenv("HLS_URL", "http://media:8888")
MEDIA_AUTH = ("relay", os.environ["INGEST_PASSWORD"])
app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
obs = OBS()
lock = threading.RLock()
state_file = DATA / "state.json"
try:
    saved = json.loads(state_file.read_text())
except (FileNotFoundError, ValueError):
    saved = {}
state = dict(connected=False, streaming=False, reconnecting=False, preferred=saved.get("preferred", "desk"),
             automatic=saved.get("automatic", True), want_live=saved.get("want_live", False), active="fallback",
             inputs={s: {"healthy": False, "ready": False} for s in ("desk", "irl")},
             error="Connecting to encoder", destination="", output_bytes=0, frames=0, dropped=0)


def persist():
    temp = state_file.with_suffix(".tmp")
    temp.write_text(json.dumps({k: state[k] for k in ("preferred", "automatic", "want_live")}))
    os.chmod(temp, 0o600)
    temp.replace(state_file)


def protected(fn):
    @wraps(fn)
    def inner(*args, **kwargs):
        auth = request.authorization
        if not auth or auth.username != "admin" or not hmac.compare_digest((auth.password or "").encode(), PASSWORD.encode()):
            return Response("Sign in as admin", 401, {"WWW-Authenticate": 'Basic realm="Streambridge"'})
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("X-Relay-Request") != "1":
            return jsonify(error="Missing request header"), 403
        return fn(*args, **kwargs)
    return inner


@app.after_request
def headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; worker-src blob:; connect-src 'self'; frame-ancestors 'none'"
    return response


def fallback_image():
    target = DATA / "fallback.png"
    if target.exists():
        return
    image = Image.new("RGB", (1280, 720), "#101a2e")
    draw = ImageDraw.Draw(image)
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    try:
        large, small = ImageFont.truetype(font_path, 62), ImageFont.truetype(font_path, 27)
    except OSError:
        large = small = ImageFont.load_default()
    draw.rounded_rectangle((530, 160, 750, 166), radius=3, fill="#8cfaac")
    draw.text((640, 310), "BE RIGHT BACK", font=large, fill="white", anchor="mm")
    draw.text((640, 395), "Reconnecting the stream. Stay with us.", font=small, fill="#a8b6cf", anchor="mm")
    image.save(target)


def configure_encoder():
    live = obs.call("GetStreamStatus")["outputActive"]
    if not live:
        obs.call("SetProfileParameter", parameterCategory="Output", parameterName="Mode", parameterValue="Simple")
        for name, value in {"StreamEncoder": "x264", "VBitrate": "4500", "ABitrate": "160", "Preset": "veryfast", "UseAdvanced": "true"}.items():
            obs.call("SetProfileParameter", parameterCategory="SimpleOutput", parameterName=name, parameterValue=value)
        obs.call("SetVideoSettings", baseWidth=1280, baseHeight=720, outputWidth=1280, outputHeight=720, fpsNumerator=30, fpsDenominator=1)
    scenes = {s["sceneName"] for s in obs.call("GetSceneList")["scenes"]}
    inputs = {s["inputName"] for s in obs.call("GetInputList")["inputs"]}
    for source in ("fallback", "desk", "irl"):
        scene = "Bridge · " + source
        if scene not in scenes:
            obs.call("CreateScene", sceneName=scene)
        settings = ({"file": str(DATA / "fallback.png")} if source == "fallback" else
                    {"is_local_file": False, "input": f"rtsp://relay:{quote(os.environ['INGEST_PASSWORD'], safe='')}@media:8554/{source}", "input_format": "rtsp",
                     "ffmpeg_options": "rtsp_transport=tcp", "reconnect_delay_sec": 1,
                     "restart_on_activate": False, "close_when_inactive": False, "buffering_mb": 2})
        name = "Bridge input · " + source
        if name not in inputs:
            obs.call("CreateInput", sceneName=scene, inputName=name, inputKind="image_source" if source == "fallback" else "ffmpeg_source", inputSettings=settings, sceneItemEnabled=True)
        items = obs.call("GetSceneItemList", sceneName=scene)["sceneItems"]
        item = next((x for x in items if x["sourceName"] == name), None)
        if item is None:
            item = obs.call("CreateSceneItem", sceneName=scene, sourceName=name, sceneItemEnabled=True)
        obs.call("SetSceneItemTransform", sceneName=scene, sceneItemId=item["sceneItemId"], sceneItemTransform={
            "positionX": 0, "positionY": 0, "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsWidth": 1280, "boundsHeight": 720, "boundsAlignment": 0, "alignment": 5})
    # An explicit looping silent source keeps fallback audio clocked as well as video.
    if "Bridge silence" not in inputs:
        obs.call("CreateInput", sceneName="Bridge · fallback", inputName="Bridge silence", inputKind="ffmpeg_source", inputSettings={"is_local_file": True, "local_file": str(DATA / "silence.wav"), "looping": True, "restart_on_activate": True}, sceneItemEnabled=True)
    # A failed source may already be black; cut to its backup without fading
    # through that missing picture. Resolve by kind so localization is safe.
    transitions = obs.call("GetSceneTransitionList")["transitions"]
    cut = next((t for t in transitions if t["transitionKind"] == "cut_transition"), None)
    if cut:
        obs.call("SetCurrentSceneTransition", transitionName=cut["transitionName"])
    obs.call("SetCurrentProgramScene", sceneName="Bridge · fallback")
    service = obs.call("GetStreamServiceSettings")
    state["destination"] = service.get("streamServiceSettings", {}).get("server", "")


def run():
    health = {s: FeedHealth() for s in ("desk", "irl")}
    last_start = 0
    while True:
        try:
            with lock:
                if not state["connected"]:
                    obs.connect()
                    configure_encoder()
                    state.update(connected=True, error="", active="fallback")
            try:
                response = requests.get(MEDIA + "/v3/paths/list", auth=MEDIA_AUTH, timeout=2)
                response.raise_for_status()
                paths = {p["name"]: p for p in response.json()["items"]}
            except (requests.RequestException, ValueError, KeyError):
                paths = {}
            now = time.monotonic()
            with lock:
                for source in ("desk", "irl"):
                    p = paths.get(source, {})
                    count = p.get("bytesReceived", 0)
                    media_state = obs.call("GetMediaInputStatus", inputName="Bridge input · " + source).get("mediaState", "")
                    ready = bool(p.get("ready"))
                    healthy = health[source].update(ready, count, media_state == "OBS_MEDIA_STATE_PLAYING", now)
                    state["inputs"][source] = {"ready": ready, "healthy": healthy}
                selected = choose_source(state["preferred"], state["automatic"], {s: v["healthy"] for s, v in state["inputs"].items()})
                if selected != state["active"]:
                    # Scene changes do not stop or restart the output encoder.
                    obs.call("SetCurrentProgramScene", sceneName="Bridge · " + selected)
                    state["active"] = selected
                output = obs.call("GetStreamStatus")
                state.update(streaming=output["outputActive"], reconnecting=output.get("outputReconnecting", False),
                             output_bytes=output.get("outputBytes", 0), frames=output.get("outputTotalFrames", 0),
                             dropped=output.get("outputSkippedFrames", 0))
                if state["want_live"] and not state["streaming"] and now - last_start > 10:
                    last_start = now
                    try:
                        obs.call("StartStream")
                    except RuntimeError:
                        state["error"] = "Output could not start; retrying in 10 seconds. Check the destination."
                elif state["streaming"] or not state["want_live"]:
                    state["error"] = ""
        except Exception as error:
            with lock:
                detail = str(error) if isinstance(error, OBSRequestError) else type(error).__name__
                state.update(connected=False, streaming=False, error=f"Encoder unavailable; reconnecting ({detail})")
                state["inputs"] = {s: {"healthy": False, "ready": False} for s in ("desk", "irl")}
                health = {s: FeedHealth() for s in ("desk", "irl")}
                obs.close()
        time.sleep(1)


@app.get("/")
@protected
def index():
    return send_from_directory("static", "index.html")


@app.get("/static/<path:name>")
@protected
def static(name):
    return send_from_directory("static", name)


@app.get("/api/status")
@protected
def status():
    with lock:
        result = json.loads(json.dumps(state))
    base = os.getenv("PUBLIC_RTMP_URL", "rtmp://localhost:1935").rstrip("/")
    query = urlencode({"user": "broadcaster", "pass": os.environ["INGEST_PASSWORD"]})
    result["ingest"] = {s: {"server": base, "key": s + "?" + query} for s in ("desk", "irl")}
    return jsonify(result)


@app.post("/api/route")
@protected
def route():
    data = request.get_json(silent=True) or {}
    if data.get("preferred") not in ("desk", "irl", "fallback") or not isinstance(data.get("automatic"), bool):
        return jsonify(error="Choose a source and an automatic fallback setting"), 400
    with lock:
        state.update(preferred=data["preferred"], automatic=data["automatic"])
        persist()
    return jsonify(ok=True)


@app.post("/api/output/<action>")
@protected
def output(action):
    if action not in ("start", "stop"):
        return jsonify(error="Unknown action"), 404
    data = request.get_json(silent=True) or {}
    with lock:
        if not state["connected"]:
            return jsonify(error="Encoder is not ready"), 503
        try:
            if action == "stop":
                state["want_live"] = False
                persist()
                if obs.call("GetStreamStatus")["outputActive"]:
                    obs.call("StopStream")
            else:
                if state["want_live"] or obs.call("GetStreamStatus")["outputActive"]:
                    return jsonify(error="Output is already running or retrying. Stop it before changing destination."), 409
                server, key = data.get("server", "").strip(), data.get("key", "").strip()
                parsed = urlsplit(server)
                if parsed.scheme not in ("rtmp", "rtmps") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or not key or len(server) > 2048 or len(key) > 2048 or any(c.isspace() for c in server + key):
                    return jsonify(error="Enter an RTMP/RTMPS server URL and separate stream key"), 400
                obs.call("SetStreamServiceSettings", streamServiceType="rtmp_custom", streamServiceSettings={"server": server, "key": key, "use_auth": False})
                obs.call("StartStream")
                state.update(want_live=True, destination=server)
                persist()
        except Exception:
            return jsonify(error="Encoder could not complete the request. Check connection and destination."), 502
    return jsonify(ok=True)


@app.route("/api/fallback", methods=["GET", "POST"])
@protected
def fallback():
    if request.method == "GET":
        return send_file(DATA / "fallback.png", mimetype="image/png")
    upload = request.files.get("image")
    if not upload:
        return jsonify(error="Choose a PNG or JPEG image"), 400
    try:
        image = Image.open(io.BytesIO(upload.read()))
        if image.format not in ("PNG", "JPEG") or image.width * image.height > 20_000_000:
            raise ValueError()
        image = image.convert("RGB")
        image.thumbnail((1280, 720))
        canvas = Image.new("RGB", (1280, 720), "black")
        canvas.paste(image, ((1280 - image.width) // 2, (720 - image.height) // 2))
        with lock:
            canvas.save(DATA / "fallback.tmp", format="PNG")
            (DATA / "fallback.tmp").replace(DATA / "fallback.png")
            if state["connected"]:
                obs.call("SetInputSettings", inputName="Bridge input · fallback", inputSettings={"file": str(DATA / "fallback.png")}, overlay=True)
    except (UnidentifiedImageError, ValueError, OSError, Image.DecompressionBombError):
        return jsonify(error="Use a PNG or JPEG under 8 MB and 20 megapixels"), 400
    return jsonify(ok=True)


@app.get("/preview/<source>/<path:asset>")
@protected
def preview(source, asset):
    if source not in ("desk", "irl") or not all(c.isalnum() or c in "._-" for c in asset) or ".." in asset or not asset.endswith((".m3u8", ".ts", ".mp4")):
        return "Not found", 404
    try:
        upstream = requests.get(f"{HLS}/{source}/{asset}", auth=MEDIA_AUTH, timeout=(2, 5))
        return Response(upstream.content, status=upstream.status_code, content_type=upstream.headers.get("Content-Type", "application/octet-stream"))
    except requests.RequestException:
        return "Preview unavailable", 503


fallback_image()
if not (DATA / "silence.wav").exists():
    with wave.open(str(DATA / "silence.wav"), "wb") as audio:
        audio.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x00" * 48000 * 4)
if os.getenv("RELAY_DISABLE_WORKER") != "1":
    threading.Thread(target=run, daemon=True, name="encoder-supervisor").start()
atexit.register(obs.close)
