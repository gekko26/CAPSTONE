# Deployment indicators — wiring guide

> **UNVERIFIED HARDWARE.** Nothing in this document has been tested on a physical
> board. Every pin choice, every resistor value and every DFPlayer command byte is a
> *proposal* derived from the code and the datasheet. Gg must validate each of them on
> the bench, **with the mains disconnected**, before anything is connected to a gate.

The indicators are **display only**. They are wired to no lock, no relay and nothing in
`Backend/models/decision.py`. If an indicator board is dead, unplugged or lying, the
decision and the data collection are unchanged.

---

## 1. What you are building

| Part | Count | Notes |
|---|---|---|
| RGB LED | 2 | Both mirror the same three channels |
| DFPlayer Mini + speaker | 1 | Voice cues, optional |
| Series resistors | 6 | One per LED channel — see §4 |

---

## 2. Pin map

Chosen to avoid every trap pin. Verified against `alcodetect_esp32.ino`.

| Pin | Use | Source | Conflict check |
|---|---|---|---|
| 34 | MQ3 sensor 1 (nose) | `.ino:117` | existing, input-only |
| 35 | MQ3 sensor 2 (jaw) | `.ino:118` | existing, input-only |
| 32 | MQ3 sensor 3 (clavicle) | `.ino:119` | existing |
| 4 | DHT11 data | `.ino:120` | existing |
| 2 | Built-in status LED | `.ino:122` | existing |
| 27 | Suction-fan relay | `.ino:166` | existing |
| 25 | Blower relay | `.ino:167` | existing |
| 26 | Solenoid, reserved | header comment only | reserved, do not use |
| **22** | **Indicator R (both LEDs)** | **new** | free |
| **23** | **Indicator G (both LEDs)** | **new** | free |
| **18** | **Indicator B (both LEDs)** | **new** | free |
| **19** | **ESP32 TX → DFPlayer RX** | **new** | free |
| **21** | **ESP32 RX ← DFPlayer TX** | **new** | free |

Why 18/22/23/19/21 are safe:

* **Not strapping pins.** 0, 2, 12 and 15 must be free at boot; none are used.
* **Not flash pins.** 6–11 are the SPI flash; none are used.
* **Not input-only.** 34–39 cannot drive an LED; none are used.
* **Not already taken.** The existing map above is complete for this sketch.
* **16/17 deliberately avoided.** On a WROVER / PSRAM module those two pins are consumed
  by the PSRAM, and Gg's board does not expose them at all. Do not move an indicator
  channel back to 16 or 17.

⚠️ GPIO 2 is the DevKit's **built-in** LED and is already used as a status light. It
also happens to be a strapping pin. Do not rewire it.

---

## 3. LED wiring

### Common cathode (default assumption)

```
ESP32 GPIO 22 ──[220Ω]──┬── LED1 red   anode
                        └── LED2 red   anode
                              cathode ── GND
```

Same for green (23) and blue (18). One resistor per channel **feeds both LEDs in
parallel** — that is why there are 6 resistors and not 3.

### Common anode

Reverse each LED (cathode to the pin side, anode to +3.3 V) and set
`LED_COMMON_ANODE true` in `alcodetect_esp32.ino`. The firmware then inverts the duty
at the single constant in `indicator.h`, so all the colour logic is unchanged.

> ⚠️ **Which one is Gg's is UNCONFIRMED.** `LED_COMMON_ANODE` is currently `false`.
> If the LED is dim-tinted or inverted when you expect solid green, flip this constant
> and reflash. That is the only change needed.

---

## 4. Resistor sizing — the arithmetic

Assume a blue/green/white LED with a forward voltage `Vf ≈ 3.0 V` (typical for a
3.3 V rail; **measure yours with a multimeter in diode mode**).

```
V_resistor = V_supply - Vf
           = 3.3 - 3.0
           = 0.3 V

I_LED target ≈ 8 mA  (bright enough, well inside the ESP32 GPIO limit of 12 mA)

R = V_resistor / I
  = 0.3 V / 0.008 A
  = 37.5 Ω
```

Because `Vf` varies a lot between colours and batches, **always fit the largest
standard value that still looks bright**:

