# Econ Sim Product Checklist

Updated: 2026-03-24

Legend:
- `[ ]` not met yet
- `[~]` partially met or inconsistent
- `[x]` implemented and broadly holding up

This file is the working product contract for `econ-sim`. It turns the full conversation into concrete acceptance criteria, with current status based on live iteration, code inspection, and browser QA.

## Latest pass notes

- 2026-03-24 current pass: explicit opening-era setup fields are gone from the public contract. The setup chamber now keeps future-setting guidance in natural-language fields like `premise` and `stakes`, while later-settlement openings are inferred internally from that prose instead of being exposed as a separate mode knob or echoed back in setup updates.
- 2026-03-20 current pass: council `Speak/Stop` no longer tears down the live session. The main scene mic now hot-pauses voice instead of disconnecting, which should reduce reconnect lag and keep solo/council rooms feeling more like one continuous channel.
- 2026-03-20 current pass: council continuation no longer stops just because the player-proxy urgency spikes. The loop now keeps going until explicit yield, interrupt, mute/room change, or a quiet runaway safety, and repeated identical turns now trigger a replan nudge instead of an immediate shutdown.
- 2026-03-20 current pass: council speech persistence was taken out of the line-by-line playback path. Spoken advisor lines are now persisted after playback in a batch, which should reduce some of the council latency that came from waiting on backend sync during speech.
- 2026-03-20 current pass: the public board got another composition pass. The main metric tiles are calmer, the stats wall sits less hard against the left edge, and `LIVE POLLS` now uses a cleaner answer-first card with a compact share badge instead of the old cramped right-side block.
- 2026-03-20 current pass: centered caption and council-floor overlays were pushed down and slimmed so they compete less directly with the back-wall boards.
- 2026-03-20 current pass: town hall questions are now persisted server-side into the shared debate thread before the frontend plays them, instead of living first as a stitched local turn. The frontend still injects the spoken audience question into the live debate channel so the active debate session hears it, but the thread is now owned by the backend.
- 2026-03-20 current pass: later-settlement openings were pushed further. When the setup prose clearly starts well after the transition, the opening jumps straight to a more transformed settlement frame, and both blueprint/stage prompts explicitly demand changed social arrangements such as income flow, ownership, staffing, or credentialing rather than a louder 2020s economy.
- 2026-03-20 current pass: documentary beat constraints were loosened slightly so the reel can breathe more like a script and less like compressed caption fragments.
- 2026-03-20 current pass: targeted validation is clean after these changes. `python -m pytest -q tests/test_director.py tests/test_prompt_quality.py tests/test_gabriel_service.py` and `npm --prefix web run build` both passed again on the current patch set.

