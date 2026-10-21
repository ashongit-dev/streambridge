#!/bin/sh
set -eu
mkdir -p /data/.config/obs-studio/plugin_config/obs-websocket /data/.config/obs-studio/basic/profiles/Relay
cat > /data/.config/obs-studio/plugin_config/obs-websocket/config.json <<'JSON'
{"server_enabled":true,"server_port":4455,"auth_required":false,"alerts_enabled":false,"first_load":false}
JSON
cat > /data/.config/obs-studio/global.ini <<'INI'
[General]
FirstRun=true
[Basic]
Profile=Relay
ProfileDir=Relay
SceneCollection=Relay
SceneCollectionFile=Relay

# OBS 30 / obs-websocket 5.3 reads this section in global.ini.
# The JSON above also supports plugin versions using separate config files.
[OBSWebSocket]
FirstLoad=false
ServerEnabled=true
ServerPort=4455
AlertsEnabled=false
AuthRequired=false
INI
# Prevent OBS logs, which can contain destination details, from going to container logs.
Xvfb :99 -screen 0 1280x720x24 -nolisten tcp >/dev/null 2>&1 &
XVFB_PID=$!
python3 - <<'PYWAIT'
import os, time
for _ in range(100):
    if os.path.exists('/tmp/.X11-unix/X99'):
        break
    time.sleep(0.1)
else:
    raise SystemExit('Virtual display did not start')
PYWAIT
obs --allow-opengl --disable-shutdown-check --disable-missing-files-check --profile Relay --collection Relay --websocket_port 4455 >/data/obs.log 2>&1 &
OBS_PID=$!
gunicorn --workers 1 --threads 8 --bind 0.0.0.0:8080 --timeout 30 'app.server:app' &
WEB_PID=$!
trap 'kill "$WEB_PID" "$OBS_PID" "$XVFB_PID" 2>/dev/null || true' EXIT INT TERM
# Restart the container if any essential process exits.
while kill -0 "$OBS_PID" 2>/dev/null && kill -0 "$WEB_PID" 2>/dev/null && kill -0 "$XVFB_PID" 2>/dev/null; do sleep 2; done
exit 1
