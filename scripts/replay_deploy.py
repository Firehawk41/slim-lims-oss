"""Replays a Render deploy on a database that has lived (CI's `deploy` job; the outage of 2026-11-17).

    replay_deploy.py build CHECKOUT --python PY           # its render.yaml build command, in PY's venv
    replay_deploy.py serve CHECKOUT --python PY --port P  # its render.yaml start command, until /health
    replay_deploy.py visit URL --jars DIR --visitors N    # N visitors each claim a lab with a persona
    replay_deploy.py revisit URL --jars DIR               # after the deploy: their labs answer, a new one too
    replay_deploy.py stop                                 # stops what `serve` started

`serve` runs the start command exactly as render.yaml has it, with its environment values; only the database,
the secret key and the port are this machine's. Stdlib only: the old checkout's packages don't matter here.
"""

import argparse
import http.cookiejar
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PIDFILE = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "replay_deploy.pid"
PAGES = ["/", "/demo/personas"]  # what every visitor can open, whichever persona they chose


def _command(text: str, key: str) -> str:
    found = re.search(rf"{key}: >-\n((?:      .*\n)+)", text)
    if found is None:
        sys.exit(f"render.yaml: no {key}")
    return " ".join(line.strip() for line in found.group(1).splitlines())


def render_service(checkout: Path, key: str = "startCommand") -> tuple[str, dict[str, str]]:
    """The web service's command and plain env values, read without a YAML library."""
    text = (checkout / "render.yaml").read_text()
    env = dict(re.findall(r"- key: (\w+)\n\s+value: \"?([^\"\n]*)\"?", text))
    return _command(text, key), env


def build(args) -> None:
    checkout = Path(args.checkout).resolve()
    command, _env = render_service(checkout, "buildCommand")
    print(f"build {checkout.name}: {command}", flush=True)
    path = f"{Path(args.python).parent}{os.pathsep}{os.environ['PATH']}"
    subprocess.run(["bash", "-c", command], cwd=checkout, env={**os.environ, "PATH": path}, check=True)


def serve(args) -> None:
    checkout = Path(args.checkout).resolve()
    command, env = render_service(checkout)
    env.pop("PYTHON_VERSION", None)
    env.update(
        APP_ENV="development",  # plain http here: production's cookies are Secure
        TRUSTED_PROXY_HOPS="0",
        DATABASE_URL=os.environ["DATABASE_URL"],
        SECRET_KEY=os.environ["SECRET_KEY"],  # the same across the deploy, as Render keeps it
        PORT=str(args.port),
        PYTHONUNBUFFERED="1",  # the log is read when something fails
        PATH=f"{Path(args.python).parent}{os.pathsep}{os.environ['PATH']}",
    )
    print(f"serve {checkout.name}: {command}", flush=True)
    log = open(Path(os.environ.get("RUNNER_TEMP", "/tmp")) / f"serve-{checkout.name}.log", "w")
    proc = subprocess.Popen(
        ["bash", "-c", command],
        cwd=checkout,
        env={**os.environ, **env},
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,  # `stop` ends the whole group: bash and the server it started
    )
    PIDFILE.write_text(str(proc.pid))
    url = f"http://127.0.0.1:{args.port}/health"
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            log.close()
            print(Path(log.name).read_text()[-6000:])
            sys.exit(f"the start command exited ({proc.returncode}) before /health answered")
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status == 200:
                    print(f"/health 200 after {args.timeout - (deadline - time.monotonic()):.0f}s")
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(2)
    print(Path(log.name).read_text()[-6000:])
    sys.exit(f"/health didn't answer in {args.timeout}s")


def stop(_args) -> None:
    if not PIDFILE.exists():
        return
    try:
        os.killpg(int(PIDFILE.read_text()), signal.SIGTERM)
        time.sleep(3)
    except ProcessLookupError:
        pass
    PIDFILE.unlink()


def _opener(jar):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), NoRedirect)


def _get(opener, url) -> tuple[int, str]:
    try:
        with opener.open(url, timeout=60) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")


def _claim(base: str, jar, n: int, retries: int = 0) -> str:
    for attempt in range(retries + 1):
        try:
            return _claim_once(base, jar, n)
        except SystemExit as failed:
            if attempt == retries:
                raise
            print(f"{failed} (retrying in 20s)", flush=True)
            time.sleep(20)
    raise AssertionError("unreachable")


def _claim_once(base: str, jar, n: int) -> str:
    opener = _opener(jar)
    status, page = _get(opener, base + "/demo/personas")
    if status != 200:
        sys.exit(f"/demo/personas: {status}")
    forms = re.findall(
        r'name="csrf_token" value="([^"]+)">\s*<input type="hidden" name="user_id" value="([^"]+)"', page
    )
    if not forms:
        sys.exit("/demo/personas: no persona to choose")
    csrf, user_id = forms[n % len(forms)]
    body = urllib.parse.urlencode({"csrf_token": csrf, "user_id": user_id}).encode()
    try:
        opener.open(urllib.request.Request(base + "/demo/continue-as", data=body), timeout=120)
        sys.exit("continue-as: no redirect")
    except urllib.error.HTTPError as e:
        if e.code != 303:
            sys.exit(f"continue-as: {e.code} {e.read().decode(errors='replace')[:500]}")
    return user_id


def _check(base: str, jar, who: str) -> None:
    opener = _opener(jar)
    for page in PAGES:
        status, body = _get(opener, base + page)
        if status != 200:
            sys.exit(f"{who} {page}: {status}\n{body[:1500]}")
        if "<h1>Something went wrong" in body:
            sys.exit(f"{who} {page}: an error page")
    print(f"{who}: {', '.join(PAGES)} 200")


def visit(args) -> None:
    jars = Path(args.jars)
    jars.mkdir(parents=True, exist_ok=True)
    for n in range(args.visitors):
        jar = http.cookiejar.MozillaCookieJar(jars / f"visitor{n}.txt")
        _claim(args.url, jar, n, args.retries)  # the old deploy's own faults aren't under test
        _check(args.url, jar, f"visitor {n}")
        jar.save(ignore_discard=True, ignore_expires=True)


def revisit(args) -> None:
    jars = sorted(Path(args.jars).glob("visitor*.txt"))
    if not jars:
        sys.exit("no visitors to revisit")
    for path in jars:
        jar = http.cookiejar.MozillaCookieJar(path)
        jar.load(ignore_discard=True, ignore_expires=True)
        _check(args.url, jar, f"{path.stem} (lab claimed before the deploy)")
    jar = http.cookiejar.CookieJar()
    _claim(args.url, jar, len(jars))
    _check(args.url, jar, "a new visitor")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(required=True)
    s = sub.add_parser("build")
    s.add_argument("checkout")
    s.add_argument("--python", required=True, help="the checkout's venv python")
    s.set_defaults(fn=build)
    s = sub.add_parser("serve")
    s.add_argument("checkout")
    s.add_argument("--python", required=True, help="the checkout's venv python")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--timeout", type=int, default=600)
    s.set_defaults(fn=serve)
    s = sub.add_parser("visit")
    s.add_argument("url")
    s.add_argument("--jars", required=True)
    s.add_argument("--visitors", type=int, default=3)
    s.add_argument("--retries", type=int, default=0)
    s.set_defaults(fn=visit)
    s = sub.add_parser("revisit")
    s.add_argument("url")
    s.add_argument("--jars", required=True)
    s.set_defaults(fn=revisit)
    sub.add_parser("stop").set_defaults(fn=stop)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
