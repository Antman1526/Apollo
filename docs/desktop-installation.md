# Apollo desktop bundles

The release workflow produces a self-contained macOS Apple-Silicon DMG and a
self-contained Windows x64 portable ZIP. Both packages include Apollo's
Python runtime, locked application dependencies, and Chromium for the
embedded browser. The workflow uploads build artifacts and SHA-256 files;
publication as a GitHub prerelease is a separate reviewed step.

## macOS

Open `Apollo-<version>.dmg`, drag `Apollo.app` to Applications, and open it.
Apollo starts its loopback server and opens the local UI. Its writable state
defaults to `~/Library/Application Support/Apollo`. Set `APOLLO_HOME` to use a
different profile, or set `APOLLO_DATA_DIR` (or the legacy `DATA_DIR`) to put
database and runtime data elsewhere. `APOLLO_PORT` changes the local port.

The build is ad hoc signed by default so it can launch on the build machine;
it is not Developer ID signed or notarized. Gatekeeper may require the user to
approve the first launch.

Builds contain no developer checkout data or personal seeds by default. A
reviewed build may opt into a non-personal seed directory with
`APOLLO_SEED_DIR`.

## Windows

Extract `Apollo-<version>-windows-x64.zip` to a folder and double-click
`Apollo\Apollo.exe`. The executable starts the bundled server and opens the
local UI. The package does not require Python, Git, llama.cpp, or a global
Playwright installation. Local GGUF serving and other optional external
runtimes remain optional and are configured in Apollo.

Windows state follows `%LOCALAPPDATA%\Apollo` by default. `APOLLO_HOME`,
`APOLLO_DATA_DIR`, and `DATA_DIR` provide the same profile and data-root
overrides as macOS. `APOLLO_PORT` changes the port. The portable executable is
unsigned, so Windows SmartScreen may show an origin warning.

## Checksums and scope

Verify the downloaded package against its matching `.sha256` file before
opening it. The build smoke starts the actual frozen executable with a fresh
temporary profile and checks `/api/health`, `/api/ready`, the root page, and a
static JavaScript module. It also places stale files in that profile to ensure
the current bundle assets win across upgrades. The frozen-child checks execute
Python with `-I -c`, import SQLite and PDF support, and verify that output and
non-zero error exits are preserved; exercise the persistent JSON-line Python
session across requests; and reject hostile `CWD`/`PYTHONPATH` imports and
invalid child arguments.

These bundles are implementation and packaging evidence. They do not certify
Developer ID signing, notarization, Windows signing, physical-device
acceptance, provider credentials, local model availability, or production
readiness.
