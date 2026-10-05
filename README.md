# Met Wallpaper

Rotating desktop background sourced from the [Met Museum Open Access
collection](https://www.metmuseum.org/art/collection/search) (CC0,
public domain). Every day it picks 24 public-domain landscape paintings,
one per hour, and burns a caption — title, artist, artist's origin and
lifespan, and the work's creation date — into the bottom-right corner of
each image before setting it as the wallpaper.

## How it works

- `omarchy-met-wallpaper-plan` (daily at 00:05, and every 20 minutes as a
  reconciliation retry) keeps a rolling 7-day cache of 24 captioned
  images per day in `~/.local/state/omarchy-met-wallpaper/cache/<date>/`,
  downloading whatever is missing and pruning days that have rolled out
  of the window. If the network is down, it fails fast and simply tries
  again on the next 20-minute tick — no manual reconnection step needed.
- `omarchy-met-wallpaper-rotate` (hourly) sets the wallpaper to today's
  image for the current hour via `omarchy-theme-bg-set`.
- `omarchy-met-wallpaper-status` prints cache depth for the next 7 days
  and the installed timers' state.

Because the cache stays 7 days ahead, a week without connectivity still
has wallpaper available; `plan` reconciles (re-downloads gaps, drops
stale days) as soon as connectivity returns.

## Dependencies

- `python3` with `Pillow` installed (used to crop, resize and caption images)
- `systemd --user` (the three timers below)
- `hyprctl` (reads the active monitor's aspect ratio; falls back to 16:9 if unavailable)
- Outbound network access to `collectionapi.metmuseum.org` and
  `images.metmuseum.org` — no credentials or API key required

## Install

```sh
omarchy plugin add https://github.com/Pixel-Jack/omarchy-met-wallpaper.git --enable
~/.config/omarchy/plugins/io.github.pixel-jack.met-wallpaper/install
```

`install` sets up the three systemd --user timers, symlinks the CLI
commands into `~/.local/bin`, and kicks off the first cache fill in the
background. It's idempotent, safe to re-run.

This plugin has no bar widget or panel — it only runs background
automation, so enabling/disabling it in the Omarchy plugin list has no
effect either way.

## Remove

```sh
~/.config/omarchy/plugins/io.github.pixel-jack.met-wallpaper/uninstall
omarchy plugin remove io.github.pixel-jack.met-wallpaper
```

`uninstall` stops and removes the timers and `~/.local/bin` symlinks but
leaves downloaded images in `~/.local/state/omarchy-met-wallpaper` in place.

## Data source and attribution

Images and metadata come from the Met's public Open Access API
(`collectionapi.metmuseum.org`, CC0) and image CDN
(`images.metmuseum.org`); both are fine to poll on an unattended
schedule. No credentials or API key are required.
