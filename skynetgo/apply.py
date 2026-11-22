#!/usr/bin/env python3
"""Compile SkyDesk's identity and server into the pinned RustDesk sources.

Reads skynetgo/config.env and rewrites compiled-in defaults in three files:

  libs/hbb_common/src/config.rs   (a git submodule)
    ORG, APP_NAME        config directory, Windows service name, install path,
                         tray mutex - everything that lets SkyDesk coexist
                         with a stock RustDesk on the same machine.
    RENDEZVOUS_SERVERS   the ID server (hbbs); hbbs advertises the relay.
    RS_PUB_KEY           the server's public key, so the client only ever
                         talks to our server.
    DEFAULT_SETTINGS     seeds `api-server` (otherwise the client derives
                         http://<host>:21114, which our server does not
                         expose) plus `custom-rendezvous-server` and `key`.
                         Those two duplicate the constants above on purpose:
                         the constants are what the client USES when the
                         options are empty, but Settings > Network displays
                         the options, so without the seed the ID-server and
                         key fields read blank (as stock RustDesk does while
                         silently using rs-ny). `relay-server` is deliberately
                         NOT seeded: hbbs advertises the relay, and pinning it
                         client-side would stop clients following the server.
    OVERWRITE_SETTINGS   LOCKS the options in LOCKED_SETTINGS below (auto-update
                         off). Unlike a default,
                         an overwrite beats whatever RustDesk2.toml holds,
                         the client refuses to save a different value (and
                         deletes a stored one), and the Settings control
                         renders disabled.
    OVERWRITE_LOCAL_SETTINGS
                         the same lock for LOCKED_LOCAL_SETTINGS (per-user
                         LocalConfig options): the startup update check.

  src/common.rs
    secure_tcp           skipped, so a client signed in to the address book
                         can connect to OSS hbbs (see the rule's comment).

  flutter/windows/CMakeLists.txt
    BINARY_NAME          the BUILT exe's name. Not cosmetic: the Windows
                         installer XCOPYs the extracted directory and points
                         the service, shortcuts and UninstallString at
                         <APP_NAME>.exe without renaming anything (the
                         `{rename_exe}` step is absent from `install_me`).
                         Upstream only survives because `rustdesk.exe` and
                         `RustDesk.exe` are the same file on NTFS. A real
                         rename needs the build to emit <APP_NAME>.exe.

  flutter/windows/runner/Runner.rc
    version-resource strings, so Task Manager and the file's Properties say
    SkyDesk rather than RustDesk.

Run from the repository root after `git submodule update --init --recursive`
and before build.py. Idempotent: a second run reports "already applied" and
exits 0. `--check` verifies the patched state and exits non-zero otherwise;
CI runs it right after applying.

Every rule must match exactly once in the pristine source. An upstream
refactor that moves or renames a constant therefore fails the build loudly
rather than being skipped.

A lock only works while upstream still reads that option key the way we
assume. CONSUMERS lists read-only checks on the code that enforces each
locked option; both modes run them, and a miss fails the build, because a
renamed key would leave the overwrite in place doing nothing.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONFIG_ENV = HERE / "config.env"

TARGETS = {
    "config.rs": ROOT / "libs" / "hbb_common" / "src" / "config.rs",
    "common.rs": ROOT / "src" / "common.rs",
    "CMakeLists.txt": ROOT / "flutter" / "windows" / "CMakeLists.txt",
    "Runner.rc": ROOT / "flutter" / "windows" / "runner" / "Runner.rc",
}

REQUIRED = ("APP_NAME", "ORG", "RENDEZVOUS_SERVER", "RS_PUB_KEY", "API_SERVER")

# Security options SkyDesk compiles in as LOCKED (OVERWRITE_SETTINGS), not
# merely defaulted. These are what we tell sites and their IT reviewers the
# client does, so a user or an old RustDesk2.toml must not be able to change
# them. hbb_common config.rs: Config::get_option reads OVERWRITE_SETTINGS
# before the stored options; set_option/set_options refuse a locked key and
# purge any stored value; ui_interface.rs is_option_fixed disables the control.
#
# Deliberately NEITHER locked NOR seeded: approve-mode and verification-method.
# Each site chooses how sessions are approved, and many need UNATTENDED access:
# the installed service plus a permanent password set per machine (Settings,
# or `SkyDesk.exe --password <pw>` elevated), never compiled into the build.
# The stock defaults (approve by password or click; both password types) allow
# that. PERMANENT_PASSWORD_BREAKERS below makes check_policy refuse the values
# that would take it away, and the permanent-password:* CONSUMERS keep the
# maps that can disable it empty.
#
# Also left to each site: enable-lan-discovery (stock: on). A compiled lock
# applies at every site, and a site that wants discovery off can set it per
# machine ("Deny LAN discovery" in Settings > Security).
LOCKED_SETTINGS = (
    # Never download and install an update. Ticked, an installed build posts
    # to api.rustdesk.com daily and installs STOCK rustdesk-<ver>.exe over
    # SkyDesk (src/updater.rs). Off by default upstream; locked so it stays off.
    ("allow-auto-update", "N"),
)

# Per-user LocalConfig options, locked the same way (OVERWRITE_LOCAL_SETTINGS).
LOCKED_LOCAL_SETTINGS = (
    # Don't ask api.rustdesk.com for the latest version on every UI start
    # (src/common.rs check_software_update, called from flutter/lib/common.dart).
    # Default on. It installs nothing, but its "new version" notice points at
    # stock RustDesk, which is never the right download for a SkyDesk user.
    ("enable-check-update", "N"),
)

# Option values that stop a permanent password from working. Never lock or
# seed these: click mode never consults a password (src/server/connection.rs),
# and temporary-only rejects the permanent one (password_security.rs).
PERMANENT_PASSWORD_BREAKERS = {
    ("approve-mode", "click"),
    ("verification-method", "use-temporary-password"),
}

# Read-only checks: (label, file under ROOT, regex that must match at least
# once). Each pins the code that gives a locked option its effect, so a
# re-pin that renames a key or moves the gate fails here instead of shipping
# a lock that does nothing. Patterns use \s* where upstream wraps lines.
CONSUMERS = (
    ("overwrite-wins:Config", "libs/hbb_common/src/config.rs",
     r"get_or\(\s*&OVERWRITE_SETTINGS,\s*&CONFIG2\.read\(\)\.unwrap\(\)\.options,"),
    ("overwrite-refuses-save:Config", "libs/hbb_common/src/config.rs",
     r"pub fn set_option\(k: String, v: String\) \{\s*if !is_option_can_save\(&OVERWRITE_SETTINGS, &k,"),
    ("overwrite-wins:LocalConfig", "libs/hbb_common/src/config.rs",
     r"get_or\(\s*&OVERWRITE_LOCAL_SETTINGS,"),
    ("allow-auto-update:key", "libs/hbb_common/src/config.rs",
     r'pub const OPTION_ALLOW_AUTO_UPDATE: &str = "allow-auto-update";'),
    ("allow-auto-update:gate", "src/updater.rs",
     r"if !\(manually \|\| config::Config::get_bool_option\(config::keys::OPTION_ALLOW_AUTO_UPDATE\)\)"),
    ("enable-check-update:key", "libs/hbb_common/src/config.rs",
     r'pub const OPTION_ENABLE_CHECK_UPDATE: &str = "enable-check-update";'),
    ("enable-check-update:gate", "src/common.rs",
     r"LocalConfig::get_option\(keys::OPTION_ENABLE_CHECK_UPDATE\)"),
    # Permanent password stays settable and usable. With verification-method
    # unset, permanent_enabled() is true. BUILTIN_SETTINGS holds
    # disable-change-permanent-password (Settings button, Config::
    # set_permanent_password, the --password CLI); HARD_SETTINGS holds
    # disable-settings (blocks --password) and a preset password. Both must
    # stay empty in the PATCHED file, so no future rule can fill them.
    ("permanent-password:enabled-by-default", "libs/hbb_common/src/password_security.rs",
     r"pub fn permanent_enabled\(\) -> bool \{\s*verification_method\(\) != VerificationMethod::OnlyUseTemporaryPassword"),
    ("permanent-password:builtin-settings-empty", "libs/hbb_common/src/config.rs",
     r"pub static ref BUILTIN_SETTINGS: RwLock<HashMap<String, String>> = Default::default\(\);"),
    ("permanent-password:hard-settings-empty", "libs/hbb_common/src/config.rs",
     r"pub static ref HARD_SETTINGS: RwLock<HashMap<String, String>> = Default::default\(\);"),
)


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
        problems.append("APP_NAME must be letters and digits only (it becomes a path, a service name and an exe name)")
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


def seeded_settings(cfg: dict[str, str]) -> tuple[tuple[str, str], ...]:
    """DEFAULT_SETTINGS seeds: user-overridable, unlike LOCKED_SETTINGS."""
    return (
        ("api-server", cfg["API_SERVER"]),
        ("custom-rendezvous-server", cfg["RENDEZVOUS_SERVER"]),
        ("key", cfg["RS_PUB_KEY"]),
    )


def hashmap_entries(pairs) -> str:
    """The body of a Rust `HashMap::from([...])` literal of String pairs."""
    return ", ".join('("' + k + '".to_owned(), "' + v + '".to_owned())' for k, v in pairs)


def hashmap_rule(name: str, static: str, pairs):
    """Rule that fills an empty `pub static ref <static>` map in config.rs."""
    e = re.escape
    entries = hashmap_entries(pairs)
    decl = "pub static ref " + static + ": RwLock<HashMap<String, String>> = "
    return (
        "config.rs", name,
        re.compile("(" + e(decl) + r")Default::default\(\);"),
        re.compile(e(decl) + r"RwLock::new\(HashMap::from\(\[" + e(entries) + r"\]\)\);"),
        lambda m: m.group(1) + "RwLock::new(HashMap::from([" + entries + "]));",
    )


def check_policy(cfg: dict[str, str]) -> None:
    """Refuse seed/lock combinations that contradict each other or break permanent passwords."""
    seeded_pairs = set(seeded_settings(cfg))
    locked_pairs = set(LOCKED_SETTINGS + LOCKED_LOCAL_SETTINGS)
    both = sorted({k for k, _ in seeded_pairs} & {k for k, _ in locked_pairs})
    if both:
        # One value displayed, another enforced.
        sys.exit("skynetgo/apply.py: keys both seeded (DEFAULT_SETTINGS) and locked: " + ", ".join(both))
    breakers = sorted(f"{k}={v}" for k, v in (seeded_pairs | locked_pairs) & PERMANENT_PASSWORD_BREAKERS)
    if breakers:
        sys.exit("skynetgo/apply.py: " + ", ".join(breakers) + " would stop permanent passwords from working; "
                 "sites need them for unattended access (see LOCKED_SETTINGS)")


def check_consumers(texts: dict[str, str]) -> tuple[list[str], list[str]]:
    """Run CONSUMERS plus the no-manual-update-caller check; return (ok, errors)."""
    ok: list[str] = []
    errors: list[str] = []
    # Files apply.py also patches are read from memory (patched or not, the
    # consumer code is untouched); the rest straight from disk.
    by_path = {TARGETS[label]: text for label, text in texts.items()}
    for label, rel, pattern in CONSUMERS:
        path = ROOT / rel
        if not path.is_file():
            errors.append(f"consumer {label}: {rel} not found")
            continue
        text = by_path[path] if path in by_path else read(path)
        if re.search(pattern, text):
            ok.append(label)
        elif label.startswith("permanent-password:"):
            errors.append(f"consumer {label}: pattern not found in {rel}; a permanent password may no "
                          f"longer be settable or usable, which unattended sites depend on")
        else:
            errors.append(f"consumer {label}: pattern not found in {rel}; upstream changed how this "
                          f"locked option is read, so the lock may no longer do anything")
    # updater.rs has a `manually` path that skips allow-auto-update. Upstream
    # never calls it; make sure that stays true.
    hits = [p.relative_to(ROOT).as_posix() for p in (ROOT / "src").rglob("*.rs")
            if "manually_check_update" in read(p)]
    if hits == ["src/updater.rs"] and read(ROOT / "src" / "updater.rs").count("manually_check_update") == 1:
        ok.append("allow-auto-update:no-manual-caller")
    else:
        errors.append("consumer allow-auto-update:no-manual-caller: updater::manually_check_update is now "
                      f"referenced outside its definition ({', '.join(hits)}); that path installs updates "
                      "regardless of allow-auto-update")
    return ok, errors


def build_rules(cfg: dict[str, str]):
    """Each rule: (target, name, pristine regex, patched regex, replacement).

    `pristine` must match the upstream form exactly once; `patched` must match
    our form exactly once after applying. Keeping both lets --check work and
    lets a re-run distinguish "already applied" from "upstream changed shape".
    """
    e = re.escape
    org = cfg["ORG"]
    app = cfg["APP_NAME"]
    host = cfg["RENDEZVOUS_SERVER"]
    key = cfg["RS_PUB_KEY"]
    desc = app + " Remote Desktop"
    return [
        # --- libs/hbb_common/src/config.rs -------------------------------------
        (
            "config.rs", "ORG",
            re.compile(r'(pub static ref ORG: RwLock<String> = RwLock::new\(")com\.carriez("\.to_owned\(\)\);)'),
            re.compile(r'pub static ref ORG: RwLock<String> = RwLock::new\("' + e(org) + r'"\.to_owned\(\)\);'),
            lambda m: m.group(1) + org + m.group(2),
        ),
        (
            "config.rs", "APP_NAME",
            re.compile(r'(pub static ref APP_NAME: RwLock<String> = RwLock::new\(")RustDesk("\.to_owned\(\)\);)'),
            re.compile(r'pub static ref APP_NAME: RwLock<String> = RwLock::new\("' + e(app) + r'"\.to_owned\(\)\);'),
            lambda m: m.group(1) + app + m.group(2),
        ),
        (
            "config.rs", "RENDEZVOUS_SERVERS",
            re.compile(r'(pub const RENDEZVOUS_SERVERS: &\[&str\] = &\[")rs-ny\.rustdesk\.com("\];)'),
            re.compile(r'pub const RENDEZVOUS_SERVERS: &\[&str\] = &\["' + e(host) + r'"\];'),
            lambda m: m.group(1) + host + m.group(2),
        ),
        (
            "config.rs", "RS_PUB_KEY",
            # Upstream's value is some base64 key. The negative lookahead keeps this
            # from matching once OUR value is in place, so a re-run reads as applied.
            re.compile(r'(pub const RS_PUB_KEY: &str = ")(?!' + e(key) + r'")[A-Za-z0-9+/=]*(";)'),
            re.compile(r'pub const RS_PUB_KEY: &str = "' + e(key) + r'";'),
            lambda m: m.group(1) + key + m.group(2),
        ),
        hashmap_rule("DEFAULT_SETTINGS/seeded-options", "DEFAULT_SETTINGS", seeded_settings(cfg)),
        hashmap_rule("OVERWRITE_SETTINGS/locked-options", "OVERWRITE_SETTINGS", LOCKED_SETTINGS),
        *([hashmap_rule("OVERWRITE_LOCAL_SETTINGS/locked-options", "OVERWRITE_LOCAL_SETTINGS",
                        LOCKED_LOCAL_SETTINGS)] if LOCKED_LOCAL_SETTINGS else []),
        # --- flutter/windows/CMakeLists.txt ------------------------------------
        (
            "CMakeLists.txt", "BINARY_NAME",
            re.compile(r'^(set\(BINARY_NAME ")rustdesk("\))', re.M),
            re.compile(r'^set\(BINARY_NAME "' + e(app) + r'"\)', re.M),
            lambda m: m.group(1) + app + m.group(2),
        ),
        # --- flutter/windows/runner/Runner.rc ----------------------------------
        (
            "Runner.rc", "FileDescription",
            re.compile(r'(VALUE "FileDescription", ")RustDesk Remote Desktop(" "\\0")'),
            re.compile(r'VALUE "FileDescription", "' + e(desc) + r'" "\\0"'),
            lambda m: m.group(1) + desc + m.group(2),
        ),
        (
            "Runner.rc", "InternalName",
            re.compile(r'(VALUE "InternalName", ")rustdesk(" "\\0")'),
            re.compile(r'VALUE "InternalName", "' + e(app) + r'" "\\0"'),
            lambda m: m.group(1) + app + m.group(2),
        ),
        (
            "Runner.rc", "OriginalFilename",
            re.compile(r'(VALUE "OriginalFilename", ")rustdesk\.exe(" "\\0")'),
            re.compile(r'VALUE "OriginalFilename", "' + e(app) + r'\.exe" "\\0"'),
            lambda m: m.group(1) + app + ".exe" + m.group(2),
        ),
        (
            "Runner.rc", "ProductName",
            re.compile(r'(VALUE "ProductName", ")RustDesk(" "\\0")'),
            re.compile(r'VALUE "ProductName", "' + e(app) + r'" "\\0"'),
            lambda m: m.group(1) + app + m.group(2),
        ),
        # --- src/common.rs (main repo) -----------------------------------------
        (
            "common.rs", "secure_tcp-noop",
            re.compile(re.escape("    if use_ws() {")),
            re.compile(re.escape("    if true || use_ws() {")),
            lambda m: "    if true || use_ws() {  // SkyDesk: OSS hbbs never answers secure_tcp's KeyExchange; a logged-in client hangs here until timeout. Force-skip. Logged-out never calls it; peer session stays end-to-end encrypted.",
        ),
    ]


def read(path: Path) -> str:
    # newline="" on both ends so each file's own line endings survive untouched.
    with path.open(encoding="utf-8", newline="") as fh:
        return fh.read()


def write(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def main() -> int:
    ap = argparse.ArgumentParser(description="Bake SkyDesk's config into the RustDesk sources.")
    ap.add_argument("--check", action="store_true", help="verify the patched state; change nothing")
    ap.add_argument("--allow-empty-key", action="store_true", help="permit an empty RS_PUB_KEY (pipeline smoke test)")
    args = ap.parse_args()

    for label, path in TARGETS.items():
        if not path.is_file():
            hint = " - run `git submodule update --init --recursive` first" if label == "config.rs" else ""
            sys.exit(f"{path} not found{hint}")
    cfg = load_env(CONFIG_ENV)
    # --check verifies the files match whatever config.env says, key or no key.
    validate(cfg, allow_empty_key=args.allow_empty_key or args.check)
    check_policy(cfg)

    texts = {label: read(path) for label, path in TARGETS.items()}
    changed: set[str] = set()
    applied: list[str] = []
    already: list[str] = []
    errors: list[str] = []
    for target, name, pristine, patched, repl in build_rules(cfg):
        text = texts[target]
        n_pristine = len(pristine.findall(text))
        n_patched = len(patched.findall(text))
        label = f"{target}:{name}"
        if args.check:
            if n_patched == 1:
                already.append(label)
            else:
                errors.append(f"{label}: expected the patched form exactly once, found {n_patched} "
                              f"(upstream-form matches: {n_pristine})")
            continue
        if n_patched == 1 and n_pristine == 0:
            already.append(label)
        elif n_pristine == 1:
            texts[target] = pristine.sub(repl, text, count=1)
            changed.add(target)
            applied.append(label)
        else:
            errors.append(f"{label}: expected the upstream form exactly once, found {n_pristine} "
                          f"(patched-form matches: {n_patched}); upstream may have changed shape")

    consumers_ok, consumer_errors = check_consumers(texts)
    errors.extend(consumer_errors)

    if errors:
        sys.exit("skynetgo/apply.py FAILED:\n  - " + "\n  - ".join(errors))

    for target in changed:
        write(TARGETS[target], texts[target])

    if applied:
        print("applied:         " + ", ".join(applied))
    if already:
        print(("verified:        " if args.check else "already applied: ") + ", ".join(already))
    print("consumers ok:    " + ", ".join(consumers_ok))
    print(f"APP_NAME={cfg['APP_NAME']}  ORG={cfg['ORG']}  RENDEZVOUS_SERVER={cfg['RENDEZVOUS_SERVER']}  "
          f"API_SERVER={cfg['API_SERVER']}")
    print("LOCKED: " + "  ".join(f"{k}={v}" for k, v in LOCKED_SETTINGS + LOCKED_LOCAL_SETTINGS))
    print("RS_PUB_KEY=" + (cfg["RS_PUB_KEY"] or "<EMPTY: smoke-test build, this client cannot connect>"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
