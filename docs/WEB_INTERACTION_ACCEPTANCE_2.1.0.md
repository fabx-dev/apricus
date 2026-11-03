# Visible acceptance — installed x14, GNOME integration rev8

2026-11-03/04. **Release BLOCKED.** Manual results below are user confirmations,
not assistant-observed screenshots or proof of audible playback.

Candidate source: `afbbcb2516efae31c644d70ca81676ca308becc3`.

| Check | Result | Evidence scope |
|---|---|---|
| Installed candidate | PASS | `snap list apricus` reports x14 |
| Interact → Done → Interact | PASS | User: “ok il comportamento funziona” |
| Text and counter preserved after Done/re-entry, without reapplying | PASS | User explicitly confirmed the simplified `ciao`/counter test |
| HTML dialog closes with Esc while interaction and Done remain active | PASS | User explicitly confirmed |
| Dedicated exit shortcut returns to desktop and removes Done | PASS | User: “funziona” following that exact test |
| Fullscreen external application; Done visible and usable | PASS | User: “funzioona” after instructions to fullscreen another app, enter Interact and click Done |
| Maximized external application remains unchanged | PASS (user-reported) | User reports no changes to the maximized app and then confirms Interact → Done while remaining on the webpage. Returning focus to another app also ends interaction by design; actual server check confirmed WALLPAPER, available=true, reason empty, not surface-hidden. |
| Minimized external application remains minimized | PASS (user-reported) | User: “si confermato” following the explicit minimize → Interact → Done → still minimized test; no claim about the separate historical minimization anomaly's cause. |
| GUI closure during active interaction | PASS (user-reported) | Initial attempt did not include clicking Done and was not counted. User repeated explicit instructions: reopen GUI → scheduled native GUI close → Interact → wait for GUI to close → click Done before returning to chat, then confirmed “rifatto ora, ti confermo”. Earlier independent check verified real GUI-window closure; the helper now correctly treats exited/zombie processes as exited. |
| Manual Stop / explicit Resume | PASS (user-reported) | User explicitly confirms Stop disables Interact and only explicit Resume makes it available again. |
| Manual Pause in Web Page GUI | NOT APPLICABLE to this UI control | Source intentionally disables Play/Pause for a running web wallpaper and provides Stop/Resume. The earlier instruction to click Pause was incorrect; no manual-Pause GUI PASS is recorded. Engine/API latch coverage remains automated, not installed acceptance. |
| Automatic pause during active interaction, no automatic re-entry | PASS (installed x14, user + bounded snapshots) | User: “tutto come previsto”. `policy-check-result.json` records native GdkWaylandDisplay fixture; INTERACTIVE/playing before and at maximize request; fixture not focused when maximize requested/notified; then maximized reason → paused/WALLPAPER; unmaximize → playing/WALLPAPER/available=true, without re-entry. Helper changed no preferences. No end-to-end latency or decoded/audible playback claim. |
| Source changes during active interaction | PASS (installed x14, user + bounded snapshots) | `source-change-result.json`: before INTERACTIVE, after WALLPAPER/available=true, explicit second local source selected, manual latches preserved. User: “esatto”, confirming green page appeared, Done disappeared, re-entry worked and “Verifica pulsante” produced “Pulsante OK”. This explicitly changes source and does not claim DOM preservation across a source replacement. |
| Bounded unresponsive-JavaScript recovery | PASS (user-reported) | User: “si funziona” following the explicit 12-second JS-block fixture → click Done while the page is unresponsive test. User confirms immediate usability; no instrumented latency measurement is claimed. |
| Native identity / reload counts in visible session | NOT TESTED directly | User confirms content persistence; separate private strict regression preserves native identities and load counts |
| Local moving video, actual audible audio, manual Pause/Resume | PASS (user-reported, installed x14) | User: “tutto ok” after explicit instructions to observe motion and listen to audio, Pause both immediately, hold about 5 seconds, then Play from the stopped content rather than restart. No instrumented sub-500 ms timing claim. |
| Mute/unmute on local video | PASS (user-reported, installed x14) | User: “si”, confirming audio disappears while motion continues on Mute and audible audio returns on unmute. |
| One working YouTube sample | PASS (user-reported, installed x14) | User: “tutto come previsto” after instructions to use one already-working YouTube sample, observe motion and listen to audio, Pause, hold about 5 seconds and Play from the same content rather than restart. No general reliability claim or resolution of the separately known stalling sample. |
| Video/audio automatic pause and resume with GUI closed | PASS (user-reported, installed x14) | User: “tutto corretto” after leaving the working sample playing, closing only Apricus GUI, maximizing then restoring another app, and repeating fullscreen/normal with F11. User confirms audio stops and audio/video resume from paused content without reopening GUI. No instrumented latency or frame-accurate continuity claim. |
| Done through Ctrl+Alt+Tab and Enter | PASS (user-reported) | User: “ok” after the explicit keyboard-only group selection, focus and Enter activation test. |
| Desktop icons / DING and Overview | PASS (user-reported) | User: “si”, confirming icons remain visible/selectable after Done and Super/Overview does not expose the wallpaper as a separate application window. |
| Formal screen-reader and contrast accessibility | NOT TESTED comprehensively | Keyboard group/focus and visible usable Done are confirmed, but not a formal accessibility audit. |

Earlier rev7 shortcut and rev8 nullable-title failures remain documented in
`docs/RELEASE_READINESS_2.1.0.md`; these new passes do not retroactively certify
older packages or establish the cause of the older minimization anomaly.
No publication or remote Git action has been performed.

Operational-message issue observed in policy snapshots: after healthy automatic
resume, available=true/state=WALLPAPER still carries reason="policy". The GUI's
generic fallback may present this completed non-error exit reason as a failure.
Also review the initial stale integration-unavailable reason when ready=true.
The same non-error status issue appears after successful source replacement:
available=true/state=WALLPAPER with reason="source-changed" despite successful
visible replacement and re-entry. Do not label these completed operations as
functional failures or dismiss the misleading GUI-message issue.
This is a status-message/readiness issue, not evidence that policy resume failed;
retain it for correction before final release acceptance.
The post-x14 local correction now suppresses only completed non-error transition
reasons in a healthy WALLPAPER state; actual bridge/eligibility errors and failed
operations remain visible. It changes no policy, player or Shell transition.
Validation: **668 passed, 5 skipped, 31 warnings**, Ruff lint/format PASS.
Its packaged/installed status-message check remains NOT TESTED until performed;
the functional confirmations above are specifically for installed x14.
