// relay_sequence_test.cpp — host tests for the pure relay state machine.
//
// No test framework: plain asserts and a tiny check counter. Compile with:
//   g++ -std=c++11 -o relay_sequence_test relay_sequence_test.cpp
// Run it; it prints PASS/FAIL counts and exits non-zero on failure.

#include "../relay_sequence.h"
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iterator>
#include <string>

static int g_pass = 0, g_fail = 0;
static const char* g_group = "";

#define CHECK(cond) do {                                            \
    if (cond) { g_pass++; }                                         \
    else { g_fail++;                                                \
           std::printf("  FAIL [%s] line %d: %s\n",                 \
                       g_group, __LINE__, #cond); }                 \
} while (0)

#define GROUP(name) g_group = (name)

// ── fake hardware ────────────────────────────────────────────────────────────
struct PinLog {
    uint8_t  last[2];
    int      writes;
    int      history[512][2];
    PinLog() { std::memset(last, 0, sizeof(last)); std::memset(history, 0, sizeof(history)); writes = 0; }
};

static PinLog g_pins;

static void recordWrite(uint8_t ch, uint8_t level) {
    g_pins.last[ch] = level;
    if (g_pins.writes < 512) { g_pins.history[g_pins.writes][0] = ch; g_pins.history[g_pins.writes][1] = level; }
    g_pins.writes++;
}

static void resetPins() { g_pins = PinLog(); }

static RelaySequence::Config cfgActiveLow() {
    RelaySequence::Config c;
    c.prerollMs = 3000; c.bufferMs = 5000; c.postrollMs = 2000;
    c.maxOnMs = c.prerollMs + c.bufferMs + c.postrollMs + 2000;  // 12000
    c.fanEnabled = true; c.blowerEnabled = true; c.activeLow = true;
    return c;
}

// ── tests ────────────────────────────────────────────────────────────────────

static void test_pinsOffAtConstruction() {
    GROUP("pins off at construction");
    resetPins();
    RelaySequence rs(recordWrite, cfgActiveLow());
    // active-low: OFF level is HIGH (1)
    CHECK(g_pins.last[0] == 1);
    CHECK(g_pins.last[1] == 1);
    CHECK(rs.state() == RelaySequence::IDLE);
    CHECK(!rs.relaysOn());
    CHECK(!rs.busy());
}

static void test_startTurnsBothOnImmediately() {
    GROUP("start() turns both relays on immediately");
    resetPins();
    RelaySequence rs(recordWrite, cfgActiveLow());
    CHECK(rs.start(1000) == true);
    CHECK(rs.state() == RelaySequence::PREROLL);
    CHECK(rs.fanOn() && rs.blowerOn());
    CHECK(g_pins.last[0] == 0 && g_pins.last[1] == 0);   // active-low ON = LOW
    CHECK(std::strcmp(RelaySequence::stateName(rs.state()), "preroll") == 0);
}

static void test_samplingBlockedUntilPreRollElapses() {
    GROUP("sampling not allowed before 3000ms, allowed from 3000ms");
    resetPins();
    RelaySequence rs(recordWrite, cfgActiveLow());
    uint32_t t = 0;
    rs.start(t);
    CHECK(!rs.mayBeginSampling(t));
    rs.tick(2999);
    CHECK(!rs.mayBeginSampling(2999));
    CHECK(rs.state() == RelaySequence::PREROLL);
    rs.tick(3000);
    CHECK(rs.mayBeginSampling(3000));
    CHECK(rs.state() == RelaySequence::SAMPLING);
}

// ── the exact sequence the sketch now runs ─────────────────────────────────────
// Regression guard for a real bug: the sketch called finishSampling() from INSIDE
// its "still sampling" branch, so the very first sample pushed SAMPLING -> POSTROLL,
// mayBeginSampling() went false, the rest of the 5 s window was never sampled and the
// stuck guard cleared isBuffering. These tests pin the order the .ino must use.
static void test_full_run_samples_for_the_whole_window() {
    GROUP("sampling continues for the whole 5 s window (no early finishSampling)");
    resetPins();
    RelaySequence rs(recordWrite, cfgActiveLow());
    const uint32_t t0 = 1000;
    rs.start(t0);                       // PREROLL
    int samples = 0;
    uint32_t t = t0;
    // walk the window the way loop() does: tick, then sample only if allowed
    while (t < t0 + 3000 + 5000 + 500) {
        rs.tick(t);
        if (rs.mayBeginSampling(t)) {
            samples++;
            CHECK(rs.state() == RelaySequence::SAMPLING);   // stays SAMPLING while sampling
            if (t >= t0 + 3000 + 5000) break;               // window is over
        }
        t += 100;
    }
    CHECK(samples >= 45);                 // ~50 expected; a single sample would fail this
    CHECK(rs.state() == RelaySequence::SAMPLING);
    // only THEN does the owner close the window
    rs.finishSampling(t);
    CHECK(rs.state() == RelaySequence::POSTROLL);
    CHECK(rs.relaysOn());                 // relays stay on through the post-roll
}

static void test_state_returns_to_idle_only_after_postroll() {
    GROUP("run reaches IDLE only after POSTROLL, not during the window");
    resetPins();
    RelaySequence rs(recordWrite, cfgActiveLow());
    rs.start(0);
    rs.tick(3000);
    CHECK(rs.mayBeginSampling(3000));
    // sampling for the full window must never reach IDLE on its own
    for (uint32_t t = 3000; t <= 8000; t += 100) {
        rs.tick(t);
        CHECK(rs.state() != RelaySequence::IDLE);
    }
    rs.finishSampling(8000);
    rs.tick(9000);  CHECK(rs.state() == RelaySequence::POSTROLL);
    rs.tick(10000); CHECK(rs.state() == RelaySequence::IDLE);
}

// ── structural guard on the sketch itself ─────────────────────────────────────
// The two tests above pin RelaySequence's behaviour, but the bug that actually shipped
// was in the SKETCH: loop() called finishSampling() from inside its "still sampling"
// branch. RelaySequence is correct either way, so only a check against the real .ino
// source can catch that. This reads the shipped sketch and asserts the branch layout.
static std::string readFile(const char* path) {
    std::ifstream f(path);
    if (!f) return std::string();
    return std::string((std::istreambuf_iterator<char>(f)),
                        std::istreambuf_iterator<char>());
}

// returns the body between the brace opened at `open` and its matching close
static std::string braceBody(const std::string& s, size_t open) {
    int depth = 0;
    for (size_t i = open; i < s.size(); i++) {
        if (s[i] == '{') depth++;
        else if (s[i] == '}') {
            depth--;
            if (depth == 0) return s.substr(open + 1, i - open - 1);
        }
    }
    return std::string();
}

static void test_sketch_closes_the_window_only_after_it_elapsed() {
    GROUP("sketch: finishSampling() is NOT inside the still-sampling branch");
    // The backend runner compiles and executes this harness with a different working
    // directory, and __FILE__ may itself be relative, so try a few candidates and take
    // the first that actually opens.
    std::string here(__FILE__);
    size_t slash = here.find_last_of('/');
    const char* candidates[] = {
        "alcodetect_esp32.ino",
        "alcodetect_esp32/alcodetect_esp32.ino",
        "../alcodetect_esp32/alcodetect_esp32.ino",
        "../../alcodetect_esp32/alcodetect_esp32.ino",
        "../../../alcodetect_esp32/alcodetect_esp32.ino",
    };
    std::string src;
    for (size_t i = 0; i < sizeof(candidates) / sizeof(candidates[0]); i++) {
        src = readFile(candidates[i]);
        if (!src.empty()) break;
    }
    if (src.empty() && slash != std::string::npos) {
        src = readFile((here.substr(0, slash + 1) + "../alcodetect_esp32.ino").c_str());
    }
    CHECK(!src.empty());
    if (src.empty()) return;

    size_t anchor = src.find("if (elapsed < BUFFER_DURATION_MS) {");
    CHECK(anchor != std::string::npos);
    if (anchor == std::string::npos) return;

    size_t open = src.find('{', anchor);
    std::string samplingBranch = braceBody(src, open);

    // THE BUG: ending the window while still inside the sampling branch.
    CHECK(samplingBranch.find("finishSampling") == std::string::npos);

    // and the window-end work must live in the else branch that follows
    size_t after = open + samplingBranch.size() + 2;
    size_t elseAt = src.find("} else {", open);
    CHECK(elseAt != std::string::npos);
    if (elseAt != std::string::npos) {
        size_t eOpen = src.find('{', elseAt);
        std::string elseBranch = braceBody(src, eOpen);
        CHECK(elseBranch.find("finishSampling") != std::string::npos);
        // and it must clear isBuffering there too, or the stuck guard fires
        CHECK(elseBranch.find("isBuffering = false") != std::string::npos);
    }
    (void)after;
}

static void test_relaysStayOnThroughSamplingAndPostRoll() {
    GROUP("relays on through SAMPLING + POSTROLL, off after postroll");
    resetPins();
    RelaySequence rs(recordWrite, cfgActiveLow());
    uint32_t t = 0;
    rs.start(t);
    rs.tick(3000);                       // -> SAMPLING
    CHECK(rs.relaysOn());
    rs.tick(7999);                       // still sampling
    CHECK(rs.relaysOn());
    // the OWNER ends the window, not tick()
    CHECK(rs.state() == RelaySequence::SAMPLING);
    rs.finishSampling(8000);             // -> POSTROLL (5s window over)
    CHECK(rs.state() == RelaySequence::POSTROLL);
    CHECK(rs.relaysOn());                // still ON through post-roll
    rs.tick(9999);
    CHECK(rs.relaysOn());
    rs.tick(10000);                      // 2000 ms post-roll
    CHECK(rs.state() == RelaySequence::IDLE);
    CHECK(!rs.relaysOn());
    CHECK(g_pins.last[0] == 1 && g_pins.last[1] == 1);
}

static void test_finishSamplingExplicit() {
    GROUP("finishSampling() moves SAMPLING -> POSTROLL");
    resetPins();
    RelaySequence rs(recordWrite, cfgActiveLow());
    rs.start(0); rs.tick(3000);
    CHECK(rs.state() == RelaySequence::SAMPLING);
    rs.finishSampling(3500);
    CHECK(rs.state() == RelaySequence::POSTROLL);
    CHECK(rs.relaysOn());
    rs.tick(5500);
    CHECK(rs.state() == RelaySequence::IDLE);
    CHECK(!rs.relaysOn());
}

static void test_startRefusedWhileBusyInEveryState() {
    GROUP("start() refused in every busy state");
    resetPins();
    RelaySequence rs(recordWrite, cfgActiveLow());

    rs.start(0);              CHECK(rs.start(1) == false);   // PREROLL
    rs.tick(3000);            CHECK(rs.start(3001) == false); // SAMPLING
    rs.finishSampling(8000);  CHECK(rs.start(8001) == false); // POSTROLL
    rs.tick(10000);           CHECK(rs.state() == RelaySequence::IDLE);
    CHECK(rs.start(10001) == true);                          // idle accepts

    // let that run all the way back to IDLE, then TEST is busy too
    rs.tick(10001 + 12000);
    CHECK(rs.state() == RelaySequence::IDLE);
    CHECK(rs.test(true, true, 1000, 23000) == true);
    CHECK(rs.start(23001) == false);
    CHECK(rs.test(true, true, 1000, 23002) == false);
}

static void test_failsafeForcesOffAndIdle() {
    GROUP("failsafe forces OFF + IDLE after max_on_ms");
    resetPins();
    RelaySequence rs(recordWrite, cfgActiveLow());
    rs.start(0);
    // never finish the window properly: just tick far into the future
    rs.tick(11999);
    CHECK(rs.relaysOn());
    CHECK(rs.state() != RelaySequence::IDLE);
    rs.tick(12000);
    CHECK(rs.state() == RelaySequence::IDLE);
    CHECK(!rs.relaysOn());
    CHECK(rs.failsafeTripped());
    CHECK(g_pins.last[0] == 1 && g_pins.last[1] == 1);
}

static void test_enableFlagsDisableOnlyTheirOwnChannel() {
    GROUP("enable flags disable only their own channel");
    {
        resetPins();
        RelaySequence::Config c = cfgActiveLow();
        c.fanEnabled = false; c.blowerEnabled = true;
        RelaySequence rs(recordWrite, c);
        rs.start(0);
        CHECK(!rs.fanOn());              // logical flag respected
        CHECK(rs.blowerOn());
        CHECK(g_pins.last[0] == 1);      // fan pin stays OFF level
        CHECK(g_pins.last[1] == 0);      // blower pin ON
    }
    {
        resetPins();
        RelaySequence::Config c = cfgActiveLow();
        c.fanEnabled = true; c.blowerEnabled = false;
        RelaySequence rs(recordWrite, c);
        rs.start(0);
        CHECK(rs.fanOn());
        CHECK(!rs.blowerOn());
        CHECK(g_pins.last[0] == 0);
        CHECK(g_pins.last[1] == 1);
    }
}

static void test_bothPolarities() {
    GROUP("active-low and active-high produce the right pin levels");
    {
        resetPins();
        RelaySequence::Config c = cfgActiveLow(); c.activeLow = true;
        RelaySequence rs(recordWrite, c);
        rs.start(0);
        CHECK(g_pins.last[0] == 0 && g_pins.last[1] == 0);  // ON = LOW
        rs.tick(12000);
        CHECK(g_pins.last[0] == 1 && g_pins.last[1] == 1);  // OFF = HIGH
    }
    {
        resetPins();
        RelaySequence::Config c = cfgActiveLow(); c.activeLow = false;
        RelaySequence rs(recordWrite, c);
        rs.start(0);
        CHECK(g_pins.last[0] == 1 && g_pins.last[1] == 1);  // ON = HIGH
        rs.tick(12000);
        CHECK(g_pins.last[0] == 0 && g_pins.last[1] == 0);  // OFF = LOW
    }
}

static void test_millisRollover() {
    GROUP("millis() rollover near 2^32 behaves identically");
    const uint32_t NEAR_MAX = 0xFFFFFF00u;   // ~256 ms before rollover
    resetPins();
    RelaySequence rs(recordWrite, cfgActiveLow());
    uint32_t t = NEAR_MAX;
    rs.start(t);
    CHECK(rs.relaysOn());
    t = NEAR_MAX + 3000;                     // wraps past 2^32
    rs.tick(t);
    CHECK(rs.mayBeginSampling(t));
    CHECK(rs.state() == RelaySequence::SAMPLING);
    CHECK(rs.relaysOn());                    // relays survived the wrap
    t = t + 5000;
    rs.tick(t);
    CHECK(rs.state() == RelaySequence::SAMPLING);   // tick must not leave SAMPLING
    rs.finishSampling(t);
    CHECK(rs.state() == RelaySequence::POSTROLL);
    t = t + 2000;
    rs.tick(t);
    CHECK(rs.state() == RelaySequence::IDLE);
    CHECK(!rs.relaysOn());
    CHECK(g_pins.last[0] == 1 && g_pins.last[1] == 1);
}

static void test_relayTestBehaviour() {
    GROUP("relay test: idle-only, clamps ms, auto-offs");
    {
        resetPins();
        RelaySequence rs(recordWrite, cfgActiveLow());
        CHECK(rs.test(true, false, 2000, 0) == true);
        CHECK(rs.state() == RelaySequence::TEST);
        CHECK(rs.fanOn() && !rs.blowerOn());
        CHECK(g_pins.last[0] == 0 && g_pins.last[1] == 1);
        rs.tick(1999);  CHECK(rs.state() == RelaySequence::TEST);
        rs.tick(2000);  CHECK(rs.state() == RelaySequence::IDLE);
        CHECK(!rs.relaysOn());
    }
    {
        resetPins();
        RelaySequence rs(recordWrite, cfgActiveLow());
        rs.test(true, true, 5, 0);        // below the clamp -> 100
        CHECK(rs.testMs() == 100);
        rs.tick(10000);
    }
    {
        resetPins();
        RelaySequence rs(recordWrite, cfgActiveLow());
        rs.test(true, true, 99999, 0);    // above the clamp -> 10000
        CHECK(rs.testMs() == 10000);
        rs.tick(10000);
        CHECK(rs.state() == RelaySequence::IDLE);
    }
    {
        resetPins();
        RelaySequence rs(recordWrite, cfgActiveLow());
        rs.start(0);
        CHECK(rs.test(true, true, 1000, 1) == false);   // refused while busy
        CHECK(rs.state() == RelaySequence::PREROLL);
    }
}

// deterministic LCG so the property run is reproducible
static uint32_t g_seed = 123456789u;
static uint32_t nextRand() {
    g_seed = g_seed * 1664525u + 1013904223u;
    return (g_seed >> 8);
}

static void test_property_everyPathToIdleLeavesRelaysOff() {
    GROUP("property: every path to IDLE leaves both relays OFF");
    for (int trial = 0; trial < 300; ++trial) {
        resetPins();
        RelaySequence rs(recordWrite, cfgActiveLow());
        uint32_t t = nextRand() % 100000u;
        for (int step = 0; step < 400; ++step) {
            t += 1 + (nextRand() % 900);
            switch (nextRand() % 5) {
                case 0: rs.start(t);   break;
                case 1: rs.finishSampling(t); break;
                case 2: rs.test((nextRand() & 1) != 0, (nextRand() & 1) != 0,
                                100 + (nextRand() % 9900), t); break;
                default: break;
            }
            rs.tick(t);
            if (rs.state() == RelaySequence::IDLE) {
                CHECK(rs.fanOn() == false);
                CHECK(rs.blowerOn() == false);
                CHECK(g_pins.last[0] == 1);   // active-low OFF == HIGH
                CHECK(g_pins.last[1] == 1);
            }
        }
    }
}

// ── regression: tick() must never steal the owner's window-end decision ──────
static void test_tick_never_leaves_sampling_on_its_own() {
    GROUP("tick() alone never leaves SAMPLING (would strand the owner's buffer flag)");
    RelaySequence rs(recordWrite, cfgActiveLow());
    uint32_t t = 0;
    rs.start(t);
    rs.tick(3000);
    CHECK(rs.state() == RelaySequence::SAMPLING);

    // Hammer tick() well past the 5 s window but inside max_on_ms: it must STILL be
    // SAMPLING with the relays on, because only the owner may end the window.
    for (uint32_t k = 3001; k <= 11000; k += 997) {
        rs.tick(k);
        CHECK(rs.state() == RelaySequence::SAMPLING);
        CHECK(rs.mayBeginSampling(k));
        CHECK(rs.relaysOn());
    }
    // only the owner may move it on
    rs.finishSampling(11001);
    CHECK(rs.state() == RelaySequence::POSTROLL);
    CHECK(rs.relaysOn());
    rs.tick(13001);
    CHECK(rs.state() == RelaySequence::IDLE);
    CHECK(!rs.relaysOn());
}

static void test_failsafe_covers_an_owner_that_never_finishes() {
    GROUP("failsafe still catches an owner that never calls finish_sampling()");
    resetPins();
    RelaySequence rs(recordWrite, cfgActiveLow());
    rs.start(0);
    rs.tick(3000);
    // owner forgets to end the window -> relays must not stay on past max_on_ms
    for (uint32_t k = 3001; k <= 11500; k += 1000) rs.tick(k);
    CHECK(rs.relaysOn());
    rs.tick(12000);
    CHECK(rs.state() == RelaySequence::IDLE);
    CHECK(!rs.relaysOn());
    CHECK(rs.failsafeTripped());
    CHECK(g_pins.last[0] == 1 && g_pins.last[1] == 1);
}

int main() {
    test_pinsOffAtConstruction();
    test_startTurnsBothOnImmediately();
    test_samplingBlockedUntilPreRollElapses();
    test_tick_never_leaves_sampling_on_its_own();
    test_failsafe_covers_an_owner_that_never_finishes();
    test_full_run_samples_for_the_whole_window();
    test_state_returns_to_idle_only_after_postroll();
    test_sketch_closes_the_window_only_after_it_elapsed();
    test_relaysStayOnThroughSamplingAndPostRoll();
    test_finishSamplingExplicit();
    test_startRefusedWhileBusyInEveryState();
    test_failsafeForcesOffAndIdle();
    test_enableFlagsDisableOnlyTheirOwnChannel();
    test_bothPolarities();
    test_millisRollover();
    test_relayTestBehaviour();
    test_property_everyPathToIdleLeavesRelaysOff();

    std::printf("\nrelay_sequence_test: %d passed, %d failed\n", g_pass, g_fail);
    return g_fail == 0 ? 0 : 1;
}
