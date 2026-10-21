"""Run a bounded synthetic-feed test on the Twitch output already started in the UI.

Never reads or changes the outgoing stream key. Stops the test output on completion.
"""
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

PROJECT = Path(__file__).resolve().parents[1]
RESULTS = PROJECT / 'test-results'
RESULTS.mkdir(exist_ok=True)
PROGRESS = RESULTS / 'twitch-progress.json'
REPORT = RESULTS / 'twitch-results.json'
COMPOSE = ['docker', 'compose', '-f', 'compose.yaml', '-f', 'compose.test.yaml']
values = dict(line.split('=', 1) for line in (PROJECT / '.env').read_text().splitlines()
              if '=' in line and not line.lstrip().startswith('#'))
AUTH = 'Basic ' + base64.b64encode(('admin:' + values['ADMIN_PASSWORD']).encode()).decode()
started = time.monotonic()
report = {'started_at': datetime.now(timezone.utc).isoformat(), 'stages': [], 'samples': [], 'passed': False,
          'output_stopped': False, 'cleanup_errors': []}
stage = 'Preflight'
last_bytes = None
initial = None
owns_test = False


def api(path='/api/status', data=None):
    headers = {'Authorization': AUTH}
    if data is not None:
        headers.update({'Content-Type': 'application/json', 'X-Relay-Request': '1'})
    request = Request('http://127.0.0.1:8080' + path, headers=headers,
                      data=None if data is None else json.dumps(data).encode())
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def save(status=None, message=''):
    payload = {'stage': stage, 'elapsed_seconds': round(time.monotonic() - started), 'message': message,
               'updated_at': datetime.now(timezone.utc).isoformat()}
    if status:
        payload['status'] = {k: status[k] for k in ('connected', 'streaming', 'reconnecting', 'active', 'preferred', 'output_bytes', 'frames', 'dropped', 'inputs')}
    temp = PROGRESS.with_suffix('.tmp')
    temp.write_text(json.dumps(payload, indent=2) + '\n')
    temp.chmod(0o600)
    temp.replace(PROGRESS)


def continuity():
    global last_bytes
    status = api()
    if not status['connected'] or not status['streaming'] or status['reconnecting']:
        raise RuntimeError('The encoder/output was disconnected during the test')
    if last_bytes is not None and status['output_bytes'] < last_bytes:
        raise RuntimeError('The output byte counter reset during the test')
    last_bytes = status['output_bytes']
    report['samples'].append({'elapsed_seconds': round(time.monotonic() - started), 'stage': stage,
                              **{k: status[k] for k in ('active', 'output_bytes', 'frames', 'dropped')}})
    save(status)
    return status


def command(*args):
    result = subprocess.run(COMPOSE + list(args), cwd=PROJECT, capture_output=True, timeout=50)
    if result.returncode:
        # Compose/FFmpeg output can include input credentials; do not echo it.
        raise RuntimeError('Docker test-feed operation failed (exit ' + str(result.returncode) + ')')


def wait_for(predicate, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = continuity()
        if predicate(status):
            return status
        time.sleep(2)
    raise RuntimeError('Timed out while waiting for ' + stage)


def show(name, expected, seconds):
    global stage
    stage = name
    print(name + ' — ' + str(seconds) + ' seconds', flush=True)
    wait_for(lambda s: s['active'] == expected)
    report['stages'].append({'name': name, 'expected_source': expected, 'elapsed_seconds': round(time.monotonic() - started)})
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        status = continuity()
        if status['active'] != expected:
            raise RuntimeError('Unexpected source change during ' + name)
        time.sleep(2)


def select(source):
    api('/api/route', {'preferred': source, 'automatic': True})


try:
    initial = api()
    host = (urlsplit(initial.get('destination', '')).hostname or '').lower()
    if not (host.endswith('.twitch.tv') or host.endswith('.live-video.net')):
        raise RuntimeError('Start the intended Twitch test output in Streambridge first')
    if not initial['streaming'] or not initial['connected']:
        raise RuntimeError('The Twitch output must already be live before this test starts')
    if any(s['ready'] for s in initial['inputs'].values()):
        raise RuntimeError('Existing publishers detected; stop them before running synthetic tests')
    owns_test = True
    select('desk')
    show('Baseline: fallback on Twitch', 'fallback', 40)
    stage = 'Starting the desk and IRL test feeds'
    save(message='Waiting for both feeds to become healthy')
    command('up', '-d', 'test-desk', 'test-irl')
    wait_for(lambda s: all(v['healthy'] for v in s['inputs'].values()))
    show('Desk: RED picture with 440 Hz tone', 'desk', 45)
    select('irl')
    show('IRL: BLUE picture with 880 Hz tone', 'irl', 45)
    select('desk')
    show('Desk before simulated disconnection: RED', 'desk', 25)
    stage = 'Stopping the desk feed'
    command('stop', 'test-desk')
    show('Automatic failover to IRL: BLUE', 'irl', 45)
    stage = 'Stopping the IRL feed'
    command('stop', 'test-irl')
    show('Both feeds lost: fallback graphic', 'fallback', 50)
    stage = 'Reconnecting both test feeds'
    command('up', '-d', 'test-desk', 'test-irl')
    wait_for(lambda s: all(v['healthy'] for v in s['inputs'].values()))
    show('Preferred desk recovered: RED', 'desk', 45)
    select('fallback')
    show('Forced fallback with both inputs healthy', 'fallback', 25)
    report['passed'] = True
except Exception as error:
    report['error'] = str(error) if isinstance(error, RuntimeError) else type(error).__name__
    print('Test stopped:', report['error'], flush=True)
finally:
    if owns_test:
        stage = 'Stopping test broadcast'
        try:
            api('/api/output/stop', {})
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if not api()['streaming']:
                    report['output_stopped'] = True
                    break
                time.sleep(1)
            if not report['output_stopped']:
                report['cleanup_errors'].append('Output stop was not confirmed; check Streambridge')
        except Exception:
            report['cleanup_errors'].append('Could not stop output; use Stop broadcast in Streambridge')
        try:
            command('stop', 'test-desk', 'test-irl')
        except Exception:
            report['cleanup_errors'].append('Could not stop synthetic publishers')
        try:
            api('/api/route', {'preferred': initial['preferred'], 'automatic': initial['automatic']})
        except Exception:
            report['cleanup_errors'].append('Could not restore source preference')
    stage = 'Finished'
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    REPORT.write_text(json.dumps(report, indent=2) + '\n')
    REPORT.chmod(0o600)
    save(message='Backend test passed' if report['passed'] else report.get('error', 'Test did not complete'))
    print('Finished. Results saved locally. Tell ChatGPT the test has finished.', flush=True)
