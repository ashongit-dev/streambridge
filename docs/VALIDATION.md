# Validation notes

## Verified implementation

Passed 21 core, configuration and API tests. The local Docker Desktop acceptance run also passed:

- Building the image and connecting to headless OBS.
- Accepting configured API credentials and rejecting anonymous/incorrect ones.
- Receiving H.264 video and AAC audio at a private RTMP receiver.
- Receiving fallback video/audio continuously for at least 60 seconds with no publishers.
- Receiving the correct desk/IRL picture after manual switches and automatic failover.
- Fetching both authenticated HLS playlists and MPEG-TS preview segments.
- Switching to fallback when both publishers stop, recovering the preferred feed, using fallback with automatic backup disabled, and preserving forced fallback when both publishers return.
- Keeping the same receiver RTMP publishing connection through every source change. Output byte counters did not reset and the controller reported no reconnects at monitored checkpoints.
- Stopping the test output and publishers cleanly, while leaving the main application running.

Received video was decoded into 45-frame sequences; the final 15 frames had to consistently match the expected synthetic source. Scene changes use direct cuts to avoid fading through a failed input's blank picture. Full results are included in `../validation/acceptance-results.json`.

This verifies local synthetic source switching and continuity. Real camera/phone inputs, cellular stalls, custom graphic delivery, long-duration stability, and output/server crash recovery still require testing in the intended environment. Browser preview playback and Twitch delivery were subsequently checked in the live test below.

### Live Twitch test

A six-minute test on the operator-provided Twitch test account passed all eight stages: baseline fallback, red desk, blue IRL, return to desk, automatic failover after desk loss, fallback after both feeds stopped, recovery to desk, and forced fallback with both inputs healthy.

The Twitch player was visually inspected at each stage. It displayed the expected picture at 1280×720; playback time and channel uptime continued without resetting. Both Streambridge browser previews were also observed playing. All 183 backend monitoring samples passed the continuity checks, with no encoder reconnects or byte-counter resets and zero reported encoder skipped frames. The output and test publishers stopped cleanly at the end, and Streambridge and Twitch both showed the output offline.

The outgoing stream key was entered by the operator in the dashboard and was not read or included in the test report. The Twitch player remained muted during visual checks, so listening quality was not assessed. This was a synthetic-feed test, not a real cellular IRL test. Results and browser observations are in `../validation/twitch-results.json` and `../validation/twitch-browser-observations.json`.

To repeat that bounded Twitch test, first enter the intended test channel's destination and key in the dashboard and start the output. Then run:

```sh
python3 tools/twitch_test.py
```

This publicly broadcasts the synthetic feed sequence to the already-configured Twitch test destination and stops the broadcast automatically at completion. It never reads or changes the outgoing stream key. Do not run it during a real broadcast.

To repeat the local acceptance test, with no real broadcast or input publishers running:

```sh
python3 tools/acceptance.py
```

It renders the private configuration, rebuilds the image, runs the unit/API tests, creates a private receiver and two synthetic publishers, and saves results under `test-results/`. It does not use Twitch. The tests replace the outgoing destination with the private test receiver; enter your real output URL/key again afterward. Docker access is required in the terminal running this command. The test receiver publishes no host ports.

To collect local diagnostics:

```sh
python3 tools/diagnose.py
```

