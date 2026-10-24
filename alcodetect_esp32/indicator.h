/*
 * indicator.h — deployment indicator state machine (2x RGB LED + voice cues).
 * =================================================================
 * DISPLAY ONLY. Nothing in this file drives a lock, a relay, or the decision.
 *
 * Header-only, Arduino-free, and free of any global I/O so the host test harness can
 * drive the exact shipped state machine with fake LED/audio sinks.
 *
 * SAFETY INVARIANTS (each has a test in tests/indicator_test.cpp)
 * -------------------------------------------------------------
 *  1. GREEN is reachable ONLY through submitVerdict(), which requires
 *         - state == ANALYZING
 *     - a run id matching the CURRENT run
 *     - a decision inside the analyze deadline
 *     Never on boot, never by default, never from a stale or replayed verdict.
 *  2. Any unknown state name, missing/invalid field or rejected run id leaves the
 *     current state and the LEDs untouched.
 *  3. If no verdict arrives within analyzeTimeoutMs the state falls to WARN and plays
 *     the WARN clip. It never goes green and never stays silent-stuck.
 *  4. In TRAINING mode no clip is ever queued and no verdict or progress colour is
 *     ever shown; the ONLY LED is the system-active idle blink, the same in both
 *     modes — it is a presence signal, not a judgement, so showing it while
 *     collecting data biases nothing about the readings.
 *  5. Progress states are BLUE only, and idle defaults to a BLUE blink (system
 *     active) in BOTH modes, so an in-progress or standby device can never be
 *     misread as showing a verdict.
 */

#ifndef INDICATOR_H
#define INDICATOR_H

#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include "dfplayer.h"

enum IndicatorState {
    IND_IDLE = 0,
    IND_PERSON_DETECTED,
    IND_SAMPLING,
    IND_ANALYZING,
    IND_PASS,
    IND_WARN,
    IND_FAIL
};

/* Voice clips. 000 is "nothing". Numbering matches docs/audio/MANIFEST.md and the
 * SD layout /01/NNN.mp3. */
enum IndClip {
    CLIP_NONE = 0,
    CLIP_PERSON_DETECTED = 1,
    CLIP_PLEASE_STOP = 2,
    CLIP_SAMPLING = 3,
    CLIP_ANALYZING = 4,
    CLIP_PASS = 5,
    CLIP_WARN = 6,
    CLIP_FAIL = 7,
    CLIP_SYSTEM_READY = 8
};

/* ── Shipped defaults. Single source of truth: the .ino aliases these, and the host
 * tests assert against them, so a change here moves firmware, bench and test together. */
#define IND_ANALYZE_TIMEOUT_MS 20000UL   // verdict must arrive within this, else YELLOW
#define IND_RESULT_HOLD_MS      8000UL   // verdict shown this long, then LEDs off
#define IND_CUE_COOLDOWN_MS    10000UL   // repeat suppression for non-verdict cues
#define IND_SLOW_PULSE_MS       1600UL   // person_detected pulse period
#define IND_FAST_PULSE_MS        400UL   // sampling pulse period
#define IND_PERSON_HOLD_MS     60000UL   // person_detected safety net -> idle
#define IND_IDLE_BLINK_MS       1000UL   // system-active idle blink: 500 ms on / 500 ms off

struct IndColor { uint8_t r, g, b; };

struct IndConfig {
    uint32_t analyzeTimeoutMs;   // verdict must arrive within this (default 20000)
    uint32_t resultHoldMs;       // verdict shown this long, then idle (default 8000)
    uint32_t slowPulseMs;        // person_detected pulse period
    uint32_t fastPulseMs;        // sampling pulse period
    uint32_t cueCooldownMs;      // repeat suppression for non-verdict cues
    uint32_t personTimeoutMs;    // person_detected -> idle safety net
    uint32_t idleBlinkPeriodMs;  // system-active idle blink period (default 1000)
    IndColor blue;
    IndColor green;
    IndColor red;
    IndColor yellow;             // red + reduced green; tuned by yellowGreenScale
    uint8_t  progressDuty;       // brightness of the in-progress blue states
    bool     commonAnode;        // LED_COMMON_ANODE build constant
};

