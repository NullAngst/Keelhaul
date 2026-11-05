"""Tests that run without root, a display, or real drives.

Run: QT_QPA_PLATFORM=offscreen python -m pytest -q tests

Backend tests skip themselves when the tool is not installed. The root
helper is exercised by running it as the current user (no elevation), which
tests the protocol, the allowlist and the secret pipes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QCoreApplication, QTimer  # noqa: E402

from keelhaul.backends import age_gpg, cryptsetup, hashes, mat2_exif, wipe  # noqa: E402
from keelhaul.core import escalation  # noqa: E402
from keelhaul.core.devices import Device  # noqa: E402
from keelhaul.core.runner import RootJob, UserJob  # noqa: E402
from keelhaul.core.secrets import SecretBuffer  # noqa: E402

app = QCoreApplication.instance() or QCoreApplication([])
HELPER = (ROOT / "keelhaul" / "core" / "root_helper.py").read_text()


def need(*tools):
    missing = [t for t in tools if not shutil.which(t)]
    if missing:
        pytest.skip("missing " + ", ".join(missing))


def secret(text: str) -> SecretBuffer:
    s = SecretBuffer()
    s.append(text.encode())
    return s


def run_jobs(jobs, timeout_ms=60000):
    codes = []

    def step():
        if not jobs:
            app.quit()
            return
        j = jobs.pop(0)
        j.finished.connect(lambda c: (codes.append(c), step()))
        j.start()

    QTimer.singleShot(0, step)
    QTimer.singleShot(timeout_ms, app.quit)
    app.exec()
    return codes


class DirectSession(escalation.RootSession):
    """Runs the helper as the current user. Test only."""

    def open(self):
        self._tried.append("direct")
        self.proc = subprocess.Popen([sys.executable, "-I", "-c", HELPER], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        import threading
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()


# ---- pure functions -------------------------------------------------
def test_secret_buffer_zero_and_utf8_pop():
    s = SecretBuffer(32)
    s.append("pässwörd".encode())
    s.pop_char()
    assert bytes(s.view()).decode() == "pässwör"
    s.clear()
    assert len(s) == 0 and not any(s._buf)


def test_secret_buffer_refuses_overflow():
    s = SecretBuffer(4)
    assert not s.append(b"12345")
    assert len(s) == 0


def test_no_secret_on_argv():
    o = cryptsetup.FormatOptions()
    for argv in (cryptsetup.format_argv("/dev/x", o), cryptsetup.open_argv("/dev/x", "n"),
                 cryptsetup.addkey_argv("/dev/x", o), age_gpg.gpg_encrypt("o", "i", False)):
        assert any("@FD" in a for a in argv)


def test_kdf_floors():
    o = cryptsetup.FormatOptions(iter_time_ms=10, memory_kib=1)
    args = o.pbkdf_args()
    assert args[args.index("--iter-time") + 1] == str(cryptsetup.ITER_TIME_FLOOR_MS)
    assert args[args.index("--pbkdf-memory") + 1] == str(cryptsetup.MEMORY_FLOOR_KIB)


def test_shred_progress():
    assert wipe.shred_progress("shred: /dev/sdb: pass 1/2 (random)...5.0GiB/10GiB 50%") == pytest.approx(0.25)
    assert wipe.shred_progress("unrelated") is None


def test_hdparm_parse():
    text = ("Security: \n\tMaster password revision code = 65534\n\t\tsupported\n\tnot\tenabled\n"
            "\tnot\tlocked\n\tnot\tfrozen\n\tnot\texpired: security count\n"
            "\t\tsupported: enhanced erase\n\t2min for SECURITY ERASE UNIT. 2min for ENHANCED "
            "SECURITY ERASE UNIT.\nLogical Unit WWN Device Identifier: 5002538\n")
    sec = wipe.parse_hdparm_identify(text)
    assert sec.supported and not sec.enabled and not sec.frozen and sec.enhanced
    frozen = wipe.parse_hdparm_identify(text.replace("not\tfrozen", "\tfrozen"))
    assert frozen.frozen


def test_hash_helpers():
    assert hashes.normalize_expected("ABCDEF  file.iso\n") == "abcdef"
    assert hashes.matches("abcdef", "ABCDEF")
    assert not hashes.matches("abcdef", "")


# ---- root helper ----------------------------------------------------
def test_helper_allowlist_and_secret_pipe(tmp_path):
    out = tmp_path / "sec.out"
    p = subprocess.Popen([sys.executable, "-I", "-c", HELPER], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE)
    assert json.loads(p.stdout.readline())["type"] == "ready"
    reqs = [({"cmd": "start", "id": "a", "argv": ["rm", "-rf", "/nonexistent"], "secrets": []}, b""),
            ({"cmd": "start", "id": "b", "argv": ["dd", "if=@FDPATH0@", f"of={out}"],
              "secrets": [6]}, b"s3cr3t")]
    for req, extra in reqs:
        p.stdin.write((json.dumps(req) + "\n").encode() + extra)
    p.stdin.close()
    events = [json.loads(line) for line in p.stdout]
    assert any(e.get("type") == "error" and "not allowed" in e.get("message", "") for e in events)
    assert {"type": "exit", "id": "b", "code": 0} in events
    assert out.read_bytes() == b"s3cr3t"


def test_wipe_runner_overwrite_and_destroy(tmp_path):
    img = tmp_path / "disk.img"
    img.write_bytes(os.urandom(1024 * 1024))
    dev = Device("disk", str(img), img.stat().st_size, "disk", True, "sata", "Test", "123",
                 "", False, False)
    caps = type("C", (), {"has": staticmethod(lambda t: False)})()

    sess = DirectSession()
    r = wipe.WipeRunner(sess, dev, str(img), "wipe", caps, passes=1, zero_last=True)
    results = []
    r.done.connect(lambda ok: (results.append(ok), app.quit()))
    QTimer.singleShot(0, r.start)
    QTimer.singleShot(30000, app.quit)
    app.exec()
    assert results == [True]
    assert img.read_bytes() == b"\0" * (1024 * 1024)

    sess2 = DirectSession()
    d = wipe.WipeRunner(sess2, dev, str(img), "destroy", caps)
    results2 = []
    d.done.connect(lambda ok: (results2.append(ok), app.quit()))

    def stop_after_two(_s):
        if d.record.passes >= 2:
            d.stop()

    d.status.connect(stop_after_two)
    QTimer.singleShot(0, d.start)
    QTimer.singleShot(30000, app.quit)
    app.exec()
    assert results2 == [False] and d.record.passes >= 2
    assert "Stopped by you" in d.record.result


# ---- backends -------------------------------------------------------
def test_age_roundtrip(tmp_path):
    need("age")
    src = tmp_path / "a.txt"
    src.write_text("keelhaul\n")
    enc = tmp_path / "a.txt.age"
    dec = tmp_path / "a.out"
    codes = run_jobs([
        UserJob("enc", age_gpg.age_encrypt(str(enc), str(src)), [secret("pw one two")], pty_answers=2),
        UserJob("dec", age_gpg.age_decrypt(str(dec), str(enc)), [secret("pw one two")], pty_answers=1),
    ])
    assert codes == [0, 0]
    assert age_gpg.detect(str(enc)) == "age"
    assert dec.read_text() == "keelhaul\n"


def test_age_folder_stream(tmp_path):
    need("age", "tar")
    folder = tmp_path / "f"
    folder.mkdir()
    (folder / "x").write_text("x")
    enc = tmp_path / "f.tar.age"
    codes = run_jobs([UserJob("enc", age_gpg.age_encrypt(str(enc), None), [secret("pw")],
                              feeder=age_gpg.tar_feeder(str(folder)), pty_answers=2)])
    assert codes == [0] and enc.stat().st_size > 0
    assert not (tmp_path / "f.tar").exists()


def test_gpg_roundtrip(tmp_path, monkeypatch):
    need("gpg")
    monkeypatch.setenv("GNUPGHOME", str(tmp_path / "gnupg"))
    (tmp_path / "gnupg").mkdir(mode=0o700)
    src = tmp_path / "b.txt"
    src.write_text("keelhaul\n")
    enc = tmp_path / "b.txt.gpg"
    dec = tmp_path / "b.out"
    codes = run_jobs([
        UserJob("enc", age_gpg.gpg_encrypt(str(enc), str(src), False), [secret("pw")]),
        UserJob("dec", age_gpg.gpg_decrypt(str(dec), str(enc)), [secret("pw")]),
    ])
    assert codes == [0, 0] and dec.read_text() == "keelhaul\n"


def test_hash_and_check(tmp_path):
    need("sha256sum")
    f = tmp_path / "c.bin"
    f.write_bytes(b"abc")
    out = []
    j = UserJob("h", hashes.hash_argv("sha256sum", str(f)))
    j.line.connect(out.append)
    assert run_jobs([j]) == [0]
    digest = hashes.parse_digest(out[0])
    assert digest == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    (tmp_path / "SHA256SUMS").write_text(f"{digest}  c.bin\n{'0' * 64}  missing.iso\n")
    j2 = UserJob("c", hashes.check_argv("sha256sum", str(tmp_path / "SHA256SUMS")), cwd=str(tmp_path))
    assert run_jobs([j2]) == [0]


def test_mat2_and_exiftool(tmp_path):
    need("exiftool")
    from PyQt6.QtGui import QImage
    img = tmp_path / "p.jpg"
    q = QImage(8, 8, QImage.Format.Format_RGB32)
    q.fill(0xFF8800)
    q.setText("Author", "Zack")
    assert q.save(str(img))
    subprocess.run(["exiftool", "-overwrite_original", "-Artist=Someone", str(img)], check=True,
                   capture_output=True)
    jobs = [UserJob("exif", mat2_exif.exiftool_argv(str(img)))]
    assert run_jobs(jobs) == [0]
    cleaned = mat2_exif.cleaned_name(str(img))
    meta = subprocess.run(["exiftool", "-Artist", cleaned], capture_output=True, text=True).stdout
    assert "Someone" not in meta
    mat2_ok = shutil.which("mat2") and subprocess.run(["mat2", "--version"], capture_output=True).returncode == 0
    if mat2_ok:
        os.remove(cleaned)
        assert run_jobs([UserJob("mat2", mat2_exif.mat2_argv(str(img)))]) == [0]
        assert os.path.exists(cleaned)


@pytest.mark.skipif(os.geteuid() != 0, reason="cryptsetup wants root for its lock directory")
def test_cryptsetup_format_dump_addkey(tmp_path):
    need("cryptsetup")
    img = tmp_path / "luks.img"
    with open(img, "wb") as fh:
        fh.truncate(32 * 1024 * 1024)
    o = cryptsetup.FormatOptions(iter_time_ms=1000, memory_kib=cryptsetup.MEMORY_FLOOR_KIB, parallel=1)
    sess = DirectSession()
    backup = tmp_path / "hdr.bak"
    jobs = [
        RootJob(sess, "format", cryptsetup.format_argv(str(img), o), [secret("first pass")], throttle=False),
        RootJob(sess, "addkey", cryptsetup.addkey_argv(str(img), o),
                [secret("first pass"), secret("second pass")], throttle=False),
        RootJob(sess, "dump", cryptsetup.dump_argv(str(img)), throttle=False),
        RootJob(sess, "backup", cryptsetup.header_backup_argv(str(img), str(backup)), throttle=False),
    ]
    dump = []
    jobs[2].line.connect(dump.append)
    codes = run_jobs(jobs, 120000)
    sess.close()
    assert codes == [0, 0, 0, 0], "\n".join(dump)
    text = "\n".join(dump)
    assert "argon2id" in text and "aes-xts-plain64" in text
    assert text.count("luks2") >= 2  # two keyslots
    assert backup.stat().st_size > 0


def test_escalation_failure_fails_queued_jobs(monkeypatch):
    from keelhaul.core import env
    monkeypatch.setattr(env, "which", lambda name: None)
    sess = escalation.RootSession("pkexec")
    reasons = []
    sess.failed.connect(reasons.append)
    job = RootJob(sess, "x", ["shred", "--", "/nonexistent"], [secret("pw")])
    codes = run_jobs([job], 5000)
    assert codes == [-1] and reasons and sess.dead
    # A second submit on a dead session fails right away instead of hanging.
    assert run_jobs([RootJob(sess, "y", ["sync"])], 5000) == [-1]
