# Changes

2026.11.05 - Build fixes

* CI import smoke test now skips keelhaul.__main__, which launched the full app and sat in its event loop until GitHub cancelled the job after two hours
* keelhaul/__main__.py now only starts the app when run directly, so importing it can never launch the GUI
* Hard time limits: 20 minutes on the CI job, 30 on the AppImage job, plus per-step timeouts on the smoke test, the tests and the AppImage import check
* AppImage workflow sets the executable bit itself on entrypoint.sh, checks AppRun is executable after extraction, and chmods the bundled python wrapper before using it, since web UI uploads drop file modes

Reasons for changes: the first CI run hung for two hours on the smoke test, and scripts uploaded through the GitHub web UI lose their executable bit.

2026.11.04 - Initial build

* Six tabs from the build spec: Drive Crypto (cryptsetup LUKS2 with header backup/restore), File Vault (age, gpg fallback), Metadata Scrub (mat2, exiftool fallback), Hash Check (sha256sum, sha512sum, b2sum), Disk Destroyer (Wipe and Destroy modes), Free-Space Wipe (sfill, dd fallback)
* Secrets typed straight into locked, fixed-size, zeroable buffers through a custom passphrase field, handed to backends over pipes (cryptsetup, gpg) or a pseudo-terminal (age), never on argv
* Whole-process mlockall when the memlock limit allows, per-buffer mlock otherwise, with a startup warning only when disk swap is actually active (zram is not counted)
* Core dumps disabled and the process marked non-dumpable, so a crash or a same-user ptrace cannot read a passphrase out of memory
* Root helper passed to the system python3 on argv under pkexec, with sudo -A fallback and a built-in askpass prompt; allowlist of programs that may run as root; one authentication per operation
* Child environment stripped of AppImage paths and library overrides, and every tool resolved from the system PATH, so system binaries never load bundled libraries
* Destructive gate: model, serial and size shown back, typed device name to arm, second typed override for mounted or system drives, re-check of serial and size right before the first write, writes through /dev/disk/by-id
* Disk Destroyer: NVMe format with user data erase, ATA Secure Erase with frozen-state detection and temporary-password recovery, blkdiscard, shred fallback, multi-drive with per-drive progress, optional plain-text wipe report
* Free-space wipe warns on SSD, copy-on-write and journaled filesystems, offers root to cover reserved blocks, and always removes its fill file
* Folder encryption streams tar into the encryptor so no plaintext tar touches disk
* "Remove original" overwrites then deletes on spinning disks, plain delete with a warning on SSD and COW filesystems
* Resource watchdog with per-process-group CPU and RSS sampling, SIGSTOP/SIGCONT pacing, nice/ionice by default, full-speed opt-in for Disk Destroyer; KDF benchmarks are never throttled
* Themes: Corroded Copper (default), Normal dark, Normal light, Verdigris, Kawaii Light, Kawaii Dark, with the danger color fixed across all of them
* GitHub Actions: CI (ruff, import and window smoke test, pytest) and AppImage build on tag via python-appimage using the newest Python base image

Reasons for changes: first build from the Keelhaul build spec, with the gaps the spec left open filled in (AppImage root access, drive renumbering between confirm and write, ATA password recovery, AppImage library leakage into system tools).
