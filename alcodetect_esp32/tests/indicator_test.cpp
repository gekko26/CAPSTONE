// indicator_test.cpp — host tests for the deployment indicator state machine.
//
// No test framework: plain asserts and a tiny check counter. Compile with:
//   g++ -std=c++11 -Wall -I. -o indicator_test indicator_test.cpp
// Run it; it prints PASS/FAIL counts and exits non-zero on failure.
//
// These tests drive the SHIPPED indicator.h and dfplayer.h — no reimplementation.

#include "../indicator.h"
#include "../dfplayer.h"
#include <cstdio>
#include <cstring>

static int g_pass = 0, g_fail = 0;
static const char* g_group = "";

#define CHECK(cond) do {                                            \
    if (cond) { g_pass++; }                                         \
    else { g_fail++;                                                \
           std::printf("  FAIL [%s] line %d: %s\n",                 \
                       g_group, __LINE__, #cond); }                 \
} while (0)

#define GROUP(name) g_group = (name)

// ── fake hardware sinks ───────────────────────────────────────────────────────
struct Sink {
    uint8_t r, g, b;
    int     ledWrites;
    int     clips;
    uint8_t lastClip;
    int     clipLog[64];
    Sink() { r = g = b = 0; ledWrites = 0; clips = 0; lastClip = 0;
             std::memset(clipLog, 0, sizeof(clipLog)); }
};
static Sink g_sink;

static void fakeLed(uint8_t r, uint8_t g, uint8_t b) {
    g_sink.r = r; g_sink.g = g; g_sink.b = b; g_sink.ledWrites++;
}
static void fakeClip(uint8_t clip) {
    g_sink.lastClip = clip; g_sink.clips++;
    if (g_sink.clips < 64) g_sink.clipLog[g_sink.clips - 1] = clip;
}

static IndConfig cfg() { return ind_default_config(); }

// A fully wired indicator with audio ready, training off.
static void makeUp(Indicator& ind, bool audioReady = true) {
    g_sink = Sink();
    IndConfig c = cfg();
    ind.begin(fakeLed, fakeClip, 0, &c);
    ind.setTrainingMode(false);
    ind.setAudioReady(audioReady);
}

// Walk the device through: trigger -> window ends -> ANALYZING.
static void driveToAnalyzing(Indicator& ind, uint32_t t0, long triggerId = 0,
                             bool hasTriggerId = false) {
    ind.onTrigger(t0, triggerId, hasTriggerId);
    ind.onSamplingEnd(t0 + 5000);
}

// ── 1. boot state ─────────────────────────────────────────────────────────────
static void test_boot_state_is_off_and_silent() {
    GROUP("boot state is OFF and silent");
    Indicator ind;
    g_sink = Sink();
    IndConfig c = cfg();
    ind.begin(fakeLed, fakeClip, 0, &c);
    ind.setAudioReady(true);
    CHECK(ind.state() == IND_IDLE);
    CHECK(g_sink.ledWrites == 0);          // nothing driven at boot
    CHECK(g_sink.clips == 0);              // nothing spoken at boot
    ind.tick(0);
    CHECK(ind.state() == IND_IDLE);
    CHECK(g_sink.r == 0 && g_sink.g == 0 && g_sink.b == 0);
}

// ── 2. GREEN only via submitVerdict with a matching run id while ANALYZING ─────
static void test_green_requires_matching_run_id_while_analyzing() {
    GROUP("GREEN requires matching run id while ANALYZING");
    Indicator ind; makeUp(ind);
    driveToAnalyzing(ind, 1000, 0, false);
    CHECK(ind.state() == IND_ANALYZING);
    long run = ind.runId();
    CHECK(run > 0);
    CHECK(ind.submitVerdict("pass", true, run, 7000));
    CHECK(ind.state() == IND_PASS);
    ind.tick(7100);
    CHECK(g_sink.g > 0 && g_sink.r == 0 && g_sink.b == 0);   // pure green
}

static void test_green_refused_while_not_analyzing() {
    GROUP("GREEN refused while not ANALYZING");
    Indicator ind; makeUp(ind);
    long run = ind.runId();
    // straight from idle — never green
    CHECK(!ind.submitVerdict("pass", true, run + 1, 1000));
    CHECK(ind.state() == IND_IDLE);
    // from person_detected — never green
    ind.applyProgress("person_detected", 1000);
    CHECK(!ind.submitVerdict("pass", true, run + 1, 1100));
    CHECK(ind.state() == IND_PERSON_DETECTED);
}

static void test_green_refused_for_wrong_or_missing_run_id() {
    GROUP("GREEN refused for wrong or missing run id");
    Indicator ind; makeUp(ind);
    driveToAnalyzing(ind, 1000, 0, false);
    ind.tick(7000);                       // let the ANALYZING blue settle first
    long run = ind.runId();
    uint8_t r0 = g_sink.r, g0 = g_sink.g, b0 = g_sink.b;

    CHECK(!ind.submitVerdict("pass", false, 0, 7000));        // no run id at all
    CHECK(!ind.submitVerdict("pass", true, run + 99, 7000));   // wrong run id
    CHECK(!ind.submitVerdict("pass", true, run - 1, 7000));    // stale earlier run
    CHECK(ind.state() == IND_ANALYZING);                      // unchanged
    ind.tick(7050);
    CHECK(g_sink.r == r0 && g_sink.g == g0 && g_sink.b == b0); // LEDs untouched
}

static void test_green_accepted_when_run_id_matches_trigger_id() {
    GROUP("GREEN accepted when run id matches the trigger's reading_id");
    Indicator ind; makeUp(ind);
    driveToAnalyzing(ind, 1000, 4242, true);
    // the backend knows the reading_id for certain, so it is accepted too
    CHECK(ind.submitVerdict("pass", true, 4242, 7000));
    CHECK(ind.state() == IND_PASS);
}

// ── 3. replay / stale verdicts ────────────────────────────────────────────────
static void test_replayed_verdict_is_rejected_after_a_new_run() {
    GROUP("a verdict from the previous run cannot light the next one");
    Indicator ind; makeUp(ind);
    driveToAnalyzing(ind, 1000, 0, false);
    long first = ind.runId();
    CHECK(ind.submitVerdict("pass", true, first, 7000));
    // a new trigger starts a new run
    ind.onTrigger(20000, 0, false);
    long second = ind.runId();
    CHECK(second != first);
    ind.onSamplingEnd(25000);
    // replay the OLD verdict
    CHECK(!ind.submitVerdict("pass", true, first, 26000));
    CHECK(ind.state() == IND_ANALYZING);
}

static void test_unknown_state_name_never_produces_a_colour() {
    GROUP("unknown state names leave the state and LEDs alone");
    Indicator ind; makeUp(ind);
    driveToAnalyzing(ind, 1000, 0, false);
    ind.tick(7000);                       // let the ANALYZING blue settle first
    uint8_t r0 = g_sink.r, g0 = g_sink.g, b0 = g_sink.b;
    long run = ind.runId();
    IndicatorState out;
    CHECK(!ind_state_parse("green", &out));
    CHECK(!ind_state_parse("PASS", &out));
    CHECK(!ind_state_parse("", &out));
    CHECK(!ind_state_parse(0, &out));
    CHECK(!ind.submitVerdict("green", true, run, 7000));
    CHECK(!ind.applyProgress("purple", 7000));
    CHECK(ind.state() == IND_ANALYZING);
    ind.tick(7050);
    CHECK(g_sink.r == r0 && g_sink.g == g0 && g_sink.b == b0);
}

// ── 4. timeout ────────────────────────────────────────────────────────────────
static void test_timeout_produces_yellow_never_green() {
    GROUP("no verdict in time -> YELLOW, never green, never stuck");
    Indicator ind; makeUp(ind);
    driveToAnalyzing(ind, 1000, 0, false);
    const uint32_t aStart = 6000;   // ANALYZING begins when the 5 s window closes
    ind.tick(aStart + IND_ANALYZE_TIMEOUT_MS - 10);
    CHECK(ind.state() == IND_ANALYZING);          // not yet
    ind.tick(aStart + IND_ANALYZE_TIMEOUT_MS + 10);
    CHECK(ind.state() == IND_WARN);
    ind.tick(aStart + IND_ANALYZE_TIMEOUT_MS + 20);
    CHECK(g_sink.g > 0 && g_sink.r > 0 && g_sink.b == 0);   // red+green = yellow
    // the WARN clip must have been queued
    bool playedWarn = false;
    for (int i = 0; i < g_sink.clips && i < 64; i++)
        if (g_sink.clipLog[i] == CLIP_WARN) playedWarn = true;
    CHECK(playedWarn);
    // and a late verdict can no longer turn it green
    long run = ind.runId();
    CHECK(!ind.submitVerdict("pass", true, run, aStart + IND_ANALYZE_TIMEOUT_MS + 30));
    CHECK(ind.state() == IND_WARN);
}

static void test_verdict_after_deadline_is_refused() {
    GROUP("a verdict arriving after the deadline is refused");
    Indicator ind; makeUp(ind);
    driveToAnalyzing(ind, 1000, 0, false);
    long run = ind.runId();
    // the deadline is 20 s after the window closed at t=6000
    CHECK(!ind.submitVerdict("pass", true, run, 6000 + IND_ANALYZE_TIMEOUT_MS + 1));
    CHECK(ind.state() != IND_PASS);
}

// ── 5. hold and cancellation ──────────────────────────────────────────────────
static void test_verdict_hold_expires_to_idle() {
    GROUP("verdict holds for IND_RESULT_HOLD_MS then returns to idle");
    Indicator ind; makeUp(ind);
    driveToAnalyzing(ind, 1000, 0, false);
    ind.submitVerdict("fail", true, ind.runId(), 7000);
    CHECK(ind.state() == IND_FAIL);
    ind.tick(7000 + IND_RESULT_HOLD_MS - 50);
    CHECK(ind.state() == IND_FAIL);              // still held
    ind.tick(7000 + IND_RESULT_HOLD_MS + 50);
    CHECK(ind.state() == IND_IDLE);
    CHECK(g_sink.r == 0 && g_sink.g == 0 && g_sink.b == 0);
}

static void test_new_trigger_cancels_a_hold() {
    GROUP("a new /trigger cancels a verdict hold");
    Indicator ind; makeUp(ind);
    driveToAnalyzing(ind, 1000, 0, false);
    ind.submitVerdict("fail", true, ind.runId(), 7000);
    CHECK(ind.state() == IND_FAIL);
    ind.onTrigger(7200, 0, false);
    CHECK(ind.state() == IND_SAMPLING);         // hold gone, new run showing
    ind.tick(7250);
    CHECK(g_sink.r == 0 && g_sink.g == 0);      // no red left showing
}

// ── 6. progress states are blue only ──────────────────────────────────────────
static void test_progress_states_are_never_red_green_or_yellow() {
    GROUP("in-progress states are BLUE only");
    const uint32_t probes[] = {0, 100, 400, 799, 800, 1200, 1600, 2400, 3200};
    // person_detected — slow pulse
    for (int i = 0; i < 9; i++) {
        Indicator ind; makeUp(ind);
        ind.applyProgress("person_detected", 0);
        CHECK(ind.state() == IND_PERSON_DETECTED);
        ind.tick(probes[i]);
        CHECK(g_sink.r == 0);
        CHECK(g_sink.b > 0);
    }
    // sampling — fast pulse
    for (int i = 0; i < 9; i++) {
        Indicator ind; makeUp(ind);
        ind.onTrigger(0, 0, false);
        CHECK(ind.state() == IND_SAMPLING);
        ind.tick(probes[i]);
        CHECK(g_sink.r == 0 && g_sink.g == 0);
        CHECK(g_sink.b > 0);
    }
    // analyzing — steady blue (its clock starts at t0+5000, so probe from there)
    Indicator ind; makeUp(ind);
    driveToAnalyzing(ind, 0, 0, false);
    for (int i = 0; i < 9; i++) {
        ind.tick(5000 + probes[i]);
        CHECK(ind.state() == IND_ANALYZING);
        CHECK(g_sink.r == 0 && g_sink.g == 0 && g_sink.b > 0);
    }
}

static void test_yellow_is_red_plus_reduced_green() {
    // YELLOW balances CURRENT, not duty.
    //
    // Red's Vf (2.0 V) is far below green's (3.1 V) on a 3.3 V rail, so at equal duty
    // red always runs much hotter: with the documented 330 ohm red and 100 ohm green
    // that is 3.94 mA against 2.00 mA -- a 1.97:1 mix that reads ORANGE.
    //
    // This used to pull the GREEN duty down to 190/255 to try to correct that. That is
    // backwards: it reduced the already-weaker channel further, giving 0.68 mA against
    // 3.94 mA -- a 5.8:1 ratio, i.e. MORE orange, not less. WARN was then hard to tell
    // from FAIL, which is a gate-presentation failure.
    //
    // The fix reduces the RED duty instead, so the two currents match. Recompute if the
    // channel resistors change: yellow.r = 255 * (I_green / I_red).
    GROUP("YELLOW balances current by reducing RED duty");
    IndConfig c = cfg();
    CHECK(c.yellow.r > 0 && c.yellow.r < c.red.r);     // red pulled down
    CHECK(c.yellow.g == c.green.g);                    // green stays at full
    CHECK(c.yellow.b == 0);
    // The whole point: the resulting currents must be within ~15% of each other.
    {
        const float i_red   = (3.3f - 2.0f) / 330.0f * 1000.0f;   // mA per LED
        const float i_green = (3.3f - 3.1f) / 100.0f * 1000.0f;
        const float eff_red   = i_red   * c.yellow.r / 255.0f;
        const float eff_green = i_green * c.yellow.g / 255.0f;
        const float ratio = eff_red / eff_green;
        CHECK(ratio > 0.85f && ratio < 1.18f);   // balanced => reads yellow, not orange
    }
    Indicator ind; makeUp(ind);
    driveToAnalyzing(ind, 1000, 0, false);
    ind.submitVerdict("warn", true, ind.runId(), 7000);
    ind.tick(7100);
    CHECK(g_sink.r == 129 && g_sink.b == 0);
    CHECK(g_sink.g == 255);
}

// ── 7. training mode ──────────────────────────────────────────────────────────
static void test_training_mode_is_off_and_silent() {
    GROUP("TRAINING mode: LEDs off, no clips, verdicts refused");
    Indicator ind; makeUp(ind);
    ind.setTrainingMode(true);
    ind.applyProgress("person_detected", 1000);
    CHECK(ind.state() == IND_IDLE);                 // refused
    CHECK(g_sink.r == 0 && g_sink.g == 0 && g_sink.b == 0);
    CHECK(g_sink.clips == 0);
    ind.onTrigger(1100, 0, false);
    CHECK(ind.state() == IND_IDLE);                 // no sampling colour
    ind.onSamplingEnd(6100);
    CHECK(ind.state() == IND_IDLE);
    ind.tick(20000);
    CHECK(g_sink.r == 0 && g_sink.g == 0 && g_sink.b == 0);
    // a verdict is refused outright
    CHECK(!ind.submitVerdict("pass", true, 1, 21000));
    CHECK(ind.state() == IND_IDLE);
    CHECK(g_sink.g == 0);                            // never green
}

static void test_leaving_training_mode_restores_normal_behaviour() {
    GROUP("leaving TRAINING mode restores normal behaviour");
    Indicator ind; makeUp(ind);
    ind.setTrainingMode(true);
    ind.applyProgress("person_detected", 1000);
    CHECK(ind.state() == IND_IDLE);
    ind.setTrainingMode(false);
    CHECK(ind.applyProgress("person_detected", 2000));
    CHECK(ind.state() == IND_PERSON_DETECTED);
}

// ── 8. audio policy ───────────────────────────────────────────────────────────
static void test_verdict_clip_preempts_a_playing_cue() {
    GROUP("a verdict clip preempts whatever cue is playing");
    Indicator ind; makeUp(ind);
    ind.applyProgress("person_detected", 1000);       // queues CLIP_PERSON_DETECTED
    CHECK(g_sink.clips == 1);
    driveToAnalyzing(ind, 1100, 0, false);
    CHECK(g_sink.clips == 2);                          // CLIP_SAMPLING
    // still inside the sampling clip's duration -> the verdict must preempt
    CHECK(ind.submitVerdict("fail", true, ind.runId(), 1600));
    CHECK(g_sink.clips == 3);
    CHECK(g_sink.lastClip == CLIP_FAIL);
}

static void test_cue_cooldown_stops_repeat_person_detected() {
    GROUP("cue cooldown stops 'person detected' repeating");
    Indicator ind; makeUp(ind);
    ind.applyProgress("person_detected", 1000);
    CHECK(g_sink.clips == 1);
    // person steps away and comes back inside the cooldown
    ind.applyProgress("idle", 2000);
    ind.applyProgress("person_detected", 3000);
    CHECK(g_sink.clips == 1);                          // suppressed by cooldown
    // after the cooldown it plays again
    ind.applyProgress("idle", 3000 + IND_CUE_COOLDOWN_MS + 100);
    ind.applyProgress("person_detected", 3100 + IND_CUE_COOLDOWN_MS + 100);
    CHECK(g_sink.clips == 2);
}

static void test_person_detected_safety_net_returns_to_idle() {
    GROUP("a stale person_detected cannot lie forever");
    Indicator ind; makeUp(ind);
    ind.applyProgress("person_detected", 1000);
    ind.tick(1000 + IND_PERSON_HOLD_MS - 10);
    CHECK(ind.state() == IND_PERSON_DETECTED);
    ind.tick(1000 + IND_PERSON_HOLD_MS + 10);
    CHECK(ind.state() == IND_IDLE);
    ind.tick(1000 + IND_PERSON_HOLD_MS + 20);
    CHECK(g_sink.r == 0 && g_sink.g == 0 && g_sink.b == 0);
}

// ── 9. absent audio never blocks the loop ─────────────────────────────────────
static void test_absent_audio_module_never_blocks() {
    GROUP("audio_ready=false never blocks and never speaks");
    Indicator ind; makeUp(ind, false);                 // DFPlayer absent
    CHECK(!ind.audioReady());
    ind.applyProgress("person_detected", 1000);
    CHECK(g_sink.clips == 0);                          // nothing queued
    driveToAnalyzing(ind, 1100, 0, false);
    CHECK(ind.state() == IND_ANALYZING);
    CHECK(ind.submitVerdict("pass", true, ind.runId(), 7000));
    CHECK(ind.state() == IND_PASS);                    // the LED still works
    CHECK(g_sink.clips == 0);                          // but nothing was spoken
    ind.tick(7100);
    CHECK(g_sink.g > 0);
}

static void test_audio_becoming_ready_enables_clips() {
    GROUP("clips start once audio reports ready");
    Indicator ind; makeUp(ind, false);
    ind.applyProgress("person_detected", 1000);
    CHECK(g_sink.clips == 0);
    ind.setAudioReady(true);
    ind.applyProgress("idle", 2000);
    ind.applyProgress("person_detected", 2000 + IND_CUE_COOLDOWN_MS + 100);
    CHECK(g_sink.clips == 1);
}

// ── 10. clip slots map to the SD layout ───────────────────────────────────────
static void test_clip_slots_match_sd_layout() {
    GROUP("clip slots are folder 1 / file = clip number");
    uint8_t f, n;
    CHECK(ind_clip_slot(CLIP_PERSON_DETECTED, &f, &n) && f == 1 && n == 1);
    CHECK(ind_clip_slot(CLIP_PASS, &f, &n) && f == 1 && n == 5);
    CHECK(ind_clip_slot(CLIP_WARN, &f, &n) && f == 1 && n == 6);
    CHECK(ind_clip_slot(CLIP_FAIL, &f, &n) && f == 1 && n == 7);
    CHECK(ind_clip_slot(CLIP_SYSTEM_READY, &f, &n) && f == 1 && n == 8);
    CHECK(!ind_clip_slot(CLIP_NONE, &f, &n));
    // the sampling cue must never outlast the 3 s pre-roll
    CHECK(ind_clip_duration(CLIP_SAMPLING) < 3000);
    for (int c = 1; c <= 8; c++)
        CHECK(ind_clip_duration((IndClip)c) <= 3000);
}

// ── 11. DFPlayer frames ───────────────────────────────────────────────────────
static void test_dfplayer_frame_bytes_and_checksum() {
    GROUP("DFPlayer frame layout and checksum");

    // Hand-computed: play folder 01 file 001 (clip 001)
    //   bytes[1..6] = FF 06 0F 01 01 01
    //   sum         = 255 + 6 + 15 + 1 + 1 + 1 = 279
    //   checksum    = (0 - 279) mod 65536 = 65257 = 0xFEE9
    //                 (65257 - 254*256 = 233 = 0xE9, so the low byte is E9)
    //   full frame  = 7E FF 06 0F 01 01 01 FE E9 EF
    const uint8_t expect_play001[10] =
        {0x7E, 0xFF, 0x06, 0x0F, 0x01, 0x01, 0x01, 0xFE, 0xE9, 0xEF};
    uint8_t f[10];
    CHECK(dfplayer_frame_play(f, 1, 1) == DFPLAYER_FRAME_LEN);
    for (int i = 0; i < 10; i++) CHECK(f[i] == expect_play001[i]);

    // Structural checks on every frame we emit.
    struct Case { const char* name; uint8_t bytes[10]; };
    Case cases[6];
    CHECK(dfplayer_frame_reset(f)     == DFPLAYER_FRAME_LEN);   std::memcpy(cases[0].bytes, f, 10); cases[0].name = "reset";
    CHECK(dfplayer_frame_select_sd(f) == DFPLAYER_FRAME_LEN);  std::memcpy(cases[1].bytes, f, 10); cases[1].name = "select_sd";
    CHECK(dfplayer_frame_volume(f, 18) == DFPLAYER_FRAME_LEN);  std::memcpy(cases[2].bytes, f, 10); cases[2].name = "volume18";
    CHECK(dfplayer_frame_stop(f)      == DFPLAYER_FRAME_LEN);   std::memcpy(cases[3].bytes, f, 10); cases[3].name = "stop";
    CHECK(dfplayer_frame_play(f, 1, 5) == DFPLAYER_FRAME_LEN);  std::memcpy(cases[4].bytes, f, 10); cases[4].name = "play_005";
    CHECK(dfplayer_frame_play(f, 1, 8) == DFPLAYER_FRAME_LEN);  std::memcpy(cases[5].bytes, f, 10); cases[5].name = "play_008";

    for (int i = 0; i < 6; i++) {
        const uint8_t* c = cases[i].bytes;
        CHECK(c[0] == 0x7E);                       // start
        CHECK(c[1] == 0xFF);                       // second fixed byte
        CHECK(c[2] == 0x06);                       // length
        CHECK(c[4] == 0x01);                       // feedback
        CHECK(c[9] == 0xEF);                       // end
        uint16_t ck = dfplayer_checksum_of(c);
        CHECK(c[7] == (uint8_t)(ck >> 8));         // checksum high
        CHECK(c[8] == (uint8_t)(ck & 0xFF));        // checksum low
    }
    // specific command bytes
    CHECK(cases[0].bytes[3] == DF_CMD_RESET);
    CHECK(cases[1].bytes[3] == DF_CMD_SELECT_SD);
    CHECK(cases[1].bytes[5] == DF_PARAM_SD_CARD);
    CHECK(cases[2].bytes[3] == DF_CMD_VOLUME);
    CHECK(cases[2].bytes[5] == 18);                // volume in the high byte
    CHECK(cases[3].bytes[3] == DF_CMD_STOP);
    CHECK(cases[4].bytes[3] == DF_CMD_PLAY_FOLDER);
    CHECK(cases[4].bytes[5] == 1);                 // folder
    CHECK(cases[4].bytes[6] == 5);                 // file
}

static void test_dfplayer_rejects_out_of_range_arguments() {
    GROUP("DFPlayer refuses impossible folders/files/volume");
    uint8_t f[10];
    CHECK(dfplayer_frame_play(f, 0, 1)  == 0);   // folder 0 does not exist
    CHECK(dfplayer_frame_play(f, 100, 1) == 0);
    CHECK(dfplayer_frame_play(f, 1, 0)  == 0);   // file 0 does not exist
    CHECK(dfplayer_frame_play(f, 1, 100) == 0);
    CHECK(dfplayer_frame_volume(f, 31) == 0);    // above DF_VOLUME_MAX
    CHECK(dfplayer_frame_volume(f, 30) == DFPLAYER_FRAME_LEN);
    CHECK(dfplayer_frame_volume(f, 0)  == DFPLAYER_FRAME_LEN);
    CHECK(dfplayer_build(0, DF_CMD_STOP, 1, 0, 0) == 0);   // null out buffer
}

// ── 12. common-anode inversion ────────────────────────────────────────────────
static void test_common_anode_inverts_duty() {
    GROUP("common-anode inverts the duty at the single constant");
    IndConfig cc = cfg();
    cc.commonAnode = false;
    Indicator a; g_sink = Sink(); a.begin(fakeLed, fakeClip, 0, &cc);
    a.setAudioReady(true);
    driveToAnalyzing(a, 1000, 0, false);
    a.submitVerdict("pass", true, a.runId(), 7000);
    a.tick(7100);
    CHECK(g_sink.g == 255);

    IndConfig ca = cfg();
    ca.commonAnode = true;
    Indicator b; g_sink = Sink(); b.begin(fakeLed, fakeClip, 0, &ca);
    b.setAudioReady(true);
    driveToAnalyzing(b, 1000, 0, false);
    b.submitVerdict("pass", true, b.runId(), 7000);
    b.tick(7100);
    // green brightness (0,255,0) inverted at the pin = (255,0,255):
    //   red anode HIGH   -> red off
    //   green anode LOW  -> green ON
    //   blue anode HIGH  -> blue off
    CHECK(g_sink.r == 255);        // red channel driven OFF
    CHECK(g_sink.g == 0);          // green channel driven ON (sinking)
    CHECK(g_sink.b == 255);        // blue channel driven OFF
    // idle means "LED dark", which under common anode is every pin HIGH
    b.tick(7100 + IND_RESULT_HOLD_MS + 50);
    CHECK(g_sink.r == 255 && g_sink.g == 255 && g_sink.b == 255);
}

// ── 13. run counter behaviour ─────────────────────────────────────────────────
static void test_run_id_increments_on_every_trigger() {
    GROUP("run id increments on every trigger");
    Indicator ind; makeUp(ind);
    long a = ind.runId();
    ind.onTrigger(1000, 0, false);
    long b = ind.runId();
    ind.onTrigger(20000, 0, false);
    long c = ind.runId();
    CHECK(b == a + 1);
    CHECK(c == b + 1);
}

static void test_analysis_only_starts_from_sampling() {
    GROUP("analyzing can only follow sampling");
    Indicator ind; makeUp(ind);
    ind.onSamplingEnd(1000);            // no run in progress
    CHECK(ind.state() == IND_IDLE);
    CHECK(!ind.audioReady() == false);  // still able to make sound
}

int main() {
    std::printf("indicator_test — %s\n", "shipped indicator.h + dfplayer.h");
    test_boot_state_is_off_and_silent();
    test_green_requires_matching_run_id_while_analyzing();
    test_green_refused_while_not_analyzing();
    test_green_refused_for_wrong_or_missing_run_id();
    test_green_accepted_when_run_id_matches_trigger_id();
    test_replayed_verdict_is_rejected_after_a_new_run();
    test_unknown_state_name_never_produces_a_colour();
    test_timeout_produces_yellow_never_green();
    test_verdict_after_deadline_is_refused();
    test_verdict_hold_expires_to_idle();
    test_new_trigger_cancels_a_hold();
    test_progress_states_are_never_red_green_or_yellow();
    test_yellow_is_red_plus_reduced_green();
    test_training_mode_is_off_and_silent();
    test_leaving_training_mode_restores_normal_behaviour();
    test_verdict_clip_preempts_a_playing_cue();
    test_cue_cooldown_stops_repeat_person_detected();
    test_person_detected_safety_net_returns_to_idle();
    test_absent_audio_module_never_blocks();
    test_audio_becoming_ready_enables_clips();
    test_clip_slots_match_sd_layout();
    test_dfplayer_frame_bytes_and_checksum();
    test_dfplayer_rejects_out_of_range_arguments();
    test_common_anode_inverts_duty();
    test_run_id_increments_on_every_trigger();
    test_analysis_only_starts_from_sampling();

    std::printf("\nindicator_test: %d passed, %d failed\n", g_pass, g_fail);
    return g_fail ? 1 : 0;
}