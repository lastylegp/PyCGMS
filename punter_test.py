"""Punter C1 + Multi-Punter transfer tests (spec: https://www.pagetable.com/?p=1663).

Runs FileTransfer downloads/uploads against fake in-process BBS peers over
queues. No network, no timeouts in the protocol itself; every transfer runs
under a watchdog that cancels and fails instead of hanging forever.

Run:  ./.venv/bin/python -m pytest punter_test.py -v   (from this directory)
"""

import os
import queue
import random
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from file_transfer import FileTransfer, TransferProtocol

_rnd = random.Random(7)
D_BIG = _rnd.randbytes(5000)
D_MED = _rnd.randbytes(1500)
D_SMALL = _rnd.randbytes(100)
D_SEQ = bytes((i * 37) % 256 for i in range(3000))


def make_block(payload, next_size, index):
    """One C1 block with valid checksums."""
    rest = bytes([next_size & 0xFF, index & 0xFF, (index >> 8) & 0xFF]) + bytes(payload)
    add = sum(rest) & 0xFFFF
    cyc = 0
    for b in rest:
        cyc ^= b
        cyc = ((cyc << 1) | (cyc >> 15)) & 0xFFFF
    return bytes([add & 0xFF, (add >> 8) & 0xFF, cyc & 0xFF, (cyc >> 8) & 0xFF]) + rest


class FakeConnection:
    """Client-side endpoint: send_raw -> c2s, get_received_data <- s2c."""

    def __init__(self):
        self.c2s = queue.Queue()
        self.s2c = queue.Queue()
        self.receive_queue = self.s2c
        self.connected = True

    def send_raw(self, data):
        self.c2s.put(bytes(data))
        return True

    def get_received_data(self, timeout=0.05):
        try:
            return self.s2c.get(timeout=timeout)
        except queue.Empty:
            return None

    def has_received_data(self):
        return not self.s2c.empty()


class Laggy:
    """Random send delays + 1-byte fragmentation, to simulate slow links."""

    def __init__(self, q, lag_max=0.0, chunk=False):
        self.q = q
        self.lag_max = lag_max
        self.chunk = chunk

    def send(self, data):
        if self.lag_max:
            time.sleep(random.uniform(0, self.lag_max))
        data = bytes(data)
        if self.chunk:
            for b in data:
                self.q.put(bytes([b]))
                time.sleep(0.002)
        else:
            self.q.put(data)


class Sender(Laggy):
    """Spec sender (incl. buggy 3x S/B end-off). Counterpart of downloads."""

    def __init__(self, c2s, s2c, lag_max=0.0, chunk=False, endoff_gap=0.05,
                 corrupt_once_at=None):
        super().__init__(s2c, lag_max, chunk)
        self.c2s = c2s
        self.endoff_gap = endoff_gap
        self.corrupt_once_at = corrupt_once_at
        self._corrupted = set()
        self.rx = bytearray()

    def recv_code(self, expect, timeout=180):
        exp = bytes(expect)
        end = time.time() + timeout
        while time.time() < end:
            try:
                self.rx.extend(self.c2s.get(timeout=0.2))
            except queue.Empty:
                continue
            if len(self.rx) >= 3 and bytes(self.rx[-3:]) == exp:
                self.rx.clear()
                return True
        raise AssertionError(f"sender timeout waiting {exp}, tail={bytes(self.rx[-12:])!r}")

    def send_file_punter(self, data, ftype=0):
        self.recv_code(b'GOO'); self.send(b'ACK')
        self.recv_code(b'S/B')
        tb = make_block(bytes([ftype]), 0xC9, 0xFFFF)
        assert len(tb) == 8
        self.send(tb)
        self.recv_code(b'GOO'); self.send(b'ACK')
        # NOTE: the receiver does an extra GOO/ACK round here (CGTerm
        # punter_recv calls handshake(GOO,ACK) right after recv_block, which
        # itself ends with GOO/ACK) - real senders answer both.
        self.recv_code(b'GOO'); self.send(b'ACK')
        # End-off A
        self.recv_code(b'S/B'); self.send(b'SYN')
        self.recv_code(b'SYN')
        for _ in range(3):
            self.send(b'S/B')
            time.sleep(self.endoff_gap)
        # Phase B
        n = len(data)
        blocks = [data[i:i + 248] for i in range(0, max(n, 1), 248)] if n else []
        first_next = (len(blocks[0]) + 7) if blocks else 7
        self.recv_code(b'GOO'); self.send(b'ACK')
        self.recv_code(b'S/B')
        self.send(make_block(b'', first_next, 0x0000))
        self.recv_code(b'GOO'); self.send(b'ACK')
        idx = 1
        for i, chunk in enumerate(blocks):
            last = (i == len(blocks) - 1)
            if last:
                nxt, bidx = len(chunk) + 7, 0xFFFF
            else:
                rem = n - (i + 1) * 248
                nxt = 255 if rem >= 248 else rem + 7
                bidx = idx
            blk = make_block(chunk, nxt, bidx)
            self.recv_code(b'S/B')
            if self.corrupt_once_at == idx and idx not in self._corrupted:
                self._corrupted.add(idx)
                bad = bytearray(blk)
                bad[7] ^= 0xFF
                self.send(bytes(bad))
                self.recv_code(b'BAD')
                self.send(b'ACK')
                self.recv_code(b'S/B')
                self.send(blk)
            else:
                self.send(blk)
            self.recv_code(b'GOO'); self.send(b'ACK')
            idx += 1
        # End-off B
        self.recv_code(b'S/B'); self.send(b'SYN')
        self.recv_code(b'SYN')
        for _ in range(3):
            self.send(b'S/B')
            time.sleep(self.endoff_gap)