- 2026-03-19 current pass: town hall is no longer treated as a separate debate thread on the backend. Debate Realtime sessions now always use the main debate prompt/thread, and audience questions are being injected into that same debate history instead of spinning up a second room identity.
- 2026-03-19 current pass: council turns now persist as speaker-attributed advisor lines instead of one flattened assistant blob, which makes the room history more like a shared exchange and lets named turns render more cleanly in the log and room captions.
- 2026-03-19 current pass: fresh Chrome-for-Testing council QA on `sim_d03111483dfb` now returns a clean two-advisor disagreement with `ROWAN HAS THE FLOOR`, named urgency chips, and `Speak -> Stop` restoring properly after the prompt.
- 2026-03-19 current pass: the auditorium/town hall logic is structurally better than before, but the surfacing is still not at the bar. Fresh Chrome-for-Testing screenshots show the dossier/drawer treatment is still too intrusive and can obscure the in-scene flow even though the underlying question injection path is cleaner.
- 2026-03-19 current pass: a fresh root probe on `sim_cb6ac7562e2e` still produced `THREE.WebGLRenderer: Context Lost` during the setup-to-launch cycle, so renderer/context stability remains an open quality issue.
- 2026-03-19 current pass: council voice handling was simplified again. In live council mode, spoken replies now go through the labeled-line council voice path instead of mixing solo turns onto the single-session Realtime audio voice, which was contributing to identity drift and brittle floor behavior.
- 2026-03-19 current pass: Chrome-for-Testing council QA on `sim_8e1f6ee20fae` now returns a labeled disagreement again with `ROWAN HAS THE FLOOR`, visible urgency chips, and `Speak -> Stop` on the scene mic after the prompt.
- 2026-03-19 current pass: auditorium town hall is now much more discoverable. A persistent topbar toggle exposes `Town hall`, the auditorium mode writes `?auditorium=town_hall` into the URL, and live Chrome-for-Testing QA confirmed the mode opens to `Town hall floor` on `sim_8e1f6ee20fae`.
- 2026-03-19 current pass: the public board was simplified again into four larger number tiles plus poll cards, which is materially more readable than the earlier six-metric compressed wall even though the left board still needs another taste/composition pass.
- 2026-03-19 current pass: live council QA needed one more correction - some failures were false negatives caused by reusing sims that had already persisted into a different room. Isolated Chrome-for-Testing council QA on `sim_dc86214d0aa9` now reliably lands in the advisor room again and returns a four-line exchange with `ROWAN HAS THE FLOOR` and `Speak -> Stop -> Speak`.
- 2026-03-19 current pass: the solo advisor room got another real composition pass. The camera is lower/wider, the twin boards are mounted lower, and the top-crop problem from recent screenshots is materially better.
- 2026-03-19 current pass: the public board is still not perfect, but the latest number wall uses larger metric typography and cleaner poll tiles, which is a real improvement over the earlier tiny clipped stats strip.
- 2026-03-19 current pass: the floating street citizen card now surfaces AI exposure directly and uses more grounded prompts about the citizen's actual week, instead of leaning so hard on generic AI boilerplate.
- 2026-03-19 current pass: street turning and follow distance were softened again. It still needs more elegance, but the post-patch camera no longer snaps as hard when the player starts turning.
- 2026-03-19 current pass: the council room no longer asks the model to perform `report_council_floor` before it can speak. Floor selection is now local UI state, the council prompt treats routing as silent, and council replies can go straight into first-person lines instead of tool-shaped chatter.
- 2026-03-19 current pass: council Chrome-for-Testing QA on `sim_e8c5df50ea03` now lands in a cleaner direct exchange again. The latest probe returned `Rowan / Amina / Leila` lines in first person with no visible tool-call language, and the room mic returned to `Speak -> Stop`.
- 2026-03-19 current pass: the public board was retuned again from one cramped six-metric strip into a two-row number wall, which materially improves the left-board scale without giving up the core metrics.
- 2026-03-19 current pass: the advisor boards were pulled farther inward again, reducing the worst left-edge crop in current room screenshots even though the stats wall still needs another composition pass.
- 2026-03-19 current pass: the street encounter card now carries shorter lived-detail copy and only two suggested questions, which reads more like an interview prompt and less like a bio dump.
- 2026-03-19 current pass: street turning was softened again by reducing heading snap on pure lateral movement and easing the citizen-camera follow slightly. It is still not fully elegant, but it is less abrupt than the prior pass.
- 2026-03-18 current pass: the council follow-up contract had a real bug. After `report_council_floor`, the follow-up instruction was still telling the model to "first call" the floor tool again, which encouraged self-aware/tool-shaped replies. The follow-up path now explicitly says the floor is already set, not to mention the mechanism aloud, and to just speak.
- 2026-03-18 current pass: explicit council debate prompts now hold up better in Chrome-for-Testing QA. The latest probe on `sim_e8c5df50ea03` returned three short direct lines with three separate `/api/audio/speech` calls (`cedar`, `marin`, `ash`) instead of a single flattened lead answer.
- 2026-03-18 current pass: the public board was simplified again by removing the low-value footer band and giving the main readouts and poll cards more vertical room. The current wall still needs another taste pass, but the left board is materially easier to read than the earlier cramped strip.
- 2026-03-18 current pass: council captions now render multi-line speaker lines in the room instead of collapsing the whole exchange into one tiny paragraph, which makes live disagreements easier to follow.
- 2026-03-18 current pass: the council room was simplified again after live QA showed the earlier hybrid pass had become too meta. The council now keeps the urgency tool, but the speech contract is back to direct first-person lines instead of role narration, and it explicitly forbids third-person room-summary phrases like `Leila might add`.
- 2026-03-18 current pass: a real typed-while-voice-joining race was making council replies silently fall back to plain text mode. The hook now waits for the voice channel more intelligently before deciding whether a typed council turn should return as council voice or plain text.
- 2026-03-18 current pass: council voice onset is materially faster because playback now starts as soon as the first synthesized line is ready instead of waiting for every advisor segment up front. In the latest instrumented Chrome-for-Testing probe, the first council speech request landed in about 3.0 seconds instead of the much slower earlier path.
- 2026-03-18 current pass: full-council disagreement prompts are now more likely to produce actual multi-speaker output. The latest live probe on a strict-licensing debate returned separate synthesized lines for Rowan and Leila instead of one flattened monologue.
- 2026-03-18 current pass: council mode now uses a hybrid live path that keeps one Realtime room session for listening / interruption / tool calls but synthesizes the spoken council reply locally after the floor tool resolves. This avoids the one-voice-per-session Realtime limitation while preserving the council floor mechanic.
- 2026-03-18 current pass: the council reply parser now splits inline speaker tags such as `Leila:` and `Rowan:` instead of only newline-separated dialogue. In Chrome-for-Testing QA, a forced disagreement prompt now produces multiple `/api/audio/speech` calls with different voices instead of collapsing the whole council reply into one voice.
- 2026-03-18 current pass: a stale React-state race was keeping council follow-up replies in plain text mode after `report_council_floor`. The council follow-up path now keys off a live mode ref rather than a lagging render-state snapshot, so the post-tool reply stays in council voice mode.
- 2026-03-18 current pass: street follow-camera movement and citizen identity bubbles were tightened again. Citizen cards now carry a clearer compact role/status read, and the street camera turns/auto-approach are less jerky than the prior pass.
- 2026-03-18 current pass: documentary and persona-update prompts were pushed further away from repetitive wait-time/admin framing. The current writing contract now answers what AI can broadly do, what still needs people, and how everyday life changes before it narrows into personal examples.
- 2026-03-18 current pass: the advisor/debate board flicker was traced to a real geometry bug as well as texture churn. The twin boards were still overlapping at the centerline in 3D space, so the wall bays and mounted boards were pulled apart, the board-texture memoization was signature-keyed, and the mounted board texture path was sharpened.
- 2026-03-18 current pass: the public board was reworked again into a bigger-number briefing wall. The top metrics now privilege a larger four-tile read with secondary mini-readouts and larger poll cards underneath, instead of trying to cram all six metrics and polls into one dense strip.
- 2026-03-18 current pass: advisor-room foreground staging was pushed outward and the solo advisor camera widened slightly so the back-wall boards have a clearer sightline. This is materially better in live Chrome-for-Testing screenshots, though the public board still rides a little too close to the left edge of frame.
- 2026-03-18 current pass: council-mode Realtime now follows the official function-call pattern more closely. `report_council_floor` is handled once per call id, follow-up speech is queued after the prior response completes, and the old timer-forced response fallback was removed because it risked duplicate or chopped follow-ups.
- 2026-03-18 current pass: council-room Chrome-for-Testing QA now shows the floor system actually landing in-room. The room displays `ROWAN HAS THE FLOOR`, urgency chips are visible for all four advisors, and `Speak -> Stop -> Speak` works in the council room under fake-media QA.
- 2026-03-18 current pass: the advisor and debate wall boards were enlarged again, the public board gained a bottom `Current read`/`Campaign read` band so it does not go half-empty on fresh stages, and the council seat labels were lowered to clear more of the back-board sightline.
- 2026-03-18 current pass: likely board flicker debt was traced to unstable board-panel inputs recreating the texture too often. The board metrics and poll columns are now memo-keyed off stable metric/poll signatures instead of raw newly-created arrays each render.
- 2026-03-18 current pass: the visible `Speak` controls are back to true start/stop semantics instead of soft pause semantics. Tapping them now tears down the live room session again, which should reduce ghost listening, duplicate voices, and half-alive room carryover.
- 2026-03-18 current pass: poll summaries now carry explicit board-slot metadata (`capability`, `national`, `gain`, `pressure`, `custom`) from Gabriel through the backend into the frontend. The public board is moving off brittle question-text guessing and toward a stable display contract.
- 2026-03-18 current pass: the public board was retuned again toward a fixed number strip (`Approval`, `Vote`, `Better off`, `AI comfort`, `Gov trust`, `Stability`) with cleaner slot-based poll cards and sharper board textures.
- 2026-03-18 current pass: room-brief and indicator normalization no longer use ellipsis clipping, and trailing connector fragments are stripped more aggressively. This was prompted by a fresh run that produced visibly mangled advisor-room briefing copy.
- 2026-03-18 current pass: a fresh small direct run (`sim_c299d47db231`) confirmed the chapter content is more macro-first than before, but it also exposed that the old room-brief normalization was still producing memo-like broken lines. That specific bug was patched after the run and still needs one fresh visual confirmation.
- 2026-03-18 current pass: fresh setup-launch validation still shows stage preparation latency as real product debt. Even at smaller persona counts, stagewriting can remain the long pole.
- 2026-03-18 current pass: council mode now uses an explicit frontend turn-plan instead of only ad hoc keyword nudges. One advisor leads most turns, contrast voices are brought in more deliberately, tool follow-ups keep the same council lead, and the dock can now display multi-line council replies as separate named voices.
- 2026-03-18 current pass: debate board derivation is no longer tied to raw user chatter. The app now extracts policy-like lines from the player’s debate turns and falls back to advisor notes, which should reduce junk platform boards and give both the board and election resolve path a cleaner player platform.
- 2026-03-18 current pass: the room-brief composer and narration normalizer were tightened again so fresh stages should land shorter four-line room briefs and fewer clipped list-like documentary beats.
- 2026-03-18 current pass: the advisor/council back boards were retuned again for readability - darker board ink, shorter policy labels, fewer live-read columns, and a raised council beam so the board face is less obstructed in Chrome-for-Testing screenshots.
- 2026-03-18 current pass: the first-run loading/tutorial deck was refreshed again with clearer “how the game works” guidance and less repetitive strategy filler.
- 2026-03-18 current pass: setup, advisor, street, and debate mic buttons now share the same pause/resume semantics instead of mixing mute paths with hard disconnects. Setup no longer tears down the chamber session just because the player taps the live mic button again.
- 2026-03-18 current pass: stale delayed scene refreshes are now generation-guarded against later room switches, which should reduce old-room state snapping back after movement or mode changes.
- 2026-03-18 current pass: Realtime tool follow-ups are now serialized after the active response finishes instead of firing immediately on top of the current response. This is meant to reduce “board updated but nobody kept talking” and overlapping reply races after polls or board tools.
- 2026-03-18 current pass: the documentary writer now carries the blueprint's `documentary_movements` forward into both the chapter-writing and montage-writing passes, and the phase ladder was widened further toward capability, education, consumer tools, entrepreneurship, and scientific assistance rather than recurring office-admin tropes.
- 2026-03-18 current pass: the public board was tightened again into a cleaner answer-first numeric wall board with larger share figures and shorter poll footnotes, rather than a dense mini-table trying to show too many labels at once.
- 2026-03-18 current pass: a new broad-default U.S. run was launched from the patched backend for fresh-content QA (`sim_175d2b6a3f6f`). It is the current baseline for validating post-prompt-change documentary quality rather than relying on older stage-ready sims.
- 2026-03-18 current pass: the public board was simplified again into a more number-first wall board with cleaner poll rows, the policy board stayed as a short numbered agenda, and foreground furniture was pushed down/out so the boards read more like room architecture.
- 2026-03-18 current pass: the public mood board was tightened one more step into a two-row number-first wall board with thinner mount framing, while the policy board stayed intact and readable in the back wall.
- 2026-03-18 current pass: the root Chrome-for-Testing probe was hardened for the current chamber flow. It now treats an immediate setup auto-launch as success instead of a failed second prompt, which makes fresh-run QA reflect the real product behavior.
- 2026-03-18 current pass: the advisor public board was pushed further toward a numeric readout: fewer live poll rows, larger share figures, and a simpler answer-first layout instead of a dense question/answer/share table.
- 2026-03-18 current pass: the early loading/tutorial deck was tightened again with stronger strategy guidance, shorter capability notes, and less filler in the first-run quote rotation.
- 2026-03-18 current pass: the stage-writing prompts were pushed further away from queue/backlog clichés and toward broader capability, cheaper expertise, learning, software leverage, and what people can now do outside their old skill boundary.
- 2026-03-18 current pass: the council roster now reads more like a real governing team - economic, households, politics, and security/state capacity - while still staying on the one-speaker shared Realtime architecture.
- 2026-03-18 current pass: live screenshots still show the street as too sparse and horizon-heavy, even after the closer follow-camera pass, so that remains an open design problem rather than a closed item.
- 2026-03-18 current pass: current official Realtime docs and builder patterns were rechecked. The live baseline in code is a shared `semantic_vad` profile with medium eagerness and interruption enabled across frontend and backend, while council mode now moves toward manual response control.
- 2026-03-18 current pass: live Chrome-for-Testing QA was rerun after the Realtime retune, board-layout rewrite, and street camera adjustment. The public board now uses the wall more intelligently, and the street camera sits farther back, but both still need another polish pass.
- 2026-03-19 current pass: council floor routing is now handled client-side instead of being a required live `report_council_floor` tool call in the spoken exchange. The council prompt still uses urgency as the hidden routing logic, but the player should now hear direct speech rather than tool-shaped floor chatter.
- 2026-03-19 current pass: council mode still uses text plus per-speaker speech synthesis for differentiated voices, but it now skips the extra floor-tool roundtrip. This should reduce latency and make the reply feel less meta, even though true multi-session live council debate is still an open R&D path.
- 2026-03-19 current pass: the street room now has a richer floating citizen card driven by the existing citizen fields, with hover taking precedence over stale preview for browsing. The in-world badge is simpler again and the suggested questions are more tailored to the citizen's life rather than generic AI boilerplate.
- 2026-03-19 current pass: street locomotion was simplified toward forward/back movement plus turning, with less camera pull toward a citizen until the player is closer. That should reduce the slide-and-swivel feel, but it still needs live taste-testing before it can be called finished.
- 2026-03-19 current pass: the public board got another readability pass - shorter poll answers, larger signal typography, and a cleaner floating citizen card so the scene is not doing too much work inside tiny in-world labels.
- 2026-03-17 current pass: setup-country carry-through was rechecked live. Mexico-style chamber nudges now survive launch instead of silently collapsing back to the U.S. default.
- 2026-03-17 current pass: the visible in-scene mic buttons now use the real Realtime toggle path in both setup and live rooms. The latest hook pass also adds an explicit connection-request guard so rapid double taps are more likely to cancel a join instead of leaving a ghost connection alive.
- 2026-03-17 current pass: the Realtime turn-detection baseline was rechecked against current official docs and kept on a shared `semantic_vad` profile with medium eagerness on both frontend and backend, rather than drifting into mismatched room-specific settings.
- 2026-03-18 current pass: council-mode Realtime is now moving toward explicit one-speaker manual turn release rather than treating the council like a normal auto-response room, and prompt follow-through after poll/board tool calls has been tightened so tool use does not swallow the spoken answer.
- 2026-03-18 current pass: the public board and debate board were simplified again: fewer live-read cards, cleaner wall spacing, and less foreground obstruction around the war-room boards.
- 2026-03-17 current pass: room and citizen handoff are less optimistic. Citizen identity is now committed only after backend focus persistence, and room changes no longer flip the visible room before the backend focus result comes back.
- 2026-03-17 current pass: room-move tool calls now tear down the prior Realtime session faster, and stale async tool results are generation-guarded before they can apply to a new session.
- 2026-03-17 current pass: the public-mood board was redesigned away from nested dashboard cards toward a simpler numeric board grammar, and the debate-room board now carries the resolved live platform rather than only persisted stage notes.
- 2026-03-17 current pass: advisor boards and wall bays were widened and slimmed again, with a slightly wider advisor camera, so the back-wall boards read more like room architecture and less like pasted cards.
- 2026-03-17 current pass: the bottom speak/input shell and light-mode loading cards got another cleanup pass. Contrast is better, the lower shell is slimmer, and the centered loading quote block is easier to read.
- 2026-03-17 current pass: the documentary prompt contract was tightened again so the macro story stays cleaner, less list-like, and less anchored on office churn; at least one early gain now has to live outside office automation when the stage supports it.
- 2026-03-17 current pass: targeted validation is clean after these changes. `python -m pytest -q tests/test_prompt_quality.py tests/test_director.py tests/test_gabriel_service.py` and `npm --prefix web run build` are both expected to stay green on the current patch series.

