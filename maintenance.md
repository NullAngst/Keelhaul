# Maintenance

Run through this every couple of months. It covers the build and the moving parts outside the repo. The security review (are the flags still right, is age still the right pick) lives in `Review_Security_And_Features.md` and runs monthly.

## 1. Check the builds still go green

1. Open the repo's Actions tab and look at the last **CI** run on the default branch. Red means something upstream changed (a runner image, PyQt6, ruff), so read the failing step first.
2. Run **Build AppImage** by hand (Actions, Build AppImage, Run workflow) and download the artifact, since that's the only way to know python-appimage and the base images still work together.
3. Run the downloaded AppImage on your own machine and open the Backends panel. Every tool you have installed should show a path.

## 2. Things outside the repo that drift

- **GitHub Actions versions.** The workflows use major tags (`actions/checkout@v5`, `actions/setup-python@v6`, `actions/upload-artifact@v4`, `softprops/action-gh-release@v2`). When GitHub warns about a deprecated Node runtime or a new major ships, bump the major. Nothing is pinned to a digest on purpose.
- **Python base image.** The build picks the newest `pythonX.Y` release from niess/python-appimage on every run. If a brand new Python breaks PyQt6 wheels, the build fails at the pip step. Wait a week for wheels, or temporarily hardcode the previous version in the "Pick the newest Python base image" step and leave a note in changes.md to undo it.
- **PyQt6 and zxcvbn.** `requirements.txt` is unpinned, so every build gets the latest. If a PyQt6 release breaks something, the CI import smoke test catches it.
- **ubuntu-latest.** When GitHub moves it to a new Ubuntu, `libfuse2` package names have changed before (it became `libfuse2t64`). The install step tries both. If the AppImage step fails with a FUSE error, that's where to look.

## 3. Run the tests locally

```
QT_QPA_PLATFORM=offscreen python3 -m pytest -q tests
ruff check .
```

The cryptsetup test only runs as root (cryptsetup wants its lock directory). Run `sudo -E python3 -m pytest -q tests -k cryptsetup` once in a while to cover it.

## 4. Spot-check the dangerous paths on throwaway hardware

Do this after any change to `backends/wipe.py`, `core/escalation.py` or `core/root_helper.py`, and at least twice a year regardless.

1. Plug in a USB stick you don't care about.
2. Disk Destroyer, Wipe mode, one pass. It should ask for authentication once, show progress, and finish with "Overwrite finished".
3. Check it worked: `sudo head -c 1M /dev/sdX | xxd | head` should show random bytes, not your old data.
4. Drive Crypto: format the stick as LUKS2, back up the header, open it, close it.
5. Free-Space Wipe on a small mounted filesystem (the stick works after `mkfs.ext4`). The fill file must be gone afterward.

## 5. Ship an update

1. Add an entry to `changes.md` in the usual format.
2. Bump `__version__` in `keelhaul/__init__.py` to the date (`YYYY.MM.DD`).
3. Commit, then tag it with the same date: `git tag 2026.11.04 && git push --tags`
4. Wait for the Build AppImage run to attach the AppImage to the release.
