#!/usr/bin/env python3
"""
studio_doctor.py - test the RUNNING studio from the command line.

When the browser shows nothing useful, this says which layer is broken:
the port, auth, the answer stream, the file list, or the download.

  python studio_doctor.py                    test http://127.0.0.1:8099
  python studio_doctor.py --token <token>    if auth is on
  python studio_doctor.py --port 8099
"""
import os, sys, json, time, argparse, urllib.request, urllib.error

def get(url, tok=None, timeout=20):
    req = urllib.request.Request(url)
    if tok: req.add_header("x-engineai-token", tok)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return 0, str(e).encode()

def post(url, body, tok=None, timeout=180):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    if tok: req.add_header("x-engineai-token", tok)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return 0, str(e).encode()

def main():
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--port", type=int, default=int(os.environ.get("ENGINEAI_PORT", 8099)))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--token", default=os.environ.get("ENGINEAI_TOKEN", ""))
    a = ap.parse_args()
    base = f"http://{a.host}:{a.port}"
    tok = a.token or None
    print(f"\n  testing {base}" + ("  (with token)" if tok else "  (no token)"))
    print("  " + "-"*64)
    fail = []

    # 1 is anything listening
    code, body = get(base + "/api/health", tok)
    if code == 0:
        print(f"  1 SERVER      NOT RUNNING — {body.decode()[:60]}")
        print("\n  start it:  cd ~/engineai && python studio.py\n"); sys.exit(1)
    if code == 401:
        print("  1 SERVER      running, but AUTH IS ON and this request has no valid token")
        print("\n  Either restart without auth:")
        print("     pkill -f studio.py && cd ~/engineai && python studio.py")
        print("  or pass a token:")
        print("     python owner.py token")
        print(f"     python studio_doctor.py --token <the token>")
        print(f"     ...and in the browser: {base}/?token=<the token>\n")
        sys.exit(1)
    if code != 200:
        print(f"  1 SERVER      responded {code}: {body[:80].decode(errors='ignore')}")
        fail.append("health"); 
    else:
        h = json.loads(body)
        print(f"  1 SERVER      ok — {len(h.get('tools',[]))} tools, backend "
              f"{h.get('backend')}, out {h.get('out')}")

    # 2 the page
    code, body = get(base + "/", tok)
    ok = code == 200 and b"Studio" in body
    print(f"  2 PAGE        {'ok' if ok else f'FAILED ({code})'}")
    if not ok: fail.append("page")

    # 3 a deterministic question - no model, no key needed
    t0 = time.time()
    code, body = post(base + "/api/ask",
                      {"text": "tip deflection of a 1 m steel cantilever under 1 kN"}, tok)
    txt = body.decode(errors="ignore")
    events = [json.loads(l[6:]) for l in txt.split("\n\n") if l.startswith("data: ")]
    kinds = [e.get("kind") for e in events]
    if code != 200:
        print(f"  3 ASK         FAILED ({code}) {txt[:90]}"); fail.append("ask")
    elif "answer" in kinds:
        ans = next(e for e in events if e["kind"] == "answer")
        print(f"  3 ASK         ok — {len([k for k in kinds if k=='step'])} steps in "
              f"{time.time()-t0:.1f}s, answer: {str(ans.get('answer'))[:40]}")
    elif "error" in kinds:
        err = next(e for e in events if e["kind"] == "error")
        print(f"  3 ASK         returned an error: {str(err.get('message'))[:80]}")
        fail.append("ask")
    else:
        print(f"  3 ASK         no answer. events: {kinds[:6]}"); fail.append("ask")

    # 4 the file list
    code, body = get(base + "/browse", tok)
    n = body.count(b"<tr>") if code == 200 else 0
    print(f"  4 FILE LIST   {'ok — ' + str(n) + ' file(s)' if code==200 else f'FAILED ({code})'}")
    if code != 200: fail.append("browse")

    # 5 download one of them
    import re as _re
    m = _re.findall(rb'href="/files/([^"?]+)', body) if code == 200 else []
    if m:
        name = m[0].decode()
        code, blob = get(f"{base}/files/{name}?dl=1", tok)
        print(f"  5 DOWNLOAD    {'ok — ' + name + f' ({len(blob)/1024:.0f} KB)' if code==200 and len(blob)>10 else f'FAILED ({code})'}")
        if code != 200: fail.append("download")
    else:
        print("  5 DOWNLOAD    nothing generated yet — ask a question first")

    print("  " + "-"*64)
    if not fail:
        print(f"  everything works. Open {base} in your browser."
              + (f"/?token=..." if tok else "") + "\n")
    else:
        print(f"  broken: {', '.join(fail)}\n")
    sys.exit(0 if not fail else 1)

if __name__ == "__main__":
    main()