## 1. Core Product Shape

- [~] The sim should feel like one contiguous game, not a web app plus a few 3D scenes. Acceptance: setup chamber, loading/interstitials, documentary intro, advisor room, street, and auditorium all feel like one authored experience with the same interaction language.
- [~] The main mode should be full-screen immersive 3D. Acceptance: the world fills the screen by default and secondary panels stay collapsed unless explicitly opened.
- [~] The main stage loop should be natural and legible. Acceptance: documentary intro -> advisor discussion / policy workshop -> street interviews -> optional return to advisor -> debate -> election / resolve -> next-stage interstitial.
- [~] The sim should work as an educational product for policymakers who do not already know AI well. Acceptance: the experience teaches through coherent scenes, dialogue, and documentary framing rather than requiring UI archaeology.

## 2. Setup Chamber

- [~] Setup should be another real 3D room, not a config form. Acceptance: the player begins in a scene-first chamber and talks to the orchestrator as a physical in-world figure.
- [~] The orchestrator should read visually as a glowing elevated conductor-like figure. Acceptance: strong “orchestrator” presence with baton / stage authority, not just text chrome.
- [x] The default setup should be broad U.S. national AGI, not narrow Great Lakes / factory-town / special lens assumptions. Acceptance: `region_focus`, `topic_lens`, `premise`, and `stakes` stay blank unless the player specifies them.
- [x] Setup should be lightly guided, not overprescribed. Acceptance: the chamber explains the broad default simply and does not front-load a preset menu unless asked.
- [x] Setup should use the same core interaction shell as the rest of the game. Acceptance: the chamber has the same mic-plus-inline-text grammar as advisor / citizen / debate rooms.
- [~] Setup voice should feel like the same live Realtime conversation as the rest of the sim. Acceptance: the orchestrator chamber uses the same interruptible `gpt-realtime-1.5` back-and-forth model as advisor, citizen, and debate scenes rather than a setup-specific-feeling voice path.
- [~] Setup voice must be continuous live conversation, not press-record-submit behavior in disguise. Acceptance: mic-on means always-live Realtime with interruptions; mic-off means a true pause; chamber voice never feels like a special recorder workflow.
- [~] Spoken or typed setup nudges should shape the run. Acceptance: the player can say things like “Finland,” “Swiss education,” “250 personas,” or “different art style,” and those instructions propagate through config, documentary framing, citizens, polling, debate framing, and visual treatment. Typed carry-through has been reverified on the live launch path; live spoken carry-through still needs another direct QA pass.
- [~] Live spoken setup must carry through country and scenario changes too. Acceptance: if the player says “Mexico” or another country aloud in the chamber, the launched sim actually adopts that frame instead of falling back to the U.S. default. The typed path is already verified; the spoken path still needs more human QA.
- [~] Saying “go”, “launch”, or “use the default” should start the sim naturally. Acceptance: the chamber hands off cleanly into loading / prep with no awkward fallback flow.