class BBSReceiver(Laggy):
    """Strict single-round spec receiver (CGTerm mirror). Counterpart of uploads."""

    def __init__(self, c2s, s2c, lag_max=0.0, chunk=False, bad_once_at=None):
        super().__init__(s2c, lag_max, chunk)
        self.c2s = c2s
        self.bad_once_at = bad_once_at
        self._badded = set()
        self.n_bad = 0
        self.rx = bytearray()

    def _next_byte(self, timeout=0.2):
        if self.rx:
            return self.rx.pop(0)
        try:
            d = self.c2s.get(timeout=timeout)
        except queue.Empty:
            return None
        if not d:
            return None
        if len(d) > 1:
            self.rx.extend(d[1:])
        return d[0]

    def recv_code(self, expect, timeout=180):
        exp = bytes(expect)
        end = time.time() + timeout
        while time.time() < end:
            try:
                self.rx.extend(self.c2s.get(timeout=0.2))
            except queue.Empty:
                continue
            if len(self.rx) >= 3 and bytes(self.rx[-3:]) == exp:
                self.rx.clear()
                return True
        raise AssertionError(f"receiver timeout waiting {exp}, tail={bytes(self.rx[-12:])!r}")

    def recv_exact(self, n, timeout=180):
        out = bytearray()
        end = time.time() + timeout
        while len(out) < n and time.time() < end:
            b = self._next_byte(0.2)
            if b is not None:
                out.append(b)
        if len(out) < n:
            raise AssertionError(f"receiver timeout reading {n} bytes, got {len(out)}")
        return bytes(out)

    def check(self, block):
        if bytes(make_block(block[7:], block[4], block[5] | (block[6] << 8))) != bytes(block):
            raise AssertionError(f"receiver checksum mismatch: {bytes(block).hex()}")

    def drain_sb(self):
        end = time.time() + 1.5
        buf = bytearray()
        while time.time() < end:
            try:
                buf.extend(self.c2s.get(timeout=0.1))
            except queue.Empty:
                continue
            while len(buf) >= 3 and bytes(buf[:3]) == b'S/B':
                del buf[:3]
                end = time.time() + 1.0
        if buf:
            self.rx.extend(buf)

    def recv_file(self, expect_ftype=None):
        self.send(b'GOO'); self.recv_code(b'ACK')
        self.send(b'S/B')
        blk = self.recv_exact(8)
        self.check(blk)
        assert blk[5] == 0xFF and blk[6] == 0xFF, "type block index"
        if expect_ftype is not None:
            assert blk[7] == expect_ftype, f"type byte {blk[7]} != {expect_ftype}"
        self.send(b'GOO'); self.recv_code(b'ACK')
        self.send(b'S/B'); self.recv_code(b'SYN')
        self.send(b'SYN')
        self.recv_code(b'S/B'); self.drain_sb()
        self.send(b'GOO'); self.recv_code(b'ACK')
        self.send(b'S/B')
        blk = self.recv_exact(7)
        self.check(blk)
        assert blk[5] == 0 and blk[6] == 0, "header block index"
        self.send(b'GOO'); self.recv_code(b'ACK')
        data = bytearray()
        nxt, idx, bi = blk[4], 0, 1
        while idx < 0xFF00 and nxt >= 7:
            self.send(b'S/B')
            blk = self.recv_exact(nxt)
            self.check(blk)
            idx = blk[5] | (blk[6] << 8)
            nxt = blk[4]
            if self.bad_once_at == bi and bi not in self._badded:
                self._badded.add(bi)
                self.n_bad += 1
                self.send(b'BAD'); self.recv_code(b'ACK')
                self.send(b'S/B')
                blk = self.recv_exact(len(blk))
                self.check(blk)
                idx = blk[5] | (blk[6] << 8)
                nxt = blk[4]
                self.send(b'GOO'); self.recv_code(b'ACK')
            else:
                self.send(b'GOO'); self.recv_code(b'ACK')
            data.extend(blk[7:])
            bi += 1
        self.send(b'S/B'); self.recv_code(b'SYN')
        self.send(b'SYN')
        self.recv_code(b'S/B'); self.drain_sb()
        return bytes(data)

    def recv_header_line(self, timeout=180):
        buf = bytearray()
        tabs = 0
        end = time.time() + timeout
        while time.time() < end:
            b = self._next_byte(0.2)
            if b is None:
                continue
            buf.append(b)
            if b == 0x09:
                tabs += 1
                if tabs == 16:
                    buf = bytearray()
            elif b == 0x0D and tabs >= 16:
                line = bytes(x for x in buf[:-1] if x != 0x04)
                if sum(1 for x in buf if x == 0x04) >= 10:
                    return ('end',)
                name, _, typ = line.decode('latin-1').rpartition(',')
                return ('header', name, typ)
        raise AssertionError("receiver timeout waiting header")


def run_guarded(fn, cancel, timeout):
    """Run fn in a thread; cancel + fail (never hang) on timeout. Re-raises."""
    res = {}

    def wrap():
        try:
            res['v'] = fn()
        except Exception as e:
            res['exc'] = e

    t = threading.Thread(target=wrap, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        try:
            cancel()
        finally:
            t.join(5)
        pytest.fail("transfer hung and had to be cancelled")
    if 'exc' in res:
        raise res['exc']
    return res.get('v')


def run_peer(fn, timeout=30):
    """Run a fake BBS peer; fail if it hangs or dies."""
    errs = []

    def wrap():
        try:
            fn()
        except Exception as e:
            errs.append(e)

    t = threading.Thread(target=wrap, daemon=True)
    t.start()
    t.join(timeout)
    assert not t.is_alive(), "bbs peer hung"
    assert not errs, f"bbs peer died: {errs[0]!r}"


def sender_script(files, **kw):
    """Sender driver. files: bytes (single) or [(raw_header, ftype, data)]."""
    def run(conn):
        s = Sender(conn.c2s, conn.s2c, **kw)
        if isinstance(files, bytes):
            s.send_file_punter(files)
        else:
            for header, ftype, data in files:
                s.send(b'\x09' * 16 + header + b'\x0D')
                s.send_file_punter(data, 0 if ftype == 'P' else 1)
            s.send(b'\x09' * 16 + b'\x04' * 16 + b'\x0D')
    return run


def download(tmp_path, script, timeout=90):
    """Download via FileTransfer into a fresh dir. Returns that dir."""
    conn = FakeConnection()
    errs = []

    def peer():
        try:
            script(conn)
        except Exception as e:
            errs.append(e)

    t = threading.Thread(target=peer, daemon=True)
    t.start()
    ft = FileTransfer(conn, protocol=TransferProtocol.PUNTER, debug=False)
    ok = run_guarded(lambda: ft.receive_file(str(tmp_path), None), ft.cancel, timeout)
    t.join(30)
    assert not t.is_alive(), "bbs peer hung"
    assert not errs, f"bbs peer died: {errs[0]!r}"
    assert ok, "receive_file returned False"
    return tmp_path


def read_all(d):
    names = sorted(os.listdir(d))
    assert len(names) == 1, f"expected 1 file, got {names}"
    with open(os.path.join(d, names[0]), 'rb') as f:
        return f.read()


def read_named(d, name):
    with open(os.path.join(d, name), 'rb') as f:
        return f.read()


# --- downloads -------------------------------------------------------------

def test_single_plain(tmp_path):
    assert read_all(download(tmp_path, sender_script(D_BIG))) == D_BIG


def test_single_laggy(tmp_path):
    assert read_all(download(tmp_path, sender_script(D_MED, lag_max=0.5, chunk=True),
                             timeout=240)) == D_MED


def test_single_slow_endoff(tmp_path):
    assert read_all(download(tmp_path, sender_script(D_SMALL, endoff_gap=1.0))) == D_SMALL


def test_single_checksum_recovery(tmp_path):
    assert read_all(download(tmp_path, sender_script(D_SEQ, corrupt_once_at=2))) == D_SEQ


@pytest.mark.parametrize("size", [1, 248, 249])
def test_single_sizes(tmp_path, size):
    data = random.Random(11).randbytes(size)
    assert read_all(download(tmp_path, sender_script(data))) == data


def test_single_goo_first(tmp_path):
    def run(conn):
        s = Sender(conn.c2s, conn.s2c)
        s.send(b'GOO')  # spec A1 sender-first variant
        s.send_file_punter(D_SMALL)
    assert read_all(download(tmp_path, run)) == D_SMALL


def test_multi_plain(tmp_path):
    d = download(tmp_path, sender_script([(b'GAME1,P', 'P', D_BIG),
                                          (b'README,S', 'S', D_SMALL),
                                          (b'TOOL,P', 'P', D_MED)]))
    assert read_named(d, 'game1.prg') == D_BIG
    assert read_named(d, 'readme.seq') == D_SMALL
    assert read_named(d, 'tool.prg') == D_MED


def test_multi_laggy(tmp_path):
    d = download(tmp_path, sender_script([(b'GAME1,P', 'P', D_MED),
                                          (b'README,S', 'S', D_SMALL)],
                                         lag_max=0.5, chunk=True), timeout=240)
    assert read_named(d, 'game1.prg') == D_MED
    assert read_named(d, 'readme.seq') == D_SMALL


def test_multi_slow_gap_no_timeout(tmp_path):
    conn = FakeConnection()

    def run(c):
        s = Sender(c.c2s, c.s2c)
        s.send(b'\x09' * 16 + b'G1,P' + b'\x0D')
        s.send_file_punter(D_SMALL)
        time.sleep(4)  # slow BBS between files: must not time out
        s.send(b'\x09' * 16 + b'G2,P' + b'\x0D')
        s.send_file_punter(D_SMALL)
        s.send(b'\x09' * 16 + b'\x04' * 16 + b'\x0D')

    peer = threading.Thread(target=lambda: run(conn), daemon=True)
    peer.start()
    ft = FileTransfer(conn, protocol=TransferProtocol.PUNTER, debug=False)
    assert run_guarded(lambda: ft.receive_file(str(tmp_path), None), ft.cancel, 90)
    peer.join(30)
    assert not peer.is_alive()
    assert read_named(tmp_path, 'g1.prg') == D_SMALL
    assert read_named(tmp_path, 'g2.prg') == D_SMALL


def test_multi_typeless_header_defaults_prg(tmp_path):
    d = download(tmp_path, sender_script([(b'NOTYPEFILE', 'P', D_SMALL),
                                          (b'EMPTYTYPE,', 'P', D_SMALL)]))
    assert read_named(d, 'notypefile.prg') == D_SMALL
    assert read_named(d, 'emptytype.prg') == D_SMALL


def test_multi_abort_closes_transfer(tmp_path):
    """BBS goes silent after last file (no END): cancel must end it."""
    conn = FakeConnection()

    def run(c):
        s = Sender(c.c2s, c.s2c)
        s.send(b'\x09' * 16 + b'F1,P' + b'\x0D')
        s.send_file_punter(D_MED)
        s.send(b'\x09' * 16 + b'F2,P' + b'\x0D')
        s.send_file_punter(D_SMALL)
        time.sleep(120)

    peer = threading.Thread(target=lambda: run(conn), daemon=True)
    peer.start()
    ft = FileTransfer(conn, protocol=TransferProtocol.PUNTER, debug=False)
    res = {}
    r = threading.Thread(
        target=lambda: res.update(v=ft.receive_file(str(tmp_path), None)), daemon=True)
    t0 = time.time()
    r.start()
    while time.time() - t0 < 60:
        names = os.listdir(tmp_path)
        if 'f1.prg' in names and 'f2.prg' in names:
            break
        time.sleep(0.5)
    else:
        pytest.fail("files never arrived")
    ft.cancel()  # <-- CTRL+X
    r.join(10)
    assert not r.is_alive(), "transfer hung after cancel"
    assert res.get('v'), "receive_file returned False after abort"
    assert read_named(tmp_path, 'f1.prg') == D_MED
    assert read_named(tmp_path, 'f2.prg') == D_SMALL


# --- uploads ---------------------------------------------------------------

def upload(tmp_path, files, timeout=90, **bbs_kw):
    """Upload file(s) to a strict fake receiver. Returns the receiver.

    files: bytes (single .prg) or list thereof; ('S', bytes) makes a .seq.
    """
    conn = FakeConnection()
    bbs = BBSReceiver(conn.c2s, conn.s2c, **bbs_kw)
    multi = isinstance(files, list)
    norm = []
    for i, item in enumerate(files if multi else [files]):
        ftype, data = item if isinstance(item, tuple) else ('P', item)
        p = os.path.join(str(tmp_path), f"up{i}{'.seq' if ftype == 'S' else '.prg'}")
        with open(p, 'wb') as f:
            f.write(data)
        norm.append((p, ftype, data))
    paths = [p for p, _, _ in norm]
    errs = []

    def peer():
        try:
            if multi:
                out = []
                for p, ftype, _ in norm:
                    kind = bbs.recv_header_line()
                    assert kind[0] == 'header', f"expected header, got {kind}"
                    base = os.path.basename(p)[:16].upper()
                    assert kind[1] == base, f"name {kind[1]!r} != {base!r}"
                    assert kind[2] == ftype, f"type {kind[2]!r}"
                    out.append(bbs.recv_file(expect_ftype=1 if ftype == 'S' else 0))
                assert bbs.recv_header_line()[0] == 'end', "expected END marker"
                return out
            return bbs.recv_file()
        except Exception as e:
            return e

    t = threading.Thread(target=lambda: errs.append(peer()), daemon=True)
    t.start()
    ft = FileTransfer(conn, protocol=TransferProtocol.PUNTER, debug=False)
    ok = run_guarded(lambda: ft.send_file(paths if multi else paths[0], None),
                     ft.cancel, timeout)
    t.join(30)
    assert not t.is_alive(), "bbs peer hung"
    got = errs[0] if errs else None
    assert not isinstance(got, Exception), f"bbs peer died: {got!r}"
    assert ok, "send_file returned False"
    want = [d[1] if isinstance(d, tuple) else d for d in (files if multi else [files])]
    assert (got if multi else [got]) == want
    return bbs


def test_up_single(tmp_path):
    upload(tmp_path, D_MED)


def test_up_laggy(tmp_path):
    upload(tmp_path, D_MED[:500], timeout=240, lag_max=0.5, chunk=True)


def test_up_bad_recovery(tmp_path):
    bbs = upload(tmp_path, D_MED, bad_once_at=2)
    assert bbs.n_bad >= 1, "BAD path was not exercised"


def test_up_multi(tmp_path):
    upload(tmp_path, [D_MED, ('S', D_SMALL)])
