# Monthly security and features review

This is for me, once a month. Budget an hour. Work top to bottom, fix or file what turns up, and log anything that changed in `changes.md`.

## 1. Backend tools and their defaults

For each tool, check its release notes since last month and ask: are my flags still the recommended ones, and has anything been deprecated?

**cryptsetup** (`backends/cryptsetup.py`)
- Is LUKS2 still the default type? Is Argon2id still the recommended PBKDF?
- Is `aes-xts-plain64` with a 512-bit key still the default cipher? If upstream moved to something else, find out why before following.
- Are my floors sane? 1 s unlock time and 256 MiB Argon2 memory. cryptsetup's own default is 2 s and 1 GiB. If upstream raised theirs, raise mine.
- Did luksAddKey, luksChangeKey or luksRemoveKey change how they take key files? The tests cover format/addkey/dump/backup, run them as root.
- Does `--sector-size 4096` still work on every device type? Some old USB bridges lie about their sector size.

**age** (`backends/age_gpg.py`)
- Is age still the right default? It's simple, it's maintained, and passphrase mode uses scrypt. If there's a credible attack on age's format or scrypt parameters, switch the default to gpg.
- Did the passphrase prompt text change? The runner answers prompts matching `passphrase ... :`. If age changes its wording, encryption hangs waiting for input. The age round-trip test catches this.
- Newer age versions have a batch passphrase plugin. If it reads from an fd, it's cleaner than the pty. Worth switching once it's in every distro's age package.

**gpg**
- Still `--symmetric --cipher-algo AES256 --s2k-mode 3 --s2k-digest-algo SHA512 --s2k-count 65011712`? Check whether GnuPG now prefers AEAD (OCB) by default and whether it's safe to turn on given who might decrypt the file.
- `--no-symkey-cache` still keeps the passphrase out of gpg-agent?

**mat2 / exiftool** (`backends/mat2_exif.py`)
- Is mat2 still maintained? Check its repo for new supported formats and known gaps.
- Still writing `name.cleaned.ext`? The replace-original logic depends on that name.

**coreutils hashes** (`backends/hashes.py`)
- SHA-256 is fine. Has anything happened to BLAKE2b? Anyone shipping SHA3 sums that I should add?

**Disk Destroyer tools** (`backends/wipe.py`)
- nvme-cli: is `nvme format --ses=1 --force` still the syntax? Has `--force` been renamed? Should Wipe prefer `nvme sanitize` when the controller supports it?
- hdparm: still `--user-master u --security-set-pass` then `--security-erase(-enhanced)`? Any change in how frozen state is reported?
- blkdiscard: still `-f` to skip its own checks (I do my own)?
- shred: still exits non-zero on write errors so Destroy mode can tell a dying drive from a finished pass?

**sfill / dd** (`backends/freespace.py`)
- Is secure-delete still packaged on Debian/Ubuntu? If it goes away, the dd fallback is enough, but update the README.

## 2. Secret handling path

Walk it end to end and check nothing has crept in that holds a passphrase as a str or writes it anywhere.

- `ui/widgets.py` SecretEdit: keystrokes go straight into SecretBuffer. Only the strength meter builds a full str (and a bytes copy). Is that still the only place?
- `core/secrets.py`: buffers are fixed capacity (never resize, so never move), mlocked, zeroed on clear and destroy.
- `core/runner.py` and `core/escalation.py`: secrets written to pipes or the pty and then destroyed. grep for `.decode(` and `str(` near secrets.
- Nothing secret on argv. `grep -rn "argv" keelhaul/backends` and check every secret is an `@FD` placeholder.
- The log: `grep -rn "logbook.record" keelhaul` and confirm none of them can include a secret. The hdparm temporary password is masked in `log_argv`.
- Startup: `harden_process()` still runs before QApplication, and still sets RLIMIT_CORE 0 and PR_SET_DUMPABLE 0.

## 3. Escalation surface

- `core/root_helper.py` ALLOW list: still only cryptsetup, nvme, hdparm, blkdiscard, shred, sfill, dd, sync. Anything added must earn it.
- Root resolves tools with its own SAFE_PATH, never the user's.
- `chown_after` only hands a file back to the uid pkexec/sudo reported. `remove_after` only deletes `.keelhaul-fill.*` files.
- pkexec exit codes (126 dismissed, 127 not authorized) are still what pkexec returns. Polkit changes these rarely, but check.
- The askpass wrapper is created in a fresh 0700 dir under XDG_RUNTIME_DIR and removed afterward.

## 4. Destructive gates

Open the Disk Destroyer and Drive Crypto tabs on a machine with a spare USB stick and try to break them:

- Can I arm a drive without typing its exact name? Without the override on a mounted drive?
- Unplug the stick after confirming and before running. It must refuse with "is gone" or "changed since you confirmed it".
- Plug two sticks and swap them between confirm and run. The by-id path must keep it from wiping the wrong one.
- Does Format refuse without both passphrases matching? Does Restore header go through the full gate?

## 5. Watchdog

- Start a Destroy run on a stick with throttling on and a low CPU ceiling. The status bar should show it pausing and resuming.
- Check the full-speed checkbox really skips nice/ionice and the watchdog.
- Confirm Drive Crypto jobs still run with throttle off, so KDF benchmarks are not slowed.

## 6. Build and probe checklist

- [ ] CI is green on the default branch
- [ ] Build AppImage runs by hand and produces `Keelhaul-x86_64.AppImage`
- [ ] The AppImage starts on my machine and the Backends panel finds cryptsetup, age, gpg, mat2, exiftool, sha256sum, b2sum, nvme, hdparm, blkdiscard, lsblk, shred, dd, sfill, pkexec, python3
- [ ] One Drive Crypto operation and one Disk Destroyer Wipe on a USB stick worked from the AppImage (pkexec prompt appeared once each)
- [ ] File Vault round-trips a folder with age and a file with gpg
- [ ] Hash Check verifies a real distro SHA256SUMS
- [ ] Anything changed got an entry in `changes.md`