## 3. Loading And Interstitials

- [~] Loading screens should feel like part of the game, not admin status panes. Acceptance: full-screen presentation, restrained chrome, polished typography, and clear hierarchy.
- [~] Ready-state loading layout should be truly full-screen and centered. Acceptance: one dominant quote or line sits in the center, with secondary cards tucked below or off-axis rather than clustering the whole screen into one card stack.
- [~] The first initialization should rotate gameplay/setup tips instead of showing one static line. Acceptance: helpful guidance cycles every ~10-15 seconds before real public quotes exist.
- [~] Initial setup tips should feel like practical strategy guidance, not filler. Acceptance: rotating first-load quotes teach the player how to use the sim, what to ask, and how to interpret the experience. The guidance is materially better now, but still can be more elegant and memorable.
- [~] Inter-stage loading should rotate quotes or tidbits from polls, citizens, or world state. Acceptance: one dominant centered quote at a time, not a cluttered feed. The current layout is close, but some state text still competes with the hero quote.
- [~] Quote formatting should be humanized. Acceptance: one quote line plus a compact byline such as `— Name · role/place`.
- [~] Quotes and loading tidbits should cycle automatically on a readable rhythm. Acceptance: the spotlight line fades between quotes / notes every roughly 10-15 seconds without user intervention.
- [~] Loading quotes still need a tighter visual cap. Acceptance: quotes are short enough to feel elegant on the full-screen card and do not end up as giant ellipsis-heavy blocks.
- [~] Loading progress must feel honest rather than frozen. Acceptance: long seeding/stagewriting phases communicate visible forward motion so the screen does not appear stuck while backend work is still active.
- [~] When the next stage is ready, the user should get a clear CTA. Acceptance: a strong button such as `Begin the chapter reel` appears instead of autoplaying abruptly.
- [~] The documentary should not startle the player by auto-firing. Acceptance: the user explicitly launches it.
- [~] After the documentary finishes, entering the live room should also be explicit. Acceptance: a clear button such as `Head to your advisor` or equivalent.
- [ ] Documentary handoff timing still needs hardening. Acceptance: `Begin the chapter reel`, `Skip`, and `Head to your advisor` always land in the expected next state without fragile transition timing.
- [ ] End-of-reel CTA timing needs a hard upper bound. Acceptance: the reel never leaves the player waiting an excessive amount of time for `Head to your advisor`, and the CTA does not depend on brittle audio-end behavior.
- [ ] The loading/interstitial state should be more diegetic and less like a separate screen mode. Acceptance: the handoff from chamber to loading to intro feels fully authored and seamless.

