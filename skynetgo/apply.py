#!/usr/bin/env python3
"""Compile SkynetGO Remote's identity and server into the pinned RustDesk sources.

Reads skynetgo/config.env and rewrites five compiled-in defaults in
libs/hbb_common/src/config.rs:

  ORG, APP_NAME        the app's identity: config directory, Windows service
                       name, install path, installed exe name, tray mutex.
                       This is what lets it coexist with stock RustDesk.
  RENDEZVOUS_SERVERS   the ID server (hbbs); hbbs advertises the relay itself.
  RS_PUB_KEY           the server's public key, so the client only ever talks
                       to our server.
  DEFAULT_SETTINGS     seeds `api-server`; otherwise the client derives
                       http://<host>:21114, which our server does not expose.

Run from the repository root after `git submodule update --init --recursive`
and before build.py. Idempotent: a second run reports "already applied" and
exits 0. `--check` verifies the patched state and exits non-zero otherwise;
CI runs it right after applying, so a silent no-op cannot ship a client that
still points at rs-ny.rustdesk.com.

Why patch at build time instead of forking hbb_common: libs/hbb_common is a
git submodule, i.e. a separate repository. Patching here keeps the whole
customization in ONE fork, and the diff against upstream is this one file
plus config.env.

Every rule must match exactly once in the pristine source. An upstream
refactor that moves or renames a constant therefore fails the build loudly
rather than being skipped.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONFIG_ENV = HERE / "config.env"
TARGET = ROOT / "libs" / "hbb_common" / "src" / "config.rs"

REQUIRED = ("APP_NAME", "ORG", "RENDEZVOUS_SERVER", "RS_PUB_KEY", "API_SERVER")


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            sys.exit(f"{path}:{lineno}: expected KEY=VALUE, got {raw!r}")
        values[key.strip()] = value.strip().strip('"').strip("'")
    missing = [k for k in REQUIRED if k not in values]
    if missing:
        sys.exit(f"{path}: missing {', '.join(missing)}")
    return values


def validate(cfg: dict[str, str], allow_empty_key: bool) -> None:
    problems = []
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", cfg["APP_NAME"]):
        problems.append("APP_NAME must be letters and digits only (it becomes a path and a service name)")
    if not re.fullmatch(r"[a-z][a-z0-9.]*", cfg["ORG"]):
        problems.append("ORG must look like org.example (lowercase letters, digits, dots)")
    if not cfg["RENDEZVOUS_SERVER"] or "://" in cfg["RENDEZVOUS_SERVER"]:
        problems.append("RENDEZVOUS_SERVER must be a bare host[:port] with no scheme")
    if not cfg["API_SERVER"].startswith("https://"):
        problems.append("API_SERVER must be an https:// URL (login credentials travel over it)")
    key = cfg["RS_PUB_KEY"]
    if key:
        if not re.fullmatch(r"[A-Za-z0-9+/]{43}=", key):
            problems.append("RS_PUB_KEY must be a 44-character base64 ed25519 public key (the id_ed25519.pub contents)")
    elif not allow_empty_key:
        problems.append(
            "RS_PUB_KEY is empty. Generate the server keypair with `rustdesk-utils genkeypair` "
            "and paste the public half here (or pass --allow-empty-key for a pipeline smoke test; "
            "that client cannot connect to anything)"
        )
    if problems:
        sys.exit("skynetgo/config.env is not usable:\n  - " + "\n  - ".join(problems))


def build_rules(cfg: dict[str, str]):
    """Each rule: (name, pristine regex, patched regex, replacement).

    `pristine` must match the upstream form exactly once; `patched` must match
    our form exactly once after applying. Keeping both lets --check work and
    lets a re-run distinguish "already applied" from "upstream changed shape".
    """
    e = re.escape
    org = cfg["ORG"]
    app = cfg["APP_NAME"]
    host = cfg["RENDEZVOUS_SERVER"]
    key = cfg["RS_PUB_KEY"]
    api = cfg["API_SERVER"]
    api_entry = '("api-server".to_owned(), "' + api + '".to_owned())'
    return [
        (
            "ORG",
            re.compile(r'(pub static ref ORG: RwLock<String> = RwLock::new\(")com\.carriez("\.to_owned\(\)\);)'),
            re.compile(r'pub static ref ORG: RwLock<String> = RwLock::new\("' + e(org) + r'"\.to_owned\(\)\);'),
            lambda m: m.group(1) + org + m.group(2),
        ),
        (
            "APP_NAME",
            re.compile(r'(pub static ref APP_NAME: RwLock<String> = RwLock::new\(")RustDesk("\.to_owned\(\)\);)'),
            re.compile(r'pub static ref APP_NAME: RwLock<String> = RwLock::new\("' + e(app) + r'"\.to_owned\(\)\);'),
            lambda m: m.group(1) + app + m.group(2),
        ),
        (
            "RENDEZVOUS_SERVERS",
            re.compile(r'(pub const RENDEZVOUS_SERVERS: &\[&str\] = &\[")rs-ny\.rustdesk\.com("\];)'),
            re.compile(r'pub const RENDEZVOUS_SERVERS: &\[&str\] = &\["' + e(host) + r'"\];'),
            lambda m: m.group(1) + host + m.group(2),
        ),
        (
            "RS_PUB_KEY",
            # Upstream's value is some base64 key. The negative lookahead keeps this
            # from matching once OUR value is in place, so a re-run reads as applied.
            re.compile(r'(pub const RS_PUB_KEY: &str = ")(?!' + e(key) + r'")[A-Za-z0-9+/=]*(";)'),
            re.compile(r'pub const RS_PUB_KEY: &str = "' + e(key) + r'";'),
            lambda m: m.group(1) + key + m.group(2),
        ),
        (
            "DEFAULT_SETTINGS/api-server",
            re.compile(r'(pub static ref DEFAULT_SETTINGS: RwLock<HashMap<String, String>> = )Default::default\(\);'),
            re.compile(
                r'pub static ref DEFAULT_SETTINGS: RwLock<HashMap<String, String>> = RwLock::new\(HashMap::from\(\['
                + e(api_entry) + r'\]\)\);'
            ),
            lambda m: m.group(1) + "RwLock::new(HashMap::from([" + api_entry + "]));",
        ),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description="Bake SkynetGO Remote's config into the RustDesk sources.")
    ap.add_argument("--check", action="store_true", help="verify the patched state; change nothing")
    ap.add_argument("--allow-empty-key", action="store_true", help="permit an empty RS_PUB_KEY (pipeline smoke test)")
    args = ap.parse_args()

    if not TARGET.is_file():
        sys.exit(f"{TARGET} not found; run `git submodule update --init --recursive` first")
    cfg = load_env(CONFIG_ENV)
    # --check verifies the file matches whatever config.env says, key or no key.
    validate(cfg, allow_empty_key=args.allow_empty_key or args.check)

    # newline="" on both ends so the file's own line endings survive untouched.
    with TARGET.open(encoding="utf-8", newline="") as fh:
        text = fh.read()

    applied: list[str] = []
    already: list[str] = []
    errors: list[str] = []
    for name, pristine, patched, repl in build_rules(cfg):
        n_pristine = len(pristine.findall(text))
        n_patched = len(patched.findall(text))
        if args.check:
            if n_patched == 1:
                already.append(name)
            else:
                errors.append(f"{name}: expected the patched form exactly once, found {n_patched} "
                              f"(upstream-form matches: {n_pristine})")
            continue
        if n_patched == 1 and n_pristine == 0:
            already.append(name)
        elif n_pristine == 1:
            text = pristine.sub(repl, text, count=1)
            applied.append(name)
        else:
            errors.append(f"{name}: expected the upstream form exactly once, found {n_pristine} "
                          f"(patched-form matches: {n_patched}); upstream config.rs may have changed shape")

    if errors:
        sys.exit("skynetgo/apply.py FAILED on " + str(TARGET.relative_to(ROOT)) + ":\n  - " + "\n  - ".join(errors))

    if applied:
        with TARGET.open("w", encoding="utf-8", newline="") as fh:
            fh.write(text)

    if applied:
        print("applied:         " + ", ".join(applied))
    if already:
        print(("verified:        " if args.check else "already applied: ") + ", ".join(already))
    print(f"APP_NAME={cfg['APP_NAME']}  ORG={cfg['ORG']}  RENDEZVOUS_SERVER={cfg['RENDEZVOUS_SERVER']}  "
          f"API_SERVER={cfg['API_SERVER']}")
    print("RS_PUB_KEY=" + (cfg["RS_PUB_KEY"] or "<EMPTY: smoke-test build, this client cannot connect>"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
