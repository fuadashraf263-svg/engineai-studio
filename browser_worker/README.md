# EngineAI Browser Worker

This is a real, fresh-profile Chromium worker for local EngineAI use. It is
bound to `127.0.0.1:8105`, requires `confirm_external_send: true` for each
navigation or action, and records its last 100 activity events.

It deliberately cannot automate passwords or login controls, use the normal
browser profile, access saved credentials, or accept downloads. Web content is
unverified until it is retained as a cited project source and checked.

Run its launch self-test:

```bash
cd ~/engineai/browser_worker
$HOME/.local/opt/node/bin/node server.mjs --selftest
```

Start all EngineAI local services:

```bash
cd ~/engineai
./start_local_services.sh
```