## 4. Documentary Intro

- [~] Each stage intro should feel like a coherent mini-documentary, not disconnected caption cards. Acceptance: the beats form one narrative arc and read like a short script rather than a list of observations.
- [~] The intro should open macro-first. Acceptance: early beats establish AI capability, economic situation, social change, and labor / price / international dynamics before personal vignettes.
- [~] The intro must explain AI capability plainly before narrowing down. Acceptance: the viewer hears what AI can broadly do now, what still requires people, and what robotics or physical deployment can or cannot yet do.
- [~] The intro should be somewhat longer and more substantive than a teaser. Acceptance: a stage intro usually lands around 8-10 beats and has enough macro setup to establish a real era-level story before it narrows.
- [~] The writing should be cleaner and less comma-heavy. Acceptance: spoken lines feel like documentary narration rather than dense lists.
- [~] Documentary beats should breathe. Acceptance: most beats are 1-2 sentences max, one big point can unfold across multiple images, and the narration leaves room for images to carry part of the meaning.
- [ ] Documentary duration should stay bounded and audience-friendly. Acceptance: a normal chapter reel feels substantial but not exhausting, and it reaches the room-entry CTA in a predictable amount of time.
- [~] One point can span multiple images when useful. Acceptance: pacing supports a bigger narrative beat across 2-3 images instead of forcing one oversized sentence per image.
- [~] Voiceover timing must not cut off mid-sentence. Acceptance: image advancement never outruns narration unless the player explicitly skips, and the voice finishes the full line during autoplay.
- [~] Visual treatment should feel cinematic. Acceptance: panning / zooming / transitions / parallax rather than static image cards.
- [~] Skip and replay controls should exist but stay unobtrusive. Acceptance: they sit in a corner and do not dominate the frame.
- [~] Light-mode documentary aesthetics should be warm / sepia, not harsh white. Acceptance: loading and intro scenes remain readable and tasteful in light mode.
- [ ] Light-mode loading text must stay readable against the sepia treatment. Acceptance: quote text, bylines, cards, and CTAs never wash out into pale backgrounds during loading or ready states.
- [~] Inter-stage documentaries should reveal election outcomes and why they happened as part of the chapter opening. Acceptance: the result is dramatized in the opening story instead of dumped clumsily beforehand.

## 5. Orchestrator And World Simulation

- [~] The orchestrator should establish the broad national or jurisdiction-level picture before narrowing down. Acceptance: each stage clearly states what AI can do, where adoption is happening, and the main economic shifts.
- [~] AI capability must be explicit at every stage. Acceptance: the player can answer “what can AI broadly do in this world?” after hearing the intro.
- [~] Stage progression must be meaningfully different over time. Acceptance: each stage feels like a new frontier, not a small variant of the present.
- [ ] Later stages need more radical-but-plausible AGI / robotics consequences. Acceptance: later chapters no longer read like near-term office automation with extra adjectives.
- [~] The sim should not default to a predominantly negative or patch-focused story. Acceptance: each stage includes real upside, convenience, productivity gains, and things people actively like, alongside frictions and losses.
- [~] Positive change needs to be a stronger first-class part of the world story. Acceptance: the sim repeatedly shows what people genuinely gain from AI, why adoption keeps spreading, and why some constituencies want more of it.
- [~] The orchestrator still over-favors a few recurring narratives, especially entry-level / job-ladder loss. Acceptance: repeated playthroughs should surface a wider set of mechanisms and lived changes, with upside lanes getting equal narrative weight.
- [~] Government should not be framed as the default first adopter unless justified. Acceptance: first-wave adoption patterns look institutionally realistic.
- [~] Policy should shape the margin while technology and society do the heavier lifting. Acceptance: policy matters without being portrayed as omnipotent.
- [~] The sim should react across stages to player policy, election outcomes, and political stance. Acceptance: downstream worlds visibly differ after restrictive versus permissive strategies.

## 6. Economic Grounding

- [~] The sim should be grounded in thoughtful macro and microeconomic logic, not buzzwords. Acceptance: the stage story naturally touches productivity, prices, wages, profits, bottlenecks, diffusion, trade, and public legitimacy where relevant.
- [~] Documentary intros should include concrete macro cues or signals. Acceptance: unemployment, service reliability, price pressure, margins, hiring, adoption, or similar real indicators appear naturally.
- [~] Distributional effects should be real and legible. Acceptance: different groups, regions, firms, and workers experience gains and losses unevenly.
- [~] Distributional stories still need more creative breadth. Acceptance: the sim should rotate through a wider set of realistic gains, frictions, and social adaptations rather than one repeated labor-market mechanism.
- [~] International competition should matter. Acceptance: over-restriction can plausibly let other countries or regions pull ahead.
- [~] AI should affect more than employment. Acceptance: services, expertise access, errands, care, schooling, status, family time, and consumer life all appear in the stage story.
- [~] Later chapters still need stronger macro creativity and a more persuasive “how life is different now” frame.

## 7. Advisor Room And War Room