static inline IndConfig ind_default_config() {
    IndConfig c;
    c.analyzeTimeoutMs = IND_ANALYZE_TIMEOUT_MS;
    c.resultHoldMs     = IND_RESULT_HOLD_MS;
    c.slowPulseMs      = IND_SLOW_PULSE_MS;
    c.fastPulseMs      = IND_FAST_PULSE_MS;
    c.cueCooldownMs    = IND_CUE_COOLDOWN_MS;
    c.personTimeoutMs  = IND_PERSON_HOLD_MS;
    c.idleBlinkPeriodMs = IND_IDLE_BLINK_MS;
    // BLUE for "system active"/progress, GREEN/YELLOW/RED for verdicts. Tuned for a
    // 3.3 V rail through a series resistor; adjust duty here, not at the call site.
    c.blue   = {0,   0, 255};
    c.green  = {0, 255,   0};
    c.red    = {255, 0,   0};
    // Yellow = red + green at MATCHED CURRENT.
    //
    // Red has a much lower Vf than green (2.0 V vs 3.1 V) on a 3.3 V rail, so at equal
    // duty the red channel always runs hotter: with the documented 330 ohm red and
    // 100 ohm green, full-duty red is 3.94 mA against green's 2.00 mA -- a 1.97:1 mix
    // that reads ORANGE, and WARN became hard to tell from FAIL.
    //
    // Pulling the GREEN duty down made it worse (that is what this constant used to
    // do: green at 190/255 gave a 4.0:1 ratio). The fix is to pull the RED duty down
    // until the two currents match: 255 * (2.00 / 3.94) = 129.
    //
    // THIS IS COUPLED TO THE RESISTORS in docs/INDICATORS_WIRING.md. If you change a
    // channel resistor, recompute: yellow_red_duty = 255 * (I_green / I_red).
    c.yellow = {129, 255, 0};
    c.progressDuty = 200;
    c.commonAnode  = false;   // LED_COMMON_ANODE — set true for common-anode LEDs
    return c;
}

static inline const char* ind_state_name(IndicatorState s) {
    switch (s) {
        case IND_IDLE:           return "idle";
        case IND_PERSON_DETECTED:return "person_detected";
        case IND_SAMPLING:       return "sampling";
        case IND_ANALYZING:      return "analyzing";
        case IND_PASS:           return "pass";
        case IND_WARN:           return "warn";
        case IND_FAIL:           return "fail";
    }
    return "idle";
}

/* Parse a wire state name. Returns false for ANY unknown or malformed name, which is
 * what keeps a typo or an injected value from ever producing a verdict colour. */
static inline bool ind_state_parse(const char* name, IndicatorState* out) {
    if (name == 0) return false;
    if (!strcmp(name, "idle"))                     { *out = IND_IDLE; return true; }
    if (!strcmp(name, "person_detected"))          { *out = IND_PERSON_DETECTED; return true; }
    if (!strcmp(name, "sampling"))                 { *out = IND_SAMPLING; return true; }
    if (!strcmp(name, "analyzing"))                { *out = IND_ANALYZING; return true; }
    if (!strcmp(name, "pass"))                     { *out = IND_PASS; return true; }
    if (!strcmp(name, "warn"))                     { *out = IND_WARN; return true; }
    if (!strcmp(name, "fail"))                     { *out = IND_FAIL; return true; }
    return false;   /* "off" is NOT accepted here: it is a command, not a state name */
}

static inline bool ind_is_verdict(IndicatorState s) {
    return s == IND_PASS || s == IND_WARN || s == IND_FAIL;
}

/* Nominal clip length in ms. Every clip is authored under 2 s; CLIP_SAMPLING is kept
 * below PREROLL_MS so the "stay still" cue can never outlast the pre-roll. */
static inline uint32_t ind_clip_duration(IndClip c) {
    switch (c) {
        case CLIP_NONE:            return 0;
        case CLIP_PERSON_DETECTED: return 1600;
        case CLIP_PLEASE_STOP:     return 2000;
        case CLIP_SAMPLING:        return 1500;   // < PREROLL_MS (3000)
        case CLIP_ANALYZING:       return 1200;
        case CLIP_PASS:            return 1800;
        case CLIP_WARN:            return 2500;
        case CLIP_FAIL:            return 1800;
        case CLIP_SYSTEM_READY:    return 1500;
    }
    return 0;
}

static inline IndClip ind_clip_for_state(IndicatorState s) {
    switch (s) {
        case IND_PASS:            return CLIP_PASS;
        case IND_WARN:            return CLIP_WARN;
        case IND_FAIL:            return CLIP_FAIL;
        case IND_PERSON_DETECTED: return CLIP_PERSON_DETECTED;
        case IND_SAMPLING:        return CLIP_SAMPLING;
        case IND_ANALYZING:       return CLIP_ANALYZING;
        default:                  return CLIP_NONE;
    }
}

/* Which folder/file a clip number lives at. Clips are addressed by folder+file so
 * playback never depends on SD copy order. */
static inline bool ind_clip_slot(IndClip c, uint8_t* folder, uint8_t* file) {
    if (c <= CLIP_NONE || c > CLIP_SYSTEM_READY) return false;
    *folder = 1;
    *file   = (uint8_t)c;
    return true;
}

static inline const char* ind_clip_name(IndClip c) {
    switch (c) {
        case CLIP_PERSON_DETECTED: return "person_detected";
        case CLIP_PLEASE_STOP:     return "please_stop";
        case CLIP_SAMPLING:        return "sampling";
        case CLIP_ANALYZING:       return "analyzing";
        case CLIP_PASS:            return "pass";
        case CLIP_WARN:            return "warn";
        case CLIP_FAIL:            return "fail";
        case CLIP_SYSTEM_READY:    return "system_ready";
        default:                   return "none";
    }
}

class Indicator {
public:
    typedef void (*LedSink)(uint8_t r, uint8_t g, uint8_t b);
    typedef void (*ClipSink)(uint8_t clip);
    typedef void (*LogSink)(const char* msg);

    void begin(LedSink led, ClipSink clip, LogSink log, const IndConfig* cfg) {
        _led = led; _clip = clip; _log = log;
        if (cfg) _cfg = *cfg; else _cfg = ind_default_config();
        _state = IND_IDLE;
        _lastLedApplied = false;
        _runCounter = 0;
        _triggerRunId = 0;
        _hasTriggerRunId = false;
        _stateSince = 0;
        _analyzeDeadline = 0;
        _analyzeActive = false;
        _training = false;
        _audioReady = false;
        _busyClip = CLIP_NONE;
        _cooldownClip = CLIP_NONE;
        _holdActive = false;
    }

    /* Reported by the firmware's /status. False until audio init succeeds. */
    void setAudioReady(bool ready) { _audioReady = ready; }
    bool audioReady() const { return _audioReady; }

    /* TRAINING mode: no clip is ever queued and no verdict or progress colour is
     * ever shown. The system-active blue blink is kept — it is a presence signal,
     * not a judgement, so it biases nothing about the collected data. */
    void setTrainingMode(bool training) {
        _training = training;
        if (training && _state != IND_IDLE) {
            _state = IND_IDLE;
            _analyzeActive = false;
            _holdActive = false;
        }
    }
    bool trainingMode() const { return _training; }

    IndicatorState state() const { return _state; }
    const char* stateName() const { return ind_state_name(_state); }
    long runId() const { return _runCounter; }
    long triggerRunId() const { return _hasTriggerRunId ? _triggerRunId : 0; }

    /* ── Firmware-driven transitions ─────────────────────────────────────────── */

    /* Every accepted /trigger bumps the run counter and cancels a verdict hold, so a
     * new run can never inherit the previous run's colour. */
    void onTrigger(uint32_t now, long triggerId, bool hasTriggerId) {
        _runCounter++;
        _hasTriggerRunId = hasTriggerId;
        _triggerRunId = hasTriggerId ? triggerId : 0;
        _holdActive = false;
        if (_training) return;   /* no run colour in training; the blink stays on */
        _enter(IND_SAMPLING, now);
    }

    /* End of the MQ3 window: the device is now waiting on the backend. */
    void onSamplingEnd(uint32_t now) {
        if (_training) return;
        if (_state != IND_SAMPLING) return;      /* never analyse an unstarted run */
        _enter(IND_ANALYZING, now);
        _analyzeDeadline = now + _cfg.analyzeTimeoutMs;
        _analyzeActive = true;
    }

    /* ── Backend-driven transitions ──────────────────────────────────────────── */

    /* Progress states the backend may set. Verdicts are refused here on purpose:
     * they must go through submitVerdict() so they cannot skip the run-id check. */
    bool applyProgress(const char* name, uint32_t now) {
        IndicatorState want;
        if (!ind_state_parse(name, &want)) return false;
        if (ind_is_verdict(want)) return false;
        if (_training) return false;
        if (want == IND_SAMPLING || want == IND_ANALYZING) return false; /* firmware-owned */
        _enter(want, now);
        if (want == IND_IDLE) _analyzeActive = false;
        return true;
    }

    /*
     * The ONLY path to GREEN. Rejects unless every one of these holds:
     *   - deployment mode
     *   - a recognised verdict state
     *   - the device is ANALYZING
     *   - a run id was supplied and it matches this run
     *   - the verdict arrived before the analyze deadline
     * Anything else returns false with state and LEDs untouched.
     */
    bool submitVerdict(const char* status, bool hasRunId, long runId, uint32_t now) {
        IndicatorState want;
        if (!ind_state_parse(status, &want)) return false;
        if (!ind_is_verdict(want)) return false;
        if (_training) return false;
        if (_state != IND_ANALYZING) return false;
        if (!hasRunId) return false;
        if (!_runIdMatches(runId)) return false;
        /* rollover-safe "has the deadline passed": signed delta > 0 */
        if (_analyzeActive && (int32_t)(now - _analyzeDeadline) > 0) return false;
        _enter(want, now);
        _analyzeActive = false;
        _holdActive = true;
        return true;
    }

    bool _runIdMatches(long runId) const {
        if (runId == _runCounter) return true;
        if (_hasTriggerRunId && runId == _triggerRunId) return true;
        return false;
    }

    /*
     * Bench path for GET /indicator_test (.ino handler) — wiring bring-up in the
     * spirit of /relay_test, but display only. Rules:
     *   - GREEN/PASS is NEVER benchable: only submitVerdict() can paint it.
     *   - Training mode refuses everything (only the active blink shows).
     *   - A live run is never repainted: while SAMPLING or ANALYZING is showing,
     *     every bench state is refused.
     *   - Each non-idle state shows for resultHoldMs, then falls back to the blink.
     *   - Re-pressing the same state re-lights it and re-arms the hold.
     */
    bool benchState(const char* name, uint32_t now) {
        IndicatorState want;
        if (!ind_state_parse(name, &want)) return false;
        if (want == IND_PASS) return false;       /* GREEN is run-vote privileged */
        if (_training) return false;
        if (_state == IND_SAMPLING || _state == IND_ANALYZING) return false;
        if (want == _state) {                     /* re-press: re-arm, no repaint */
            _stateSince = now;
            if (want == IND_IDLE) {
                _analyzeActive = false;
                _holdActive = false;
                _applyLed(0, 0, _targetB(now), now);
            } else {
                _holdActive = true;
            }
            return true;
        }
        if (want == IND_IDLE) {
            _analyzeActive = false;
            _holdActive = false;
            _enter(IND_IDLE, now);
            return true;
        }
        _enter(want, now);
        _holdActive = true;                       /* show, then back to the blink */
        return true;
    }

    /* ── Non-blocking tick, called from loop() ───────────────────────────────── */

    void tick(uint32_t now) {
        /* Training mode never shows a run or a verdict: force the machine back to
         * idle and let the system-active blink be the only LED it renders. */
        if (_training && _state != IND_IDLE) _enter(IND_IDLE, now);

        /* Analyze deadline: never green, never stuck silent. */
        if (_state == IND_ANALYZING && _analyzeActive &&
            (uint32_t)(now - _stateSince) > _cfg.analyzeTimeoutMs) {
            _logf("indicator: analyze timeout, no verdict -> WARN");
            _analyzeActive = false;
            _enter(IND_WARN, now);
            _holdActive = true;      /* still show it, then release */
        }

        /* Verdict hold expiry. */
        if (_holdActive && _state != IND_IDLE &&
            (uint32_t)(now - _stateSince) > _cfg.resultHoldMs) {
            _holdActive = false;
            _enter(IND_IDLE, now);
        }

        /* person_detected safety net: a stale blue must not lie forever. */
        if (_state == IND_PERSON_DETECTED &&
            (uint32_t)(now - _stateSince) > _cfg.personTimeoutMs) {
            _logf("indicator: person_detected held too long, returning to idle");
            _enter(IND_IDLE, now);
        }

        /* Audio busy window expiry, so the next cue is not dropped forever. */
        if (_busyClip != CLIP_NONE && _busyElapsed(now)) _busyClip = CLIP_NONE;

        _applyLed(_targetR(now), _targetG(now), _targetB(now), now);
    }

    bool _busyElapsed(uint32_t now) const {
        return (uint32_t)(now - _busySince) > ind_clip_duration(_busyClip);
    }

    /* ── Internals ───────────────────────────────────────────────────────────── */

    void _enter(IndicatorState s, uint32_t now) {
        if (_state == s) return;
        _state = s;
        _stateSince = now;
        IndClip c = ind_clip_for_state(s);
        if (c != CLIP_NONE) _play(c, now);
        /* IDLE is never dark: resume the system-active blink immediately. */
        if (s == IND_IDLE) _applyLed(0, 0, _targetB(now), now);
    }

    /* One clip at a time. A verdict preempts; a repeat cue is dropped while the
     * previous clip is still audible or still inside its cooldown. */
    void _play(IndClip c, uint32_t now) {
        if (_training || _clip == 0 || !_audioReady) return;
        bool verdict = (c == CLIP_PASS || c == CLIP_WARN || c == CLIP_FAIL);
        if (!verdict) {
            if (_busyClip != CLIP_NONE && !_busyElapsed(now)) return;   /* still talking */
            if (c == _cooldownClip && (uint32_t)(now - _cooldownSince) < _cfg.cueCooldownMs) {
                return;
            }
        } else {
            if (_busyClip != CLIP_NONE && !_busyElapsed(now)) {
                /* preempt whatever cue is playing */
                _logf("indicator: cue preempted by verdict");
            }
        }
        _busyClip = c; _busySince = now;
        if (!verdict) { _cooldownClip = c; _cooldownSince = now; }
        _clip((uint8_t)c);
    }

    /* Triangle pulse between 25% and 100% of progressDuty. Never reaches 0, so an
     * in-progress state always reads as "working", never as "off" or a verdict. */
    uint8_t _pulse(uint32_t now, uint32_t period) const {
        if (period == 0) return _cfg.progressDuty;
        uint32_t t = now % period;
        uint32_t half = period / 2;
        if (half == 0) half = 1;
        uint32_t tri = (t < half) ? (t * 2) : ((period - t) * 2);
        uint32_t v = 25 + (75UL * tri) / (half * 2);
        uint8_t  duty = (uint8_t)((_cfg.progressDuty * v) / 100);
        return duty;
    }

    uint8_t _scale(const IndColor& c, uint8_t duty, uint8_t chan) const {
        uint8_t base = chan == 0 ? c.r : (chan == 1 ? c.g : c.b);
        return (uint8_t)((uint32_t)base * duty / 255);
    }

