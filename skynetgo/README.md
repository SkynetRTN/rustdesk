# SkyDesk

The SkynetGO project's build of the RustDesk client, with the Skynet server,
its public key, the address-book API, and a distinct app identity compiled in.
Installs as `SkyDesk`, keeps its own config and address book, runs its own
service, and coexists with a stock RustDesk on the same machine. It sits in the
`SkyLib` / `SkyNode` family of tools.

Everything SkyDesk-specific lives in this directory plus one workflow:

| Path | Role |
|---|---|
| `skynetgo/config.env` | the five values that get compiled in (all public) |
| `skynetgo/apply.py` | patches three upstream files at build time; `--check` verifies |
| `.github/workflows/skynetgo-windows.yml` | Windows x64 build, trimmed from upstream's `flutter-build.yml` |

Branch layout: `master` mirrors upstream untouched. `skynetgo` is based on an
upstream release tag and carries only the files above. Re-pinning is a rebase.
(The directory and branch keep the project name, `skynetgo`; the product is
`SkyDesk`.)

## Why a rebuild is required

RustDesk can rename itself at runtime, but only from a config blob signed by
RustDesk's own key (`src/common.rs`, `read_custom_client`) - the Pro client
generator. The `rustdesk-host=…,key=….exe` filename trick sets the server but
not the app name, so config still lands in `%APPDATA%\RustDesk` and collides
with a stock install. A separate app therefore means changing the compiled-in
defaults. On Windows the service name, registry keys, install path, installed
exe name, and tray mutex all derive from `get_app_name()`
(`src/platform/windows.rs`), so one constant flows through everything.

`libs/hbb_common` is a git submodule (a separate repository). Rather than fork
it too, `apply.py` patches it at build time and asserts every edit landed.

## What gets patched, and why the exe name is not cosmetic

`apply.py` edits three files:

- `libs/hbb_common/src/config.rs` - `ORG`, `APP_NAME`, `RENDEZVOUS_SERVERS`,
  `RS_PUB_KEY`, and three seeded options in `DEFAULT_SETTINGS`: `api-server`,
  `custom-rendezvous-server`, `key`.
- `flutter/windows/CMakeLists.txt` - `BINARY_NAME`, so the build emits
  `SkyDesk.exe`.
- `flutter/windows/runner/Runner.rc` - the version-resource strings, so Task
  Manager and the file's Properties say SkyDesk.