- [~] The advisor room should feel like an Oval Office / executive war room. Acceptance: walls, decor, furniture, props, and lighting evoke a real political office.
- [~] The room should include more detail and knickknacks without clutter. Acceptance: more books, objects, drapes, lamps, framed elements, desks, couches, and political-office texture.
- [~] Light mode should meaningfully relight the room, not just brighten it. Acceptance: walls and materials feel plausibly different in light mode.
- [~] The boards should be mounted in the back wall, not feel like floating cards. Acceptance: two large whiteboards live in the room background behind the advisor/player.
- [~] Board placement still regresses in live use. Acceptance: the twin boards should stay visibly mounted behind the characters in the actual camera frame and not disappear, float forward, or read like pasted cards. Current live views are better but not perfect.
- [~] The twin-board concept should remain stable. Acceptance: one board is for polling/public mood/statistics and the other is for working policy ideas / teleprompter notes.
- [~] The stats board should stay sparse and large-type. Acceptance: 3-5 high-value signals max, obvious labels, and no tiny dense blocks or off-screen copy. The current board is closer, but it still wastes space and needs a stronger visual rhythm.
- [~] The stats board must stay sparse and large-type. Acceptance: no more than a few high-value signals appear at once, labels are obvious, and no tiny dense blocks or off-screen text creep back in.
- [~] The public-mood board needs a cleaner numerical grammar. Acceptance: approval, vote, comfort, service feel, job risk, and one or two poll toplines should read as numbers first rather than dense mixed prose cards. The latest pass is better, but the left board still needs stronger composition.
- [~] The policy board should be simple. Acceptance: short numbered items, no extra prose.
- [~] The policy board should remain a clean teleprompter slate. Acceptance: short numbered planks only, no guidance prose, and the whole slate stays visible in-frame.
- [~] The policy board must remain a clean teleprompter slate. Acceptance: numbered planks only, no explanatory guidance text, and the full list remains visible in-frame.
- [~] The policy board should update through conversation. Acceptance: ideas can be added, replaced, scratched off, and cleared naturally.
- [~] The policy board should not open prefilled with random mush. Acceptance: blank or sparse at first unless prior discussion actually created items.
- [~] The boards still need better readability. Acceptance: they are wide, high-resolution, not fuzzy, not obstructed, and readable from the camera position.
- [~] Nothing structural should block the boards in-frame. Acceptance: pillars, plaques, furniture, or character staging do not sit on top of the most important board content from the live advisor camera.
- [~] Advisor-room camera composition should protect the board sightline. Acceptance: the player and advisor can stay in frame without covering the most important inner halves of the twin boards.
- [~] Board styling still needs more natural handwritten / marker taste. Acceptance: off-white board surfaces and better handwritten typography.
- [~] Board handwriting should not depend on whatever cursive font the OS happens to provide. Acceptance: a bundled or otherwise controlled handwritten face keeps the board look stable across machines.
- [~] The poll/stat board needs a better information design pass. Acceptance: only high-value signals appear, clearly labeled, with tighter layout.
- [~] Board copy must not truncate or spill awkwardly. Acceptance: policy planks, poll snippets, and labels fit inside the visible board frame without clipped lines, ellipsis mush, or awkward wrap.

## 8. Advisor Dialogue Quality

- [~] The advisor should be brief and conversational by default. Acceptance: “what do you think?” gets 1-3 sentences unless the player explicitly asks for detail.
- [~] A vague first advisor opener should cap at one short sentence. Acceptance: prompts like `what do you think?` or `so?` never trigger a platform dump or multiple stacked ideas.
- [~] Early turns should be exploratory rather than instantly prescriptive. Acceptance: first turns feel like a sounding board, not a speechwriter.
- [~] The advisor still needs a stronger low-pressure conversational mode. Acceptance: early replies probe, react, and workshop with the player before trying to install a platform in their head.
- [~] The advisor should be willing to say “leave that alone”, “wait”, or “we do not know enough yet”. Acceptance: not every issue turns into a policy patch.
- [~] The advisor must surface upside and restraint, not only mitigations. Acceptance: it sometimes highlights why people like the technology, why adoption is happening, and when the right move is to avoid overreacting.
- [~] The advisor should sound intelligent without sounding theatrical, aggressive, or managerial. Acceptance: plainspoken, sharp, live political-economic operator tone.
- [~] The advisor must surface upside and restraint as real options. Acceptance: it sometimes says the technology is helping, diffusion has a constituency, or the right move is to leave something alone for now.
- [~] If the player is circling, the advisor can get more proactive later. Acceptance: after some back-and-forth, it summarizes likely agenda options succinctly.
- [~] Polling should be integral to the advisor’s reasoning. Acceptance: the advisor can ask for or interpret both quantitative and qualitative poll signals.
- [~] The live voice behavior in advisor conversations still needs more polish. Acceptance: no chopped responses, no awkward dead air, no weird interruptions.
- [~] The advisor room may need a multi-advisor council variant. Acceptance: a richer advisory-board scene with several distinct viewpoints can be introduced without losing the simple live interaction shell.
- [~] If the council variant ships, it must have real differentiated seats. Acceptance: several advisors have distinct roles and fault lines, and the room can surface disagreement without becoming chaotic or losing the clean speak/listen flow.

## 9. Street / Citizen Room

- [~] The citizen room should feel like a real street / civic neighborhood. Acceptance: houses, stores, sidewalks, street elements, and ambient life rather than a sparse abstract strip. The closer follow camera helps, but the live scene still reads too sparse.
- [~] Street movement should feel natural. Acceptance: forward movement maps predictably to player/camera direction, click-to-move works cleanly, and movement does not jerk the camera.
- [~] Street heading and camera follow should feel physically coherent. Acceptance: when the player turns, the camera and forward direction stay aligned instead of sliding through the street on a fixed world axis.
- [~] A stronger embodied third-person presence is still desired. Acceptance: the player has a clearer in-world avatar / body presence rather than only a drifting camera feel.
- [~] The street should scale to larger persona counts without rendering everyone fully. Acceptance: nearby citizens are featured and distant extras are culled or simplified.
- [~] Citizens should look visually differentiated. Acceptance: varied silhouettes, palettes, stance, and surface detail.
- [~] Hovering or approaching a citizen should reveal a small readable identity card. Acceptance: compact name + a couple relevant details, no giant text blocks.
- [~] Citizen labels should stay fully on-screen and visually anchored. Acceptance: identity cards do not spill off the viewport edge or cover the whole lane when someone is selected near the curb.
- [~] It should be easy to walk up to and talk to people. Acceptance: selection is reliable and the responding persona is the visibly selected one.
- [~] Every primary visible citizen should be interactable, or clearly ambient. Acceptance: there are no obvious people in the main lane that look clickable but do nothing.
- [~] Only one citizen conversation should ever be active. Acceptance: no cross-talk, no double voices, no identity mismatch.
- [~] Street selection should stay aligned with the active live channel. Acceptance: preview state, committed focus, and voice channel identity do not drift apart.
- [~] Citizen identity must stay consistent from hover to response. Acceptance: the hover card, selected target, transcript label, and live speaking persona never disagree about who the player is talking to.
- [~] Background extras must not read like failed interactables. Acceptance: the player can always tell which people are selectable citizens and which are ambient extras.
- [~] The street still needs a fuller environmental art pass and more robust movement feel.
- [~] Street movement input should feel obviously responsive. Acceptance: held forward movement clearly advances the player, click-to-walk gives visible feedback, and the player never has to guess whether input registered.
- [ ] Moving forward in the street should keep the world populated. Acceptance: walking ahead reveals more people and texture rather than draining the scene into an empty road.
- [~] Street selection must feel intentional rather than nearest-neighbor arbitrary. Acceptance: clicking a person clearly selects that exact person, and a nearby citizen does not silently steal focus.