| LED colour | Typical Vf | V_resistor | Suggested R |
|---|---|---|---|
| Red | 1.8–2.0 V | 1.3–1.5 V | 330 Ω |
| Green | 2.8–3.0 V | 0.3–0.5 V | 220 Ω |
| Blue | 2.9–3.1 V | 0.2–0.4 V | 220 Ω |

**Start at 220 Ω.** It is dimmer than the theoretical optimum but cannot burn an LED
and cannot push a GPIO past its limit. Step down to 150 Ω or 100 Ω only if it looks
too dim, and re-measure the GPIO current if you do.

Two LEDs on one channel draw twice the current, so the per-channel budget is
2 × 8 mA = 16 mA worst case. Keep the **real** target at ~5 mA each (10 mA per
channel) if you fit both on one resistor. **Or fit one resistor per LED** — that is
the safer layout and only costs 6 resistors.

---

## 5. DFPlayer Mini wiring

```
ESP32 GPIO 19 (TX) ──[1 kΩ]── DFPlayer RX
ESP32 GPIO 21 (RX) ──[1 kΩ]── DFPlayer TX      (5 V logic → 3.3 V pin)
ESP32 GND           ───────── DFPlayer GND
5 V (clean)         ───────── DFPlayer VCC
```

* The **1 kΩ series resistors** on both UART lines are for protection, not level
  shifting. They limit current if a line is ever driven the wrong way.
* **Common ground is mandatory.** Without it the UART is unreliable.
* The DFPlayer's `BUSY` pin is optional and left unwired (`DFPLAYER_BUSY_PIN = -1`).
  With it unwired the firmware can only confirm that the bytes were written, and says
  so honestly in `/status` as `audio_ready`. Wire it to a spare GPIO and set the
  constant if you want a true confirmation.

### Power — this is the part people get wrong

The DFPlayer's amplifier draws **peaks of several hundred milliamps** when a clip
starts. On a USB-powered DevKit that browns out the ESP32 mid-run, which looks
exactly like a firmware bug.

* Power the DFPlayer from a **separate, stable 5 V rail** — not the ESP32's 3.3 V
  regulator, and not a USB port shared with the board if you can avoid it.
* Put **470 µF electrolytic + 100 nF ceramic** across the DFPlayer's 5 V and GND, right
  at its pins.
* If the ESP32 browns out during playback, that is this. Not the firmware.

### Speaker

* Speaker across `SPK1` and `SPK2` **only**. **Never one leg to GND** — that is how the
  module's output stage is damaged.
* 4–8 Ω, 1–3 W is right. Bigger is fine and louder; the DFPlayer drives it.

---

## 6. Mains safety

> **The relay contacts are NOT galvanically isolated logic.** Keep the mains side
> physically separate from every part of this document.

* Do **all** indicator bring-up with the mains **disconnected** and the relay wiring
  **not** made.
* The indicators do not need the fans. You can test every LED colour, every clip, the
  token path, the timeout and the training-mode behaviour with **nothing but the
  ESP32, the LEDs and the DFPlayer** connected.
* Only after §9 passes should the relay wiring be re-made, with the device off and the
  circuit breaker up.

---

## 7. Behaviour

| State | LED | Clip | Who sets it |
|---|---|---|---|
| `idle` | OFF | — | backend, or after the hold expires |
| `person_detected` | BLUE, slow pulse (1600 ms) | 001 | backend, debounced |
| `sampling` | BLUE, fast pulse (400 ms) | 003 | **firmware**, at pre-roll start |
| `analyzing` | BLUE steady | 004 | **firmware**, at window close |
| `pass` | GREEN solid | 005 | backend, authenticated verdict only |
| `warn` | YELLOW solid | 006 | backend, **or the firmware timeout** |
| `fail` | RED solid | 007 | backend, authenticated verdict only |
| any (training mode) | OFF | silent | — |

In-progress states are **blue only**, so they can never be mistaken for a verdict.

**YELLOW is red + a reduced green channel**, tuned by one constant
(`IndConfig::yellow` in `indicator.h`). It is used for WARN and nothing else.

---

## 8. The safety rules, and where they live

