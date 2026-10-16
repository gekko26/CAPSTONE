/*
 * dfplayer.h — dependency-free DFPlayer Mini frame builder + transport.
 * =================================================================
 * Header-only and free of Arduino types so the host test harness can exercise the
 * exact shipped byte sequences without an ESP32.
 *
 * PROTOCOL STATUS: every command byte below is UNCONFIRMED against the DFRobot
 * datasheet and MUST be tested on hardware by Gg before it is trusted. The datasheet
 * is the source of truth; this file is a best-effort reading of it. See
 * docs/audio/MANIFEST.md.
 *
 * Frame layout (10 bytes):
 *   [0] 0x7E                 start
 *   [1] 0xFF                 second fixed byte
 *   [2] 0x06                 length (6 bytes follow)
 *   [3] cmd                  command byte
 *   [4] feedback             0x01 = ACK requested
 *   [5] param high byte
 *   [6] param low byte
 *   [7] checksum high byte
 *   [8] checksum low byte
 *   [9] 0xEF                 end
 *
 * checksum = (0 - sum(bytes[1..6])) truncated to 16 bits
 */

#ifndef DFPLAYER_H
#define DFPLAYER_H

#include <stdint.h>
#include <stddef.h>

#define DFPLAYER_FRAME_LEN   10
#define DFPLAYER_START       0x7E
#define DFPLAYER_END         0xEF
#define DFPLAYER_LEN_BYTE    0x06
#define DFPLAYER_FEEDBACK    0x01

// ── Command bytes (UNCONFIRMED — verify against the datasheet) ──────────────────
#define DF_CMD_RESET        0x0C
#define DF_CMD_VOLUME       0x06
#define DF_CMD_SELECT_SD    0x09
#define DF_CMD_PLAY_FOLDER  0x0F   // paramHi = folder, paramLo = file
#define DF_CMD_STOP         0x16

#define DF_PARAM_SD_CARD    0x02   // for DF_CMD_SELECT_SD
#define DF_VOLUME_MIN       0
#define DF_VOLUME_MAX       30

/*
 * Build one 10-byte frame into `out`.
 *
 * Returns DFPLAYER_FRAME_LEN on success, or 0 if the arguments are out of range.
 * Rejects a folder/file pair outside 1..99 so a bad caller can never emit a frame
 * that asks the module for a track that does not exist.
 */
static inline size_t dfplayer_build(uint8_t* out,
                                    uint8_t cmd,
                                    uint8_t feedback,
                                    uint8_t paramHi,
                                    uint8_t paramLo) {
    if (out == 0) return 0;
    if (cmd == DF_CMD_VOLUME) {
        if (paramHi > DF_VOLUME_MAX) return 0;      // volume is 0..30
    } else if (cmd == DF_CMD_PLAY_FOLDER) {
        if (paramHi < 1 || paramHi > 99) return 0;   // folder
        if (paramLo < 1 || paramLo > 99) return 0;   // file
    }
    out[0] = DFPLAYER_START;
    out[1] = 0xFF;
    out[2] = DFPLAYER_LEN_BYTE;
    out[3] = cmd;
    out[4] = feedback;
    out[5] = paramHi;
    out[6] = paramLo;
    uint16_t sum = (uint16_t)out[1] + (uint16_t)out[2] + (uint16_t)out[3] +
                   (uint16_t)out[4] + (uint16_t)out[5] + (uint16_t)out[6];
    uint16_t checksum = (uint16_t)(0u - sum);        // two's complement, 16 bits
    out[7] = (uint8_t)(checksum >> 8);
    out[8] = (uint8_t)(checksum & 0xFF);
    out[9] = DFPLAYER_END;
    return DFPLAYER_FRAME_LEN;
}

/* ── Named frames, so callers never hand-roll a command byte ─────────────────── */

static inline size_t dfplayer_frame_reset(uint8_t* out) {
    return dfplayer_build(out, DF_CMD_RESET, DFPLAYER_FEEDBACK, 0x00, 0x00);
}

static inline size_t dfplayer_frame_select_sd(uint8_t* out) {
    return dfplayer_build(out, DF_CMD_SELECT_SD, DFPLAYER_FEEDBACK, DF_PARAM_SD_CARD, 0x00);
}

static inline size_t dfplayer_frame_volume(uint8_t* out, uint8_t volume) {
    return dfplayer_build(out, DF_CMD_VOLUME, DFPLAYER_FEEDBACK, volume, 0x00);
}

static inline size_t dfplayer_frame_stop(uint8_t* out) {
    return dfplayer_build(out, DF_CMD_STOP, DFPLAYER_FEEDBACK, 0x00, 0x00);
}

/* Play /01/005.mp3 by folder+file, so playback does not depend on SD copy order. */
static inline size_t dfplayer_frame_play(uint8_t* out, uint8_t folder, uint8_t file) {
    return dfplayer_build(out, DF_CMD_PLAY_FOLDER, DFPLAYER_FEEDBACK, folder, file);
}

/* Recompute the checksum a frame already carries. Used by the tests to verify. */
static inline uint16_t dfplayer_checksum_of(const uint8_t* frame) {
    uint16_t sum = (uint16_t)frame[1] + (uint16_t)frame[2] + (uint16_t)frame[3] +
                   (uint16_t)frame[4] + (uint16_t)frame[5] + (uint16_t)frame[6];
    return (uint16_t)(0u - sum);
}

#endif /* DFPLAYER_H */