## 10. Citizen Dialogue Quality

- [~] Citizens should sound like ordinary people, not polished survey respondents. Acceptance: informal day-level speech with some rough edges.
- [~] Citizens should answer from personal life first. Acceptance: work, bills, family, routines, care, dignity, status, convenience, fear, and hope come before abstract politics.
- [~] Citizen political framing should stay secondary unless locally salient. Acceptance: many citizens never mention candidates or campaign logic unless their own situation makes politics salient.
- [~] Citizen responses need more unique AI-in-life specifics. Acceptance: each person talks about concrete ways AI touches their own job, services, routines, aspirations, or frustrations rather than falling back to generic opinion language.
- [~] Not everyone should default to generalized anti-AI anxiety. Acceptance: some citizens talk about convenience, improved service, better tools, status, pride, annoyance, scams, or mixed feelings.
- [~] Citizen viewpoints need a wider spread of exposure and feeling. Acceptance: early stages include people who are enthusiastic, indifferent, barely touched by AI, non-users, mixed, and fearful rather than overconverging on one lane.
- [~] Persona fidelity should be strong. Acceptance: timid, brash, guarded, hopeful, angry, skeptical, tired, and contradictory personalities come through distinctly.
- [~] Citizens should give concrete specifics. Acceptance: multiple citizens do not sound interchangeable.
- [~] Answers should generally be concise and natural. Acceptance: short enough to feel spoken, not mini essays.
- [~] Persona voice casting is wired into the simulation design. Acceptance: personas select among the defined voices and those voices persist into live citizen sessions.
- [ ] Live citizen conversations still need further playtesting for stability and interruption quality.

## 11. Debate / Auditorium

- [~] The auditorium should read as a real auditorium from an over-the-shoulder / on-stage perspective. Acceptance: the audience is visible in front of the candidates.
- [~] Debate should use the same core mic/text interaction shell as the other rooms. Acceptance: one unified speak / pause / type model.
- [~] Debate should support open back-and-forth, not a one-shot fixed rebuttal. Acceptance: the player can keep debating until calling the election.
- [~] The player’s finalized policy board should appear in the debate room background. Acceptance: visible teleprompter-style back-board continuity from the war room.
- [~] The opponent should steelman a real competing case. Acceptance: they present the strongest credible opposite governing lane to the player’s current board or stated position, not just reactive politics.
- [~] The opponent must occupy the strongest credible opposite governing lane. Acceptance: restrictive player agendas get a truly pro-diffusion rival, while speed-first player agendas get a genuinely stronger legitimacy/distribution rival.
- [~] Debate lane consistency should hold across live and prewritten debate output. Acceptance: the auditorium rival and the orchestrated debate copy express the same clear opposing governing philosophy.
- [~] Election resolution should be explicit and responsive. Acceptance: the election trigger has clear feedback and resolves promptly.
- [~] The election CTA still needs better immediate feedback. Acceptance: pressing the election button visibly changes state at once so it never feels dead or silently pending.
- [ ] Debate still needs more live-flow verification under actual voice conditions.
- [ ] The debate resolve control should not block the stage picture. Acceptance: the election trigger is clear and easy to hit without sitting in the middle of the podium/board reading lane.
- [ ] The bottom composer must not block debate actions. Acceptance: the mic/text bar never intercepts clicks meant for `Call election and advance` or other auditorium controls.

## 12. Realtime Voice / Text System

- [x] Every conversational surface should use `gpt-realtime-1.5`. Acceptance: setup chamber, advisor, citizen, and debate all route through Realtime rather than a patchwork of separate methods.
- [~] Live conversation must feel continuous rather than turn-based. Acceptance: with the mic on, the player can talk, interrupt, listen, and resume naturally without explicit turn friction or waiting for hard stops.
- [~] Response onset should feel immediate. Acceptance: normal replies begin promptly rather than after long dead air.
- [~] Mic-on should mean live natural back-and-forth. Acceptance: the user can interrupt the counterpart and continue without explicit turn friction.
- [~] Counterpart audio should play through cleanly unless the user interrupts. Acceptance: no random mid-sentence cutoffs, chopped endings, or self-interruptions caused by session logic.
- [~] Mic-off should truly pause listening. Acceptance: when paused, the session is not listening, does not keep transcribing, and does not continue reacting to room audio. The main scene mic is now wired to the real toggle path again, but this still needs live human voice verification after the VAD retune.
- [~] Turning the mic back on must not create duplicate voices. Acceptance: pause/resume never stacks a second assistant stream or leaves the old stream alive underneath the new one.
- [~] Interruptions should be user-driven rather than accidental. Acceptance: the system stops because the player barged in, not because VAD or buffer handling clipped it.
- [~] Text and voice should share context. Acceptance: typed turns sit in the same thread and live state as voice turns.
- [~] The mic/text control should be visually simple and consistent across rooms. Acceptance: one central mic/speak control plus inline text box.
- [~] The mic/text shell still needs a stronger design pass. Acceptance: the bottom control bar is centered, elegant, clearly stateful, and no longer reads as an ugly off-center web form.
- [~] Voice start should feel direct and low-friction. Acceptance: hitting `Speak` does not visibly stall on room/admin work before the live channel opens.
- [~] Function calling must work reliably during voice conversations. Acceptance: polls, board updates, room movement, and citizen focus work over voice.
- [~] Room switches must fully isolate live sessions. Acceptance: moving between advisor, citizen, debate, and setup tears down the previous Realtime session cleanly so there is no lingering audio, stale transcript, or identity leak.
- [~] Leaving a room must stop the old counterpart from continuing to talk. Acceptance: moving from advisor to street or debate never leaves the previous speaker audible in the new room.
- [~] `gpt-4o-mini-transcribe` is the live STT path. Acceptance: chamber, advisor, street, and debate sessions actually transcribe through `gpt-4o-mini-transcribe`.
- [x] Current VAD / interruption settings are aligned across backend and frontend. Acceptance: setup and live rooms use the same `semantic_vad` profile with interruption enabled so conversational behavior is at least consistent across surfaces.
- [~] Pause must silence the live channel immediately. Acceptance: turning the mic off mutes any late remote audio, stops listening, and prevents the counterpart from continuing to react until resumed.
- [~] Raw Realtime artifact tokens are now scrubbed at the hook/API boundary. Acceptance: no visible `<|...|>` marker junk in fresh runs.
- [~] Live voice is still not polished enough. Acceptance: no chopped audio, no slow response start, no random cutoffs, no lingering edge-case glitches.

