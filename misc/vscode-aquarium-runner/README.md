# MacWS VS Code Bridge and Aquarium Runner

The extension owns MacWS's event-driven web-link endpoint at
`/private/tmp/macws_vscode_url.sock`. The root-side host sends one bounded,
length-prefixed HTTP(S) URL per connection and considers the request complete
only after `simpleBrowser.show` resolves and the extension returns its one-byte
acknowledgement. There is no polling loop while VS Code is idle.

This disposable VS Code extension opens the WebGL Aquarium workload in the
built-in Simple Browser when `MacWS: Open WebGL Aquarium` is selected from the
command palette. It exists because Electron 42 rejects browser-level
`Target.createTarget`, while navigating the workbench target directly causes
VS Code to replace that renderer.

Set `macwsAquarium.url` in the disposable benchmark profile to change the fish
count or canvas dimensions. Automatic startup is deliberately disabled in the
production profile: a 60,000-fish renderer otherwise competes with ordinary
pages and video playback for the same Chromium GPU process and native-AGX
resource budget. `macwsAquarium.openOnStartup` remains available for a
dedicated benchmark profile.

Startup is deliberately idempotent. VS Code restores Simple Browser webviews
from the disposable profile, while `simpleBrowser.show` always creates another
panel. The extension reuses one restored `WebGL Aquarium` webview and closes
only duplicate benchmark webviews before deciding whether a new one is needed.
This prevents repeated benchmark launches from accumulating independent
Chromium renderers and native-AGX resource graphs.

The dedicated `agx-native-production1` benchmark profile also uses
`../vscode-production-settings.json`. Copy it to
`/tmp/macws-vscode-profile-agx-native-production1/User/settings.json` inside
the macOS root before loading `com.macwsguide.vscode`. It disables VS Code
1.130's optional AgentHost (Copilot/Claude background providers) and terminal
process persistence for this disposable graphics benchmark profile. Neither
setting disables the extension host, Simple Browser, WebGL2, Chromium JIT, or
native AGX. The tracked production settings also pin the comparison workload
to 60,000 fish at 1024 x 1024; every recorded run must verify the page's
`fish` and `modelFish` counters instead of trusting the URL alone.
