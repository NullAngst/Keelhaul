# Keelhaul

Keelhaul is a desktop app for Linux that puts cryptsetup, age, gpg, mat2, exiftool, coreutils, nvme-cli, hdparm and secure-delete behind one window with secure defaults already picked, for people who want what those tools do without memorizing their flags.

It has no crypto or wiping code of its own. Every operation calls the real tool, so you get the audited binary your distro ships, not a reimplementation.

Six tabs:

1. **Drive Crypto**: LUKS2 format, open, close, add/change/remove key slots, header info, header backup and restore (cryptsetup)
2. **File Vault**: encrypt or decrypt a file or a whole folder with a passphrase (age, gpg fallback)
3. **Metadata Scrub**: strip metadata from one file, a folder, or a folder tree (mat2, exiftool fallback)
4. **Hash Check**: drop a file, get its hash, paste the expected one, get MATCH or NO MATCH (sha256sum, sha512sum, b2sum)
5. **Disk Destroyer**: wipe one or many drives so the data is gone, or write to them until they die (nvme-cli, hdparm, blkdiscard, shred)
6. **Free-Space Wipe**: overwrite only the free space on a mounted filesystem (sfill, dd fallback)

## Prerequisites

- Linux on x86_64. The AppImage is amd64 only for now.
- `pkexec` (polkit) with a running authentication agent, or `sudo`. KDE Plasma, GNOME, Xfce and the rest all run a polkit agent already.
- The system `python3`. The small root helper runs under it, since root can't read inside an AppImage.
- The backend tools for the tabs you want. Missing ones only disable or degrade their tab, and the **Backends** button in the toolbar tells you exactly what's missing and the install command for your distro.

I'm on Kubuntu, so in my case this gets everything:

```
sudo apt install cryptsetup age gnupg mat2 libimage-exiftool-perl nvme-cli hdparm secure-delete pkexec python3
```

On other systems the package names shift a little:

| Family | Command |
|---|---|
| Fedora | `sudo dnf install cryptsetup age gnupg2 mat2 perl-Image-ExifTool nvme-cli hdparm polkit python3` |
| openSUSE | `sudo zypper install cryptsetup age gpg2 mat2 exiftool nvme-cli hdparm polkit python3` |
| Arch | `sudo pacman -S cryptsetup age gnupg mat2 perl-image-exiftool nvme-cli hdparm polkit python`, plus `secure-delete` from the AUR |
| Alpine | `sudo apk add cryptsetup age gnupg mat2 exiftool nvme-cli hdparm polkit python3` |
| Gentoo | `sudo emerge --ask sys-fs/cryptsetup app-crypt/age app-crypt/gnupg app-misc/mat2 media-libs/exiftool sys-apps/nvme-cli sys-apps/hdparm sys-auth/polkit` |

coreutils (sha256sum, b2sum, shred, dd) and util-linux (lsblk, blkdiscard) are on every system already. secure-delete isn't packaged everywhere; without it the Free-Space Wipe tab falls back to a dd fill file, which works fine.

## Install and run

1. Download `Keelhaul-x86_64.AppImage` from the latest release on this repo's Releases page.
2. Make it executable: `chmod +x Keelhaul-x86_64.AppImage`
3. Run it as your normal user: `./Keelhaul-x86_64.AppImage`, since Keelhaul asks for root one operation at a time and never runs the GUI as root.
4. Click **Backends** in the top right and check every tab you care about says ready.

To run from source instead:

1. Clone the repo and `cd` into it.
2. Install the Python side: `pip install --user -r requirements.txt` (or use a venv, or whichever way you manage Python packages).
3. Run it: `python3 -m keelhaul`

## Using it

### Drive Crypto

Pick a device, pick an operation, fill in what it asks for. Format uses LUKS2, aes-xts-plain64, a 512-bit key (AES-256 in XTS), 4096-byte sectors and Argon2id. Open the Advanced box only if you need a different cipher or KDF cost. The KDF never gets throttled, because a slowed benchmark would quietly pick weaker parameters.

Open only unlocks the container to `/dev/mapper/<name>`. Mount the filesystem inside with your file manager or `mount`.

Back up the header. Seriously. A corrupted LUKS header means the data is gone even with the right passphrase, and the backup is a couple of megabytes. Keep it somewhere as safe as the drive, since it can unlock the container with any passphrase that was valid when you made it.

### File Vault