## 13. Polling, Boards, And Qualitative Insight

- [x] Polling is designed to run through `gabriel.poll`, not a fake local abstraction.
- [~] The recurring poll battery is broader than approval/vote. Acceptance: it captures lived AI effects, worries, fairness, service quality, winners, and household security.
- [~] Polling should support more qualitative one-line reactions. Acceptance: quotes can feed loading screens and advisor discussion.
- [ ] Poll ordering should privilege lived experience over horse-race. Acceptance: default poll batteries and loading quotes start from what AI is doing in daily life before approval/vote questions.
- [~] The system should comfortably support a larger question budget without obvious ceilings. Acceptance: recurring standard battery plus ad hoc questions do not choke the run.
- [ ] Polling should stay diegetic. Acceptance: asking for or running polls feels like part of the advisor/scene flow, not a visible operator console, drawer ritual, or exposed admin panel.
- [~] Poll summaries shown to the user should be interpretable and selective. Acceptance: clear labeling and useful subset rather than clutter.
- [~] Board updates should flow through function calls and preserve continuity into later rooms.
- [ ] Advisor-triggered polls must return usable results in the same turn. Acceptance: if the advisor says they ran a poll, the result is actually available and visible rather than disappearing into a failed tool path.
- [ ] Same-stage policy consequences should be legible before the election. Acceptance: polls, citizens, and the debate opponent react to the player’s emerging agenda inside the current stage, not only after stage advancement.

## 14. Scenario Flexibility

- [x] Default scenario should be broad U.S. national.
- [~] Alternate country / system / jurisdiction runs should work. Acceptance: Finland, Swiss education, or state-level runs shift titles, scope, electorate, and mood correctly.
- [~] Different scenario focuses should affect population sampling and political frame. Acceptance: if the player asks for education or state politics, the run really shifts.
- [~] Art direction should be nudgable through setup. Acceptance: the player can ask for a different visual style without breaking the product.
- [ ] Player avatar/presentation presets should be available without clutter. Acceptance: a few simple character presentation choices such as hair, beard, or suit style can be selected or inferred without turning setup into a form.
- [ ] Scenario changes should propagate end-to-end. Acceptance: setup choices consistently affect documentary framing, sampled citizens, polling, debate language, and visual mood across the whole run.

## 15. Theme, Graphic Design, And HUD

- [~] Light mode and dark mode should both feel intentionally art-directed. Acceptance: no washed-out light mode or generic inversion.
- [ ] Light mode needs a full contrast pass across loading, documentary, rooms, and controls. Acceptance: no white-on-sepia washout, low-contrast labels, or unreadable buttons/cards in any stage.
- [ ] Theme toggling should have a visible and reliable effect across states. Acceptance: ready screens, documentaries, loading states, and live rooms all actually change mood and remain readable when the player switches theme.
- [~] The aesthetic should be elegant, cinematic, grounded, and slightly playful. Acceptance: a polished game feel rather than generic enterprise UI.
- [~] Scenario/jurisdiction can influence color mood subtly. Acceptance: U.S., Finland, Swiss education, etc. can shift tone without breaking the shared language.
- [~] GUI elements should support the scene rather than dominate it. Acceptance: panels stay collapsed by default and in-world elements carry more of the interaction.
- [~] Room navigation should feel diegetic and unobtrusive. Acceptance: plaques, exits, and hotspots feel part of the environment.
- [ ] Hotspot placement must respect the board and character composition. Acceptance: navigation plaques and room markers never sit in the key reading lane for boards or faces.
- [ ] The core loop should not depend on drawers or admin rails. Acceptance: setup, advisor work, polling, street conversations, debate, and stage handoff can all be completed from the main scene shell without opening fallback UI.
- [ ] Some controls and room chrome still need another design pass to fully meet the polish bar.

## 16. Stability, Performance, And QA Discipline

- [~] The sim should stay smooth on the current machine. Acceptance: movement, scene rendering, and audio do not feel obviously laggy or broken.
- [~] Stale helper processes should be cleaned up during iteration. Acceptance: one backend, one frontend, minimal stray browser helpers.
- [~] Process hygiene should be part of the workflow. Acceptance: when the machine starts lagging, stale browser/backend/helper processes are deliberately culled before fresh QA passes.
- [~] Browser/runtime cleanliness should be part of QA. Acceptance: live passes are run without stale Playwright piles, and avoidable console/runtime noise is tracked instead of ignored.
- [ ] Stage-by-stage UX inspection is required during live QA. Acceptance: each pass explicitly notes oddities in setup, loading, documentary, advisor room, street, debate, and election handoff before it is considered clean.
- [ ] Fresh-run testing is required. Acceptance: names, roles, prompts, and worlds refresh correctly from scratch.
- [ ] Full-run testing is required. Acceptance: chamber -> loading -> documentary -> advisor -> street -> debate -> election -> next-stage handoff gets exercised.
- [~] Multi-scenario testing is required. Acceptance: broad U.S. plus at least one alternate jurisdiction / lens run gets verified.
- [~] Higher persona-count stability should be tested beyond tiny demo counts. Acceptance: tested persona-count thresholds and graceful degradation are known rather than only assumed.
- [ ] The product still needs more live voice playtesting on fresh runs after the latest fixes.
- [ ] The scene shell still has known performance debt from large 3D chunks and scene complexity.

## 17. Practical “Done” Bar

- [ ] A fresh multi-stage run tells a coherent macro-to-micro story about AI capability, economic change, and political choice.
- [ ] A policymaker can understand the stage world after the documentary without opening side panels.
- [ ] A vague first advisor prompt gets a short, useful, non-pushy response.
- [ ] Talking to several citizens yields clearly different personalities, voices, and lived experiences.
- [ ] A few turns of debate reveal a genuine rival platform and an intellectually serious clash of ideas.
- [ ] Nothing in the visible experience feels like debug cruft, duplicate transcripts, dead buttons, raw model artifacts, or stitched-together app modes.
- [ ] A full run can be completed from setup chamber through next-stage handoff without relying on drawers, hidden admin controls, or fallback UI paths.