| Rule | Enforced at | Tested by |
|---|---|---|
| GREEN only on a matching run id while ANALYZING | `indicator.h` `submitVerdict()` | `indicator_test.cpp` |
| Never green at boot | `indicator.h` `begin()` | `test_boot_state_is_off_and_silent` |
| Replayed/stale verdict ignored | `indicator.h` `_runIdMatches()` | `test_replayed_verdict_is_rejected_after_a_new_run` |
| Unknown state name → reject, change nothing | `ind_state_parse()` | `test_unknown_state_name_never_produces_a_colour` |
| No verdict in 20 s → YELLOW + WARN clip, never green | `indicator.h` `tick()` | `test_timeout_produces_yellow_never_green` |
| Verdict held 8 s, then OFF | `indicator.h` `tick()` | `test_verdict_hold_expires_to_idle` |
| New trigger cancels a hold | `indicator.h` `onTrigger()` | `test_new_trigger_cancels_a_hold` |
| Token missing on device → 503, LEDs stay OFF | `.ino` `handleIndicator()` | compile-time + bench step 9.6 |
| Training mode → OFF and silent | `indicator.h` `setTrainingMode()` | `test_training_mode_is_off_and_silent` |
| Verdict clip preempts a cue | `indicator.h` `_play()` | `test_verdict_clip_preempts_a_playing_cue` |
| "Person detected" does not repeat | `indicator.h` `_play()` | `test_cue_cooldown_stops_repeat_person_detected` |
| A stale blue cannot lie forever (60 s) | `indicator.h` `tick()` | `test_person_detected_safety_net_returns_to_idle` |
| Missing DFPlayer never blocks boot | `serviceAudio()` retries at 30 s | `test_absent_audio_module_never_blocks` |

---

## 9. Bench bring-up order

Do these in order. Stop at the first failure.

**0. Mains OFF.** Relay wiring not made. ESP32 on USB only.

**1. Build without a token.** `arduino-cli compile --fqbn esp32:esp32:esp32 .`
Expect a clean build. With no `secrets.h` the device answers **503** on `/indicator`
and every LED stays off — that is correct, not broken.

**2. Boot silence.** Flash, watch `arduino-cli monitor -p /dev/ttyUSB0 -c baudrate=115200`.
Expect: boot banner, mode line, **`audio_ready: false`**, and **no sound at all**.
Check `/status`: `indicator_state` is `idle`, `indicator_enabled` is `false`.

**3. LEDs OFF at boot.** Both LEDs dark. They must not flicker, and must not be green.

**4. Create the token.** `openssl rand -hex 32` → put it in `secrets.h`, and
`export ESP32_DEVICE_TOKEN='<same value>'` for the backend. **Never echo it.**
Now `indicator_enabled` in `/status` is `true`.

**5. Bench each state.** Settings → Sensors → Indicators. Press each button and check
the LED colour and the clip. Green is intentionally absent.

**6. Wrong token.** Stop the backend, change the export to a wrong value, restart,
press a bench button. Expect **401** and a clear message, and **no LED change**.

**7. Wrong run id.** `curl -X POST .../indicator -H 'X-Device-Token: <t>' -d '{"state":"pass","run_id":999999}'`
Expect **409**. **No green light.**

**8. DFPlayer unplugged.** Pull the module. Restart the device. Expect a normal boot,
`audio_ready: false`, and every bench state still lighting its LED with no sound.

**9. Training mode.** `GET /mode?training=1`. Press bench buttons. Expect **no LED, no
sound**, and a 409 from the bench. Then `GET /mode?training=0`.

**10. Only now:** re-make the relay wiring, mains still off, then bench the relays.

---

## 10. Open items — NOT done in this change

* **`/mode` and `/relay_test` are still unauthenticated.** The shared-token design was
  implemented for `/indicator` only, as scoped. Anyone on the LAN can currently switch
  a gate device into training mode or fire the relays. This is a real risk and should
  be the next piece of work.
* **`LED_COMMON_ANODE` is an assumption**, not a measurement.
* **Wi-Fi credentials are still inline** in the `.ino` (lines 69-70) and are in git
  history. `secrets.h.example` documents the move; it was not performed because a
  device flashed without a real `secrets.h` would fail to join Wi-Fi at all.
* **Every DFPlayer command byte is UNCONFIRMED** against the datasheet. See
  `docs/audio/MANIFEST.md` §5.
* **No LED has ever been lit and no clip has ever been played.** There is no hardware
  in front of me. All of the above is untested on metal.