# Dumac Download Manager

A free download manager for macOS. Download videos from YouTube, Instagram, TikTok, Facebook, X, Vimeo and more, and speed up ordinary file downloads, all from one Mac app.

- **"Download this video" button** on video pages in Brave, Chrome, Edge and Vivaldi, with every available quality (up to 4K) and MP3.
- **Takes over browser downloads**: click a download link and it goes to the app, with several connections at once for speed.
- **Mac app** with a download list, live progress (speed, time left), pause/resume, categories, a queue, a scheduler, a speed limit and a proxy setting.
- **Safari** bookmark button, a **clipboard watcher** ("Download this link?") and a floating **drop target**.
- **Auto-retry** when the connection drops; resumes where it stopped.

It's built on [yt-dlp](https://github.com/yt-dlp/yt-dlp) for videos and [aria2](https://aria2.github.io/) for files.

## Install

You need macOS 13 or later, [Homebrew](https://brew.sh), and Apple's Command Line Tools (`xcode-select --install`).

```bash
git clone https://github.com/kaydudrone/dumac-download-manager.git
cd dumac-download-manager
./install.sh
```

The installer adds any missing tools (aria2, deno, ffmpeg, python) with Homebrew, puts a private copy of yt-dlp in `~/Library/Application Support/Dumac Download Manager`, builds the app into `~/Applications`, and connects the browsers you have. It installs nothing system-wide and adds no background jobs or login items. Run it again any time to update.

Then:

1. Open **Dumac Download Manager** from Launchpad. If macOS asks for access to your Downloads folder, click **Allow**.
2. **Browser extension** (Brave, Chrome, Edge, Vivaldi): open the browser's extensions page (`brave://extensions`, `chrome://extensions`…), turn on **Developer mode**, click **Load unpacked** and choose the `extension` folder of this repo.
3. **Safari**: open `macapp/safari-button.html` in Safari and drag the button to the Favorites bar.

Keep the cloned folder: the browser loads the extension from it.

## Use

| You want to… | Do this |
|---|---|
| Download a video | On the video page, click **Download this video** and pick a quality |
| Download a file | Click the download link in your browser. The app takes it over |
| Add a link yourself | In the app: **Add URL**, then **Start Download** or **Download Later** |
| Download overnight | **Download Later** a few links, then **Scheduler**: pick a time, and optionally sleep or shut down when done |
| Pause / continue | **Stop** / **Resume** |

**1080p** downloads as H.264 and plays everywhere. **4K** downloads as AV1, which plays in QuickTime on Apple Silicon Macs.

## Privacy

- Everything runs on your Mac. No accounts, no analytics, no servers.
- For sites that need you signed in (for example Instagram), the extension passes that site's cookies from your browser to the local helper for that one download. They're written to a file only you can read, and deleted when the download finishes. Turn this off in **Options → Use my browser login**.
- **YouTube** sometimes answers "Sign in to confirm you're not a bot". **Options → Use my YouTube login** fixes it by using your browser's YouTube sign-in. It's off by default, because YouTube can flag accounts that download a lot.

## Please download responsibly

Only download content you own, that's freely licensed, or that you have permission to download, and respect each site's terms of service and copyright law where you live. This tool is provided as is, without warranty.

## Troubleshooting

| Problem | Fix |
|---|---|
| "Sign in to confirm you're not a bot" (YouTube) | Wait a few hours, or turn on **Options → Use my YouTube login** |
| A download fails with "Error" | Re-run `./install.sh` (it updates yt-dlp), then click **Resume** |
| The extension says "Helper not installed" | Re-run `./install.sh`, then reload the extension |
| No button on a video page | Refresh the page. The button only appears on single-video pages, not feeds |
| The app's list is empty | Quit and reopen it. If macOS asks for Downloads access, click **Allow** |

## Uninstall

```bash
./uninstall.sh
```

This removes the app and the browser connection. Your downloaded files are never touched.

## Credits

[yt-dlp](https://github.com/yt-dlp/yt-dlp) (Unlicense), [aria2](https://github.com/aria2/aria2) (GPL-2.0), [deno](https://deno.com) (MIT), [FFmpeg](https://ffmpeg.org) (LGPL/GPL), toolbar icons from [Lucide](https://lucide.dev) (ISC). These are installed separately and are not bundled in this repository.

Not affiliated with Tonec Inc. or any other download manager.

## Licence

MIT. See [LICENSE](LICENSE).