Drop a file or folder in, pick a passphrase, hit Encrypt. Folders are streamed through tar straight into the encryptor, so no plaintext tar ever lands on disk. Decrypt figures out by itself whether a file is age or gpg.

"Remove the original" overwrites then deletes on a spinning disk. On an SSD or btrfs/ZFS it can only delete, and the app tells you that before it does it.

### Metadata Scrub

Pick a target and scope, press **Preview** to see every file it'll touch, then **Scrub**. Cleaned copies land next to the originals as `name.cleaned.ext`. Turn on "Replace originals" and each original is removed only after its cleaned copy is confirmed written.

### Hash Check

Drop a file anywhere on the tab. Paste the expected hash and the verdict shows up in green or copper. Drop a `SHA256SUMS` file (like the one next to a distro ISO) and it checks every entry you have the file for.

### Disk Destroyer

**THIS DELETES EVERYTHING ON THE DRIVES YOU PICK. THERE IS NO UNDO. CHECK THE MODEL AND SERIAL AGAINST THE LABEL ON THE DRIVE BEFORE YOU TYPE ITS NAME.**

Tick the drives, pick a mode (there's no default on purpose), then **Arm and run**. You type each drive's name to arm it. A mounted or system drive needs a second typed override. Right before anything is written, Keelhaul re-checks that each drive still has the serial and size you confirmed, and it writes through the `/dev/disk/by-id` path, so a drive that got swapped or renumbered in the meantime is refused instead of wiped.

- **Wipe** uses the drive's own erase: `nvme format` with user data erase on NVMe, ATA Secure Erase through hdparm on SATA SSDs, then `blkdiscard`, then an overwrite as the last resort. HDDs and USB sticks get a random overwrite. The drive still works afterward.
- **Destroy** runs `shred` random passes over the whole device, again and again, until it stops accepting writes or disappears, or you press Stop.

A SATA SSD that's "frozen" (most BIOSes freeze it at boot) can't take an ATA Secure Erase. Suspend and resume the machine or hot-plug the drive, then run it again. If an ATA erase ever fails halfway, the temporary drive password is in the log and in the wipe report so you can unlock it with `hdparm --security-disable`.

When it's done, **Save wipe report** writes a plain-text record of what was done to which serial number, which is handy if you're selling or recycling the drives.

### Free-Space Wipe

Pick a mounted filesystem and run it. It fills the free space with random data, then removes the fill. It's honest about where this doesn't work: on SSDs, btrfs/ZFS and journaled metadata, old data can survive, and the tab says so. Full-disk encryption from day one is the real fix for those.

## Memory and swap

Passphrases live in locked, fixed-size buffers that get zeroed after use, and they're handed to backends through pipes or a pseudo-terminal, never on the command line. If your memlock limit is big enough, Keelhaul locks its whole process into RAM. Most distros default to 8 MiB, which is only enough for the secret buffers themselves. If you have disk swap on, Keelhaul warns you at startup, since other copies (the strength meter's, for one) could in theory be swapped out.

To close that gap, pick one: turn swap off while you work (`sudo swapoff -a`), use zram instead of a swap partition, or raise the limit by adding `yourname - memlock unlimited` to `/etc/security/limits.conf` and logging back in. zram counts as RAM, so Keelhaul doesn't warn about it.

## Settings

The toolbar has Settings, About, a Theme menu and the Backends indicator. Themes: Corroded Copper (default), Normal dark, Normal light, Verdigris, Kawaii Light and Kawaii Dark. Destructive buttons are the same copper in every theme, so they always look dangerous.

The watchdog pauses Keelhaul's own jobs (SIGSTOP, then SIGCONT a few seconds later) if they keep the machine pegged past the ceilings in Settings. It paces work and never changes what a command does. Disk Destroyer has a full-speed checkbox for when killing a drive fast is the point.

The operational log is off by default. When on, it writes commands, exit codes and times to `~/.local/state/keelhaul/keelhaul.log`, never passphrases or file contents.

## Building the AppImage

Push a tag and GitHub Actions builds `Keelhaul-x86_64.AppImage` with python-appimage and attaches it to the release. Run the **Build AppImage** workflow by hand from the Actions tab to get one without a release.

Now Keelhaul is installed, finding your system's tools, and every one of those tools is running with the safe flags already set. Tinker with the themes and watchdog ceilings as you see fit.

## License

GPL-3.0. See `LICENSE`.
