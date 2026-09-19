# SkynetGO Remote

A build of the RustDesk client with the Skynet server, its public key, the
address-book API, and a distinct app identity compiled in. Installs as
`SkynetGORemote`, keeps its own config and address book, runs its own service,
and coexists with a stock RustDesk on the same machine.

Everything SkynetGO-specific lives in this directory plus one workflow:

| Path | Role |
|---|---|
| `skynetgo/config.env` | the five values that get compiled in (all public) |
| `skynetgo/apply.py` | patches `libs/hbb_common/src/config.rs` at build time; `--check` verifies |
| `.github/workflows/skynetgo-windows.yml` | Windows x64 build, trimmed from upstream's `flutter-build.yml` |

Branch layout: `master` mirrors upstream untouched. `skynetgo` is based on an
upstream release tag and carries only the files above. Re-pinning is a rebase.

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

## One-time setup: the server keypair

The client pins the server's public key. Generate the pair once, on any host
with the server binaries:

```bash
rustdesk-utils genkeypair
```

Store both halves in the skynet repo's `inventory/group_vars/prod_remote`
vault (`vault_rustdesk_private_key` / `vault_rustdesk_public_key`; the Ansible
role deploys them to the VM). Paste the **public** half into `config.env` as
`RS_PUB_KEY`. Never regenerate it: every deployed client would stop connecting.

## Building

Push to `skynetgo`, or run the **SkynetGO Remote (Windows x64)** workflow from
the Actions tab. The fork is public, so GitHub-hosted Windows runners are free
and unmetered; the self-hosted Linux runner is not involved (Flutter Windows
desktop cannot cross-compile).

Artifacts:

- `SkynetGORemote-<version>-x64` - the single-file installer. Running it
  extracts to a temp directory and launches the client; **Install** inside the
  client copies it to `C:\Program Files\SkynetGORemote\SkynetGORemote.exe` and
  registers the `SkynetGORemote` service.
- `SkynetGORemote-<version>-x64-unpacked` - the raw build folder.

Expect the first run to take well over an hour; vcpkg and Rust caches make
later runs much faster.

The `allow_empty_key` workflow input builds without a public key so the
pipeline can be proven before the keypair exists. That client cannot connect
to anything; it is for the pipeline, not for people.

## Verifying a build

Before distributing, on a machine that also has stock RustDesk:

1. Install it. Confirm `C:\Program Files\SkynetGORemote\` and a service named
   `SkynetGORemote` exist, and that stock RustDesk's install is untouched.
2. Open **Settings → Network**. The ID server and key should already read
   `remote.skynetgo.org` and the compiled-in key with nothing typed.
3. Confirm stock RustDesk still launches and still reaches its own server
   while SkynetGO Remote is running.

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

- **Window title and the raw build's exe name** still say `rustdesk`
  (`flutter/windows/CMakeLists.txt` `BINARY_NAME`, and the Flutter runner).
  Cosmetic: the *installed* exe is named from `APP_NAME`. Changing
  `BINARY_NAME` also changes paths the build steps reference by name, so it is
  a deliberate follow-up rather than part of the first build.
- **Code signing.** Unsigned builds trip SmartScreen ("More info → Run
  anyway"). Acceptable for staff distribution; revisit before wider release.
- **MSI package** and the **virtual printer driver** upstream bundles. Neither
  is a telescope-control feature; both add failure surface.
- **Direct IP access port.** Per-install setting in a separate config dir, so
  two apps only contend if both enable it. Server-mediated sessions never use
  it.

## License

RustDesk is AGPL-3.0. Distributing this build means distributing its source,
which this public fork does.
