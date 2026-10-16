# Voice clip manifest — DFPlayer Mini

> **NOTHING HAS BEEN RECORDED OR PLAYED.** No MP3 exists in this repository, none was
> downloaded, and no audio has been verified on hardware. This file is the *spec* for
> what Gg must record.

---

## 1. SD card layout

The firmware addresses clips by **folder + file number**, never by copy index, so
playback does not depend on the order the files were copied.

```
SD root
└── 01/
    ├── 001.mp3
    ├── 002.mp3
    ├── 003.mp3
    ├── 004.mp3
    ├── 005.mp3
    ├── 006.mp3
    ├── 007.mp3
    └── 008.mp3
```

* Format the card **FAT32**. A 32 GB card may need FAT32 specifically — `exFAT` is
  frequently unsupported by the DFPlayer's reader.
* Folder name is the number **`01`**. Change `IndConfig`/the firmware's folder constant
  together with the card.
* File names must be **exactly three digits**. `1.mp3` will not be found.

---

## 2. Clip table

| # | Wire name | Exact spoken text | Max length | Triggered by |
|---|---|---|---|---|
| 001 | `person_detected` | "Person detected." | 2.0 s | backend, after 3 consecutive close frames, max once per 10 s |
| 002 | `please_stop` | "Please stop at the marked area." | 2.0 s | **not wired yet** — reserved |
| 003 | `sampling` | "Sampling. Please stay still." | 2.0 s | firmware, at pre-roll start |
| 004 | `analyzing` | "Analyzing." | 2.0 s | firmware, at MQ3 window close |
| 005 | `pass` | "Pass. You may proceed." | 2.0 s | backend, authenticated PASS verdict only |
| 006 | `warn` | "Warning. Please wait for assistance." | 2.0 s | backend WARN verdict, **or the firmware timeout** |
| 007 | `fail` | "Fail. Access denied." | 2.0 s | backend, authenticated FAIL verdict only |
| 008 | `system_ready` | "System ready." | 2.0 s | **not wired yet** — reserved |

**Hard limits the firmware assumes:**

* Every clip ≤ 2.0 s nominal, so a cue can never overrun the next event.
* Clip 003 is budgeted at 1.5 s, which is **shorter than the 3 s pre-roll**, so
  "stay still" has finished before the window closes.
* Clip 006 is budgeted at 2.5 s because it is also the timeout cue and must be
  unambiguous.

If your recording is longer than the table says, raise the matching duration in
`ind_clip_duration()` in `indicator.h` **and** re-run the host harness, which asserts
every clip is ≤ 3 s.

---

## 3. Recording rules

* **Never speak a name, a BAC value, an employee id or any personal data.** These
  clips are audible in a public space. The text above is the complete allowed content.
* Record in a quiet room; gate ambience is loud and the DFPlayer's speaker is small.
* Leave ~0.3 s of silence at the start and end of each clip — the DFPlayer can click
  audibly on a clipped waveform.
* Keep the level consistent across all eight clips. Normalise to the same peak.

---

## 4. Format

| Property | Value |
|---|---|
| Container | MP3 |
| Sample rate | **44.1 kHz** or 32 kHz (either works; do not mix) |
| Channels | **Mono** preferred; stereo works but wastes SD and bandwidth |
| Bitrate | 64–128 kbps mono is plenty for speech |

The DFPlayer's decoder is happier with a plain CBR MP3. Avoid VBR and avoid any
metadata/ID3 tags larger than a few hundred bytes.

---

## 5. DFPlayer commands used — **ALL UNCONFIRMED**

`alcodetect_esp32/dfplayer.h` builds every frame. The 10-byte layout and the checksum
are asserted by `tests/indicator_test.cpp`, which proves the *code* matches this table —
it proves nothing about whether the *bytes are right for the hardware*.

| Byte position | Meaning | Value used | Command |
|---|---|---|---|
| 0 | start | `0x7E` | all |
| 1 | fixed | `0xFF` | all |
| 2 | length | `0x06` | all |
| 3 | command | `0x0C` reset | `dfplayer_frame_reset()` |
| 3 | command | `0x09` select SD | `dfplayer_frame_select_sd()` |
| 3 | command | `0x06` volume | `dfplayer_frame_volume()` |
| 3 | command | `0x0F` play folder+file | `dfplayer_frame_play()` |
| 3 | command | `0x16` stop | `dfplayer_frame_stop()` |
| 4 | feedback | `0x01` = request ACK | all |
| 5 | param high | volume (0–30), or folder | volume / play |
| 6 | param low | file (1–99) | play |
| 7–8 | checksum | `(0 - sum(bytes 1..6)) mod 65536` | all |
| 9 | end | `0xEF` | all |

Hand-worked example, asserted in the test:

```
play folder 01 file 001
  bytes[1..6] = FF 06 0F 01 01 01
  sum         = 255 + 6 + 15 + 1 + 1 + 1 = 279
  checksum    = (0 - 279) mod 65536 = 65257 = 0xFEE9
  frame       = 7E FF 06 0F 01 01 01 FE E9 EF
```

**Unconfirmed, and each needs a hardware test by Gg:**

* Is the length byte `0x06` correct, or should the frame be 10 bytes with the length
  excluding the header and checksum?
* Is `0xFF` the right second byte, or is it `0xFF` only in the ACK variant?
* Is folder+file really `0x0F`, or is single-file play `0x03` and folder play a
  different command?
* Is the SD selector parameter `0x02` for SD (vs `0x00` for USB, `0x01` for flash)?
* Is **9600 baud** correct? The datasheet commonly says 9600 but many modules ship as
  38400 or 38400 with a 9600 default. If nothing plays, try 38400 first.
* Does `volume` belong in the high byte, or is it `0x06` with the value split?

**If no clip plays, the order to try:** 9600 → 38400 baud; then check TX/RX are swapped;
then try `esptool`-style single-file play `0x03`. Log what the module's own
`serial-monitor` output shows on the DFPlayer's TX line.

---

## 6. Generating the clips offline

`tools/gen_audio.sh` will render the eight clips **if** a local offline TTS is
installed. It downloads nothing.

```bash
tools/gen_audio.sh --dry-run     # print the plan, write nothing
tools/gen_audio.sh               # render into ./build/audio/ (gitignored)
tools/gen_audio.sh --copy-to-sd  # also copy to a mounted SD card, if you point it at one
```

If no TTS engine is found it prints the eight lines to record by hand and exits 0, so
it is safe to run anywhere.

**MP3 binaries must never be committed.** They go in `build/audio/`, which is ignored.

---

## 7. First hardware test

1. Format a FAT32 card, create `01/`, copy the eight MP3s in.
2. Insert the card, power the DFPlayer, and listen to the module's own activity.
3. Press **Settings → Sensors → Indicators → "Analyzing"** (clip 004, a single word).
   If you hear it, the protocol and the baud rate are right and you can stop guessing.
4. If not, work §5 top to bottom.
5. With the DFPlayer **unplugged**, restart the ESP32 and confirm it still boots and
   still lights LEDs. This is the "audio must never block" requirement.