// relay_sequence.h — pure, testable relay state machine for the AlcoDetect ESP32.
//
// Deliberately free of Arduino: no Arduino.h, no millis(), no digitalWrite, no delay().
// Time is injected as `now_ms` on every call and pin writes go through an injected
// callback, so the whole sequence can be exercised on a host with a fake clock.
//
// WHY A PRE-ROLL EXISTS
//   Airflow across the MQ3 sensors changes what they read. The fans must therefore be
//   running BEFORE the first sample is taken, and must keep running for a moment
//   after the last one so the reading settles. The sampling window itself is
//   unchanged; it simply starts at the end of the pre-roll.
//
// STATE DIAGRAM
//
//            start(now)
//   IDLE ─────────────────────► PREROLL ──(preroll_ms elapsed)──► SAMPLING
//     ▲                            │                                  │
//     │                            │ failsafe                        │ (buffer_ms elapsed)
//     │                            ▼                                  ▼
//     │                          IDLE ◄── failsafe ────────────── POSTROLL
//     │                                                               │
//     └────────────────────── (postroll_ms elapsed) ──────────────────┘
//
//            test(fan, blower, ms, now)
//   IDLE ─────────────────────► TEST ─────── (ms elapsed) ──────────► IDLE
//
//   Any state ── (max_on_ms exceeded while relays on) ──► IDLE, relays forced OFF.
//
// INVARIANT: whenever state() == IDLE, both pins are at their OFF level. Every
//            transition into IDLE drives the OFF level, including the failsafe.
//
// Both channels switch together and share the same pre-roll, so training and
// deployment mode run an identical fan sequence. There is no mode-dependent timing.

#ifndef RELAY_SEQUENCE_H
#define RELAY_SEQUENCE_H

#include <stdint.h>

class RelaySequence {
public:
    enum State { IDLE, PREROLL, SAMPLING, POSTROLL, TEST };

    // channel: 0 = fan, 1 = blower. level: 1 = HIGH, 0 = LOW (already polarity-corrected).
    typedef void (*PinWriteFn)(uint8_t channel, uint8_t level);

    struct Config {
        uint32_t prerollMs;
        uint32_t bufferMs;
        uint32_t postrollMs;
        uint32_t maxOnMs;
        bool     fanEnabled;
        bool     blowerEnabled;
        bool     activeLow;
        Config()
            : prerollMs(3000), bufferMs(5000), postrollMs(2000), maxOnMs(12000),
              fanEnabled(true), blowerEnabled(true), activeLow(true) {}
    };

    RelaySequence(PinWriteFn write, const Config& cfg)
        : _write(write), _cfg(cfg), _state(IDLE), _stateStart(0), _relaysOnAt(0),
          _fanOn(false), _blowerOn(false), _testMs(0),
          _pinFan(0), _pinBlower(0), _failsafe(false), _hasPinState(false) {
        driveOff();   // relays are OFF the moment the object exists
    }

    // ── queries ────────────────────────────────────────────────────────────
    State state() const { return _state; }
    bool  busy() const { return _state != IDLE; }
    bool  relaysOn() const { return _fanOn || _blowerOn; }
    // Reported state includes the enable flags: a disabled channel is never "on",
    // which is also what /status must report.
    bool  fanOn() const { return _fanOn && _cfg.fanEnabled; }
    bool  blowerOn() const { return _blowerOn && _cfg.blowerEnabled; }
    bool  failsafeTripped() const { return _failsafe; }
    uint32_t testMs() const { return _testMs; }

    static const char* stateName(State s) {
        switch (s) {
            case IDLE:     return "idle";
            case PREROLL:  return "preroll";
            case SAMPLING: return "sampling";
            case POSTROLL: return "postroll";
            case TEST:     return "test";
        }
        return "idle";
    }

    // Sampling may begin only once the pre-roll has elapsed. Samples taken earlier
    // must never reach the buffer.
    bool mayBeginSampling(uint32_t now_ms) const {
        return _state == SAMPLING;
    }

    // ── events ─────────────────────────────────────────────────────────────

    // Accepted only in IDLE. Turns both relays on immediately and starts the pre-roll.
    bool start(uint32_t now_ms) {
        if (busy()) return false;
        _failsafe = false;
        _state = PREROLL;
        _stateStart = now_ms;
        _relaysOnAt = now_ms;
        _testMs = 0;
        _fanOn = true;
        _blowerOn = true;
        applyOutputs(now_ms);
        return true;
    }

    // Explicit "sampling window finished" event. tick() also performs this transition
    // on its own once bufferMs elapses; calling it lets a caller finish early.
    void finishSampling(uint32_t now_ms) {
        if (_state != SAMPLING) return;
        _state = POSTROLL;
        _stateStart = now_ms;
        applyOutputs(now_ms);
    }

    // Bench/wiring check. Idle-only. `ms` is clamped to [100, 10000].
    bool test(bool fan, bool blower, uint32_t ms, uint32_t now_ms) {
        if (busy()) return false;
        if (ms < 100) ms = 100;
        if (ms > 10000) ms = 10000;
        _failsafe = false;
        _state = TEST;
        _stateStart = now_ms;
        _relaysOnAt = now_ms;
        _testMs = ms;
        _fanOn = fan;
        _blowerOn = blower;
        applyOutputs(now_ms);
        return true;
    }

    // Advances the machine. Safe to call as often as loop() likes.
    void tick(uint32_t now_ms) {
        uint32_t elapsed = now_ms - _stateStart;   // unsigned: rollover-safe

        switch (_state) {
            case PREROLL:
                if (elapsed >= _cfg.prerollMs) {
                    _state = SAMPLING;
                    _stateStart = now_ms;
                }
                break;
            case SAMPLING:
                if (elapsed >= _cfg.bufferMs) {
                    _state = POSTROLL;
                    _stateStart = now_ms;
                }
                break;
            case POSTROLL:
                if (elapsed >= _cfg.postrollMs) {
                    toIdle();
                }
                break;
            case TEST:
                if (elapsed >= _testMs) {
                    toIdle();
                }
                break;
            case IDLE:
                break;
        }

        // Failsafe: however we got here, the relays must not stay on.
        if (relaysOn() && (uint32_t)(now_ms - _relaysOnAt) >= _cfg.maxOnMs) {
            _failsafe = true;
            toIdle();
        }
        applyOutputs(now_ms);
    }

private:
    void toIdle() {
        _state = IDLE;
        _stateStart = 0;
        _testMs = 0;
        _fanOn = false;
        _blowerOn = false;
        _relaysOnAt = 0;
        driveOff();
    }

    // desired logical state -> actual pin level, honouring polarity and enables
    void applyOutputs(uint32_t now_ms) {
        (void)now_ms;
        bool fan = _fanOn && _cfg.fanEnabled;
        bool blower = _blowerOn && _cfg.blowerEnabled;
        uint8_t fanLevel = levelFor(fan);
        uint8_t blowerLevel = levelFor(blower);
        if (!_hasPinState || fanLevel != _pinFan || blowerLevel != _pinBlower) {
            if (_write) {
                _write(0, fanLevel);
                _write(1, blowerLevel);
            }
            _pinFan = fanLevel;
            _pinBlower = blowerLevel;
            _hasPinState = true;
        }
    }

    void driveOff() {
        _fanOn = false;
        _blowerOn = false;
        uint8_t off = levelFor(false);
        if (_write) {
            _write(0, off);
            _write(1, off);
        }
        _pinFan = off;
        _pinBlower = off;
        _hasPinState = true;
    }

    uint8_t levelFor(bool logicalOn) const {
        // active-low modules energise on LOW
        if (_cfg.activeLow) return logicalOn ? 0 : 1;
        return logicalOn ? 1 : 0;
    }

    PinWriteFn  _write;
    Config      _cfg;
    State       _state;
    uint32_t    _stateStart;
    uint32_t    _relaysOnAt;
    bool        _fanOn;
    bool        _blowerOn;
    uint32_t    _testMs;
    uint8_t     _pinFan;
    uint8_t     _pinBlower;
    bool        _failsafe;
    bool        _hasPinState;
};

#endif  // RELAY_SEQUENCE_H
