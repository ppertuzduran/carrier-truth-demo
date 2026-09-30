# Research challenge: screen sharing without an app

**Ask:** send a client a link they can open on their phone without installing an app, and after permission, see
their screen while they browse other sites or apps, to guide them remotely.

**Verdict:** on desktop this is possible with just a link. On iPhone and Android, the major browsers don't support
screen capture, so seeing other apps requires a native app. The proposal is a single link that detects the platform
and picks the mechanism.

Only facts checked against the sources listed at the end are included.

## Desktop

- `getDisplayMedia()` is supported in Chrome 72+, Edge 79+, Firefox 66+ and Safari 13+ (MDN browser-compat-data).
- The W3C Screen Capture spec requires:
  - a secure context (HTTPS)
  - transient user activation (a click)
  - the user choosing the surface to share every time; the page cannot impose it

  If the user picks the entire screen, the agent sees any site or app.
- In Chrome, `displaySurface` only expresses a preference (tab, window or screen).
- For legible text, the video track can be marked with `contentHint = "detail"` or `"text"` (MDN).

## Android

- `getDisplayMedia` is not supported in Chrome or Firefox for Android (MDN).
- The native path is **MediaProjection**. It captures the whole screen or, since Android 14, a single app.
- MediaProjection requires:
  - consent before each session
  - a `MediaProjection` token used only once
  - a foreground service of type `mediaProjection`
- Since Android 15 QPR1, a status-bar chip shows the capture in progress, and projection stops when the screen locks.

## iPhone

- `getDisplayMedia` is not supported in Safari on iOS (MDN).
- The native path is **ReplayKit**:
  - `RPScreenRecorder` records "your app" only.
  - Broadcasting is done with app extensions; `RPBroadcastSampleHandler` processes the buffers.
  - `RPSystemBroadcastPickerView` shows the broadcast picker.
- All of it requires an installed app.

## What is not possible, and why

- On mobile, the only web API for screen capture (`getDisplayMedia`) is not supported, so a web page cannot see other
  apps or sites.
- On no platform can capture happen without the user choosing what to share: the spec explicitly forbids it.
- Co-browsing third-party sites through a proxy is ruled out: it would mean intercepting the client's sessions and
  credentials.

## Proposed solution

1. **Link:** a single-use, short-lived token, sent by SMS or WhatsApp like Live View today. It opens a consent page that
   records IP and time.
2. **Route by platform:**
   - **Desktop:** `getDisplayMedia` → WebRTC server (SFU) → Copilot panel. Candidate to evaluate: LiveKit, which has
     web, Swift and Android SDKs.
   - **Mobile, no install:** co-browsing of PBG's own pages (application review, documents, signature) with rrweb,
     which records the DOM through `MutationObserver`. The session is replayed in the Copilot.
     - Privacy: `maskAllInputs`, `maskTextClass` and `blockClass`.
     - Limits: cross-origin iframes need rrweb injected in each one, and canvas isn't recorded by default.
   - **Mobile, full screen:** a minimal companion app (MediaProjection on Android, a ReplayKit extension on iOS),
     opened from the same link and joined to the same session.

**Validate in a 2–3 day prototype:**
- latency and data usage on mobile networks
- how many sessions actually need to leave PBG's own flow, which decides whether the companion app is justified

## Sources

- MDN browser-compat-data, `MediaDevices.getDisplayMedia`: https://github.com/mdn/browser-compat-data/blob/main/api/MediaDevices.json
- W3C, Screen Capture: https://www.w3.org/TR/screen-capture/
- Chrome for Developers, screen sharing controls: https://developer.chrome.com/docs/web-platform/screen-sharing-controls
- MDN, `MediaStreamTrack.contentHint`: https://developer.mozilla.org/en-US/docs/Web/API/MediaStreamTrack/contentHint
- Android Developers, Media projection: https://developer.android.com/media/grow/media-projection
- Apple, ReplayKit: https://developer.apple.com/documentation/replaykit
- rrweb guide: https://github.com/rrweb-io/rrweb/blob/master/guide.md
- LiveKit docs: https://docs.livekit.io/