The `BINARY_NAME` edit is load-bearing. The Windows installer (`install_me` in
`src/platform/windows.rs`) XCOPYs the extracted directory into
`C:\Program Files\<APP_NAME>\` and then points the service (`sc create`), both
shortcuts, and the `UninstallString` at `<APP_NAME>.exe` - but it never renames
the copied exe; the `{rename_exe}` step exists only in a different install
path. Upstream gets away with this because `rustdesk.exe` and `RustDesk.exe`
are the same file on a case-insensitive filesystem. With a real app name the
first build shipped `rustdesk.exe` next to a shortcut for `SkynetGORemote.exe`
and a service that could not start. Emitting `<APP_NAME>.exe` from the
compiler makes the install path identical to upstream's.

### Constants vs. options

The rendezvous host and key are compiled in twice, deliberately. The
**constants** (`RENDEZVOUS_SERVERS`, `RS_PUB_KEY`) are what the client uses
when the corresponding options are empty - `get_rendezvous_servers()` and the
key lookup fall through to them. The **options** seeded into
`DEFAULT_SETTINGS` are what Settings → Network displays and what a user can
override. Without the seed the client connects correctly but the ID-server
and key fields read blank, exactly as stock RustDesk does while silently
using `rs-ny.rustdesk.com`. Seeding both layers means the UI tells the
truth. `relay-server` is not seeded: hbbs advertises the relay (`-r`), so
clients follow the server if it ever moves.

## One-time setup: the server keypair

The client pins the server's public key. Generate the pair once, on any host
with the server binaries:

```bash
rustdesk-utils genkeypair
```

Store both halves in the skynet repo's shared prod vault,
`inventory/group_vars/prod/vault.yml` (`vault_rustdesk_private_key` /
`vault_rustdesk_public_key`; the Ansible role deploys them to the VM). Paste
the **public** half into `config.env` as `RS_PUB_KEY`. Never regenerate it:
every deployed client would stop connecting.

## Building

Push to `skynetgo`, or run the **SkyDesk (Windows x64)** workflow from the
Actions tab. The fork is public, so GitHub-hosted Windows runners are free and
unmetered; the self-hosted Linux runner is not involved (Flutter Windows
desktop cannot cross-compile).

Artifacts:

- `SkyDesk-<version>-x64` - the single-file installer. Running it extracts to
  a temp directory and launches `SkyDesk.exe`; **Install** inside the client
  copies the directory to `C:\Program Files\SkyDesk\` and registers the
  `SkyDesk` service.
- `SkyDesk-<version>-x64-unpacked` - the raw build folder.

A cold build takes about 45 minutes; vcpkg and Rust caches make later runs
faster.

The `allow_empty_key` workflow input builds without a public key so the
pipeline can be proven before the keypair exists. That client cannot connect
to anything; it is for the pipeline, not for people.

## Verifying a build

Before distributing, on a machine that also has stock RustDesk:

1. Install it. Confirm `C:\Program Files\SkyDesk\SkyDesk.exe` exists (the exe
   must carry the app name - see above), a service named `SkyDesk` is
   running, and stock RustDesk's install is untouched.
2. Open **Settings → Network**. ID server, key, and API server should
   already read `remote.skynetgo.org`, the compiled-in key, and
   `https://remote.skynetgo.org` with nothing typed. Relay server is blank
   by design (see *Constants vs. options*).
3. Confirm stock RustDesk still launches and still reaches its own server
   while SkyDesk is running.

## Removing a broken earlier install

The first build installed as `SkynetGORemote` with the exe left as
`rustdesk.exe`, so its own uninstaller cannot find itself. From an elevated
PowerShell, give it the file it expects and then let it clean up:

```powershell
Copy-Item "C:\Program Files\SkynetGORemote\rustdesk.exe" "C:\Program Files\SkynetGORemote\SkynetGORemote.exe"
```

```powershell
& "C:\Program Files\SkynetGORemote\SkynetGORemote.exe" --uninstall
```

If the service still lingers: `sc.exe delete SkynetGORemote`.

## Re-pinning to a newer upstream release

```bash
git fetch upstream --tags --no-recurse-submodules
git rebase --onto <tag> <old-tag> skynetgo
git submodule update --init --recursive
python3 skynetgo/apply.py && python3 skynetgo/apply.py --check
```

`--no-recurse-submodules` matters: a plain `--tags` fetch follows submodule
pointers on demand and aborts on the first upstream ref whose `hbb_common`
commit is unreachable. If `apply.py` reports that a rule matched zero or
several times, upstream moved a constant - fix the regex in `build_rules`
rather than the source.

Then re-derive `env:` and the step list in `skynetgo-windows.yml` from the new
tag's `flutter-build.yml` (`build-for-windows-flutter`, x86_64 entry). Update
`VERSION` in the workflow; it names the artifact.

Both ends - the laptop and the telescope control computers - run this same
build, so there is no client-version drift to manage. Choose the tag on the
basis of what actually connects to the server you run.

## Deliberately not customized (yet)

- **Code signing.** Unsigned builds trip SmartScreen ("More info → Run
  anyway"). Acceptable for staff distribution; revisit before wider release.
- **MSI package** and the **virtual printer driver** upstream bundles. Neither
  is a telescope-control feature; both add failure surface.
- **Direct IP access port.** Per-install setting in a separate config dir, so
  two apps only contend if both enable it. Server-mediated sessions never use
  it.
- **Icon.** Still RustDesk's. The window title already follows `APP_NAME` at
  runtime (`main.cpp` asks the core for it).

## License

RustDesk is AGPL-3.0. Distributing this build means distributing its source,
which this public fork does.