    void _enterColors(IndicatorState s, uint8_t& r, uint8_t& g, uint8_t& b) const {
        switch (s) {
            case IND_PASS:  r = _cfg.green.r;  g = _cfg.green.g;  b = _cfg.green.b;  break;
            case IND_WARN:  r = _cfg.yellow.r; g = _cfg.yellow.g; b = _cfg.yellow.b;  break;
            case IND_FAIL:  r = _cfg.red.r;    g = _cfg.red.g;    b = _cfg.red.b;    break;
            default:        r = 0; g = 0; b = 0; break;
        }
    }

    /* System-active idle blink: a square-wave on/off of the blue channel, shown in
     * BOTH training and deployment. Blue is the "watched / working" family, so the
     * blink can never be mistaken for a verdict. Period 0 disables it (LED dark). */
    uint8_t _idleBlink(uint32_t now) const {
        uint32_t period = _cfg.idleBlinkPeriodMs;
        if (period == 0) return 0;
        uint32_t half = period / 2 ? period / 2 : 1;
        if ((now % period) >= half) return 0;
        return _scale(_cfg.blue, _cfg.progressDuty, 2);
    }

    uint8_t _targetR(uint32_t now) const {
        if (_state == IND_IDLE) return 0;   /* blink drives blue only */
        if (_state == IND_PERSON_DETECTED || _state == IND_SAMPLING || _state == IND_ANALYZING)
            return _scale(_cfg.blue, _pulse(now, _state == IND_SAMPLING ? _cfg.fastPulseMs
                                                : _state == IND_ANALYZING ? 0UL
                                                : _cfg.slowPulseMs), 0);
        uint8_t r,g,b; _enterColors(_state, r,g,b); return r;
    }
    uint8_t _targetG(uint32_t now) const {
        if (_state == IND_IDLE) return 0;   /* blink drives blue only */
        if (_state == IND_PERSON_DETECTED || _state == IND_SAMPLING || _state == IND_ANALYZING)
            return _scale(_cfg.blue, _pulse(now, _state == IND_SAMPLING ? _cfg.fastPulseMs
                                                : _state == IND_ANALYZING ? 0UL
                                                : _cfg.slowPulseMs), 1);
        uint8_t r,g,b; _enterColors(_state, r,g,b); return g;
    }
    uint8_t _targetB(uint32_t now) const {
        if (_state == IND_IDLE) return _idleBlink(now);
        if (_state == IND_PERSON_DETECTED || _state == IND_SAMPLING || _state == IND_ANALYZING)
            return _scale(_cfg.blue, _pulse(now, _state == IND_SAMPLING ? _cfg.fastPulseMs
                                                : _state == IND_ANALYZING ? 0UL
                                                : _cfg.slowPulseMs), 2);
        uint8_t r,g,b; _enterColors(_state, r,g,b); return b;
    }

    void _applyLed(uint8_t r, uint8_t g, uint8_t b, uint32_t) {
        if (_led == 0) return;
        /* A common-anode LED sinks current, so the duty is inverted. One constant. */
        if (_cfg.commonAnode) { r = (uint8_t)(255 - r); g = (uint8_t)(255 - g); b = (uint8_t)(255 - b); }
        if (!_lastLedApplied || r != _lr || g != _lg || b != _lb) {
            _led(r, g, b);
            _lr = r; _lg = g; _lb = b; _lastLedApplied = true;
        }
    }

    void _logf(const char* msg) { if (_log) _log(msg); }

    IndConfig _cfg = ind_default_config();
    IndicatorState _state = IND_IDLE;
    LedSink  _led  = 0;
    ClipSink _clip = 0;
    LogSink  _log  = 0;

    long _runCounter = 0;
    long _triggerRunId = 0;
    bool _hasTriggerRunId = false;

    uint32_t _stateSince = 0;
    uint32_t _analyzeDeadline = 0;
    bool _analyzeActive = false;
    bool _training = false;
    bool _audioReady = false;
    bool _holdActive = false;

    IndClip _busyClip = CLIP_NONE;
    uint32_t _busySince = 0;
    IndClip _cooldownClip = CLIP_NONE;
    uint32_t _cooldownSince = 0;

    uint8_t _lr = 0, _lg = 0, _lb = 0;
    bool _lastLedApplied = false;
};

#endif /* INDICATOR_H */