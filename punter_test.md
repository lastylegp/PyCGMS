# punter_test.py

Automated tests for the Punter C1 / Multi-Punter transfers in `file_transfer.py`
(spec: https://www.pagetable.com/?p=1663).

## What it does

Exercices `FileTransfer.send_file()` / `receive_file()` against fake
in-process BBS peers connected through queues — no network, no modem, no
timeouts in the protocol itself:

- `Sender`: spec sender (incl. the buggy 3× `S/B` end-off). Counterpart of
  **downloads**. Knobs: `lag_max` (random send delays), `chunk` (1-byte
  fragmentation), `endoff_gap`, `corrupt_once_at` ( checksum failure on one
  block → `BAD` recovery).
- `BBSReceiver`: strict single-round spec receiver (CGTerm mirror, no extra
  `GOO`/`ACK` rounds). Counterpart of **uploads**. Knob: `bad_once_at`.

Covered: single/multi plain downloads, laggy links, slow end-offs, checksum
and `BAD` recovery, block-size edges (1/248/249 bytes), sender-first `GOO`,
slow inter-file gaps, typeless headers (→ `.prg`), abort-after-last-file
(CTRL+X path), single/multi uploads incl. laggy and `BAD` recovery, header
names/types and the multi END marker.

Every transfer runs under a watchdog thread: on expiry the transfer is
cancelled and the test fails instead of hanging forever. Fake peer crashes
are surfaced as assertion errors, not silent timeouts.

## Run

```sh
./.venv/bin/python -m pytest punter_test.py -v
```

Fast subset (skips the deliberately laggy cases):

```sh
./.venv/bin/python -m pytest punter_test.py -v -k "not laggy"
```

Note: the full suite takes several minutes (laggy links are slow on purpose).
Test data is deterministic (seeded); files live in pytest `tmp_path`
directories — nothing is written to the repo.

## Requirements

`pytest` in `.venv` (`./.venv/bin/python -m pip install pytest`).
