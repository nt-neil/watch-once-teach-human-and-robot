#!/usr/bin/env python3
"""End-to-end HTTP test for the WatchOnce web UI (stdlib only).

Runs the full chain against a live server: home -> POST /analyze (mock) ->
poll /api/job -> /result -> /export. For the recording machine (real bind).

Usage:
  python3 e2e-http-test.py [--base http://127.0.0.1:18766]
      [--example synthetic] [--timeout 300] [--out e2e-result.json]

Exit 0 = all checks pass. Mock mode only: spends $0.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append({"name": name, "ok": bool(ok), "detail": str(detail)[:200]})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""),
          flush=True)
    return bool(ok)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def http_error_302(self, req, fp, code, msg, headers):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)

    http_error_301 = http_error_303 = http_error_307 = http_error_302


def opener():
    return urllib.request.build_opener(NoRedirect)


def get(op, url, timeout=20):
    try:
        with op.open(url, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def build_multipart(fields):
    bound = "E2EBOUNDARY7d3f"
    body = b""
    for k, v in fields.items():
        body += (f"--{bound}\r\nContent-Disposition: form-data; "
                 f'name="{k}"\r\n\r\n{v}\r\n').encode()
    body += f"--{bound}--\r\n".encode()
    return body, f"multipart/form-data; boundary={bound}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:18766")
    ap.add_argument("--example", default="synthetic")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--out", default="e2e-result.json")
    args = ap.parse_args()
    base = args.base.rstrip("/")
    op = opener()
    ok = True

    s, home = get(op, base + "/")
    ok &= check("GET / returns 200", s == 200, f"status={s}")
    ok &= check("home shows product title", b"Watch Once" in home)

    s, braw = get(op, base + "/api/budget")
    try:
        b0 = json.loads(braw.decode())
    except ValueError:
        b0 = {}
    ok &= check("GET /api/budget readable", s == 200 and "paid_calls" in b0, f"status={s}")

    body, ctype = build_multipart({"provider": "mock", "example": args.example})
    req = urllib.request.Request(base + "/analyze", data=body,
                                 headers={"Content-Type": ctype}, method="POST")
    job_id = ""
    try:
        with op.open(req, timeout=30) as r:
            loc, st = "", r.status
    except urllib.error.HTTPError as e:
        loc, st = e.headers.get("Location", ""), e.code
    if st == 303 and loc.startswith("/job/"):
        job_id = loc[len("/job/"):]
    ok &= check("POST /analyze (mock) -> 303 job redirect", bool(job_id),
                f"status={st} loc={loc}")
    if not job_id:
        return finish(args, ok, {})

    run_id, stages, state, err = "", [], "", ""
    t0 = time.time()
    while time.time() - t0 < args.timeout:
        s, jraw = get(op, base + f"/api/job/{job_id}")
        if s != 200:
            break
        try:
            j = json.loads(jraw.decode())
        except ValueError:
            break
        state, run_id, err = j.get("state", ""), j.get("run_id", ""), j.get("error", "")
        if j.get("stage") and (not stages or stages[-1] != j["stage"]):
            stages.append(j["stage"])
        if state in ("done", "failed"):
            break
        time.sleep(2)
    print(f"stages seen: {' > '.join(stages) or '(none)'}", flush=True)
    ok &= check("job reaches done", state == "done", f"state={state} err={err}")
    ok &= check("job yields run_id", bool(run_id), f"run_id={run_id}")
    if state != "done" or not run_id:
        return finish(args, ok, {"job_id": job_id, "stages": stages})

    s, res = get(op, base + f"/result/{run_id}")
    ok &= check("GET /result/<run> returns 200", s == 200, f"status={s}")
    for marker in (b"Human manual", b"Robot", b'class="tl"', b"Provenance",
                   b"Full manual", b"stepjson-1"):
        ok &= check(f"result page has {marker.decode()}", marker in res)
    me = os.path.basename(os.path.expanduser("~")).encode().lower()
    ok &= check("result page leaks no /Users/ path", b"/Users/" not in res)
    ok &= check("result page leaks no username", me not in res.lower())

    s, exp = get(op, base + f"/export/{run_id}/vla")
    try:
        expj = json.loads(exp.decode())
    except ValueError:
        expj = None
    ok &= check("GET /export/<run>/vla returns JSON", s == 200 and expj is not None,
                f"status={s}")

    s, fnt = get(op, base + "/static/fonts/plex-sans-400.woff2")
    ok &= check("offline font served", s == 200 and fnt[:4] == b"wOF2", f"status={s}")

    s, braw = get(op, base + "/api/budget")
    try:
        b1 = json.loads(braw.decode())
    except ValueError:
        b1 = {}
    same = (b0.get("paid_calls") == b1.get("paid_calls")
            and b0.get("spend_usd") == b1.get("spend_usd"))
    ok &= check("mock run spends $0", same, f"before={b0} after={b1}")

    return finish(args, ok, {"job_id": job_id, "run_id": run_id, "stages": stages})


def finish(args, ok, extra):
    report = {"ok": bool(ok), "base": args.base, "example": args.example,
              "checks": CHECKS, **extra}
    try:
        with open(args.out, "w") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
        print(f"report -> {args.out}", flush=True)
    except OSError as e:
        print(f"cannot write {args.out}: {e}", flush=True)
    n_ok = sum(1 for c in CHECKS if c["ok"])
    print(f"E2E {'PASS' if ok else 'FAIL'} ({n_ok}/{len(CHECKS)})", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
