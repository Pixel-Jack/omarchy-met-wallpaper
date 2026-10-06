#!/usr/bin/env python3
"""Fetch, process and schedule Met Museum landscape paintings as rotating wallpaper.

Subcommands:
  plan    - ensure the next N days have IMAGES_PER_DAY captioned images cached,
            promote today's set active, and prune stale days (safe to run often).
  status  - print cache/plan state as JSON.
  path    - print the active image path for a given hour slot (used by rotate).
  list    - print {paused, items} for today, items used by the bar panel.
  skip    - print the path of the next ready image after the current one.
  use     - print the path for a given hour slot, regardless of the clock.
  toggle-pause - flip the paused flag, print "paused" or "resumed".
  is-paused    - exit 0 if paused, 1 otherwise (used by rotate to no-op).
"""
import io
import json
import random
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

IMAGES_PER_DAY = 24
FORWARD_DAYS = 7
MAX_FETCHES_PER_RUN = 40
MAX_IMAGE_WIDTH = 3840
SEARCH_URL = "https://collectionapi.metmuseum.org/public/collection/v1.1/search"
OBJECT_URL = "https://collectionapi.metmuseum.org/public/collection/v1/objects/{}"
POOL_MAX_AGE_SECS = 7 * 86400
USED_HORIZON = IMAGES_PER_DAY * (FORWARD_DAYS + 2)  # avoid repeats within ~9 days
REQUEST_TIMEOUT = 8
USER_AGENT = "omarchy-met-wallpaper/1.0 (personal wallpaper plugin; contact: local user)"

STATE_DIR = Path.home() / ".local/state/omarchy-met-wallpaper"
CACHE_DIR = STATE_DIR / "cache"
POOL_FILE = STATE_DIR / "pool.json"
INDEX_FILE = STATE_DIR / "index.json"

FONT_CANDIDATES_REGULAR = [
    "/usr/share/fonts/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
]
FONT_CANDIDATES_BOLD = [
    "/usr/share/fonts/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
]


class OfflineError(Exception):
    pass


TARGET_ASPECT = None  # set by cmd_plan() via detect_target_aspect()
MIN_ASPECT = None
MAX_ASPECT = None


def detect_target_aspect():
    # Omarchy's background renderer shares one image across every connected
    # monitor, independently applying Image.PreserveAspectCrop per screen. With
    # more than one monitor, no single crop is exactly right for all of them,
    # so this picks the aspect that minimizes the worst-case crop on either
    # side: the geometric mean of the narrowest and widest connected monitor.
    # render_caption() then pads the caption's margins by that worst-case crop
    # so it stays on-screen everywhere, not just on whichever monitor this
    # happened to be focused when the image was cropped.
    try:
        out = subprocess.run(
            ["hyprctl", "monitors", "-j"], capture_output=True, timeout=3, check=True
        ).stdout
        monitors = json.loads(out)
        aspects = [m["width"] / m["height"] for m in monitors if m.get("width") and m.get("height")]
        if not aspects:
            raise ValueError("hyprctl reported no monitors")
        lo, hi = min(aspects), max(aspects)
        return (lo * hi) ** 0.5, lo, hi
    except Exception as e:
        log(f"could not detect monitor aspect ratios, defaulting to 16:9: {e}")
        return 16 / 9, 16 / 9, 16 / 9


def crop_to_aspect(image, aspect):
    w, h = image.size
    current = w / h
    if abs(current - aspect) < 0.01:
        return image
    if current > aspect:
        new_w = round(h * aspect)
        x0 = (w - new_w) // 2
        return image.crop((x0, 0, x0 + new_w, h))
    new_h = round(w / aspect)
    y0 = (h - new_h) // 2
    return image.crop((0, y0, w, y0 + new_h))


def log(msg):
    print(f"[omarchy-met-wallpaper] {msg}", file=sys.stderr)


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return resp.read()
    except urllib.error.HTTPError:
        # A real response with a bad status (404, 500, ...) - the server is
        # reachable, so this isn't a connectivity problem. Let it propagate
        # as a normal error for the caller to handle per-request.
        raise
    except (urllib.error.URLError, TimeoutError) as e:
        raise OfflineError(str(e)) from e


def http_get_bytes(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return resp.read()
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, TimeoutError) as e:
        raise OfflineError(str(e)) from e


def load_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


POOL_PAGE_SIZE = 500  # the API caps objectIDs per request to this regardless of "limit"
POOL_MAX_IDS = 3000


def fetch_all_ids(query_params):
    ids = []
    offset = 0
    while len(ids) < POOL_MAX_IDS:
        params = f"{query_params}&limit={POOL_PAGE_SIZE}&offset={offset}"
        body = json.loads(http_get(f"{SEARCH_URL}?{params}"))
        page = body.get("objectIDs") or []
        ids.extend(page)
        if len(page) < POOL_PAGE_SIZE:
            break
        offset += POOL_PAGE_SIZE
    return ids


def load_pool():
    pool = load_json(POOL_FILE, None)
    if pool and time.time() - pool.get("fetched_at", 0) < POOL_MAX_AGE_SECS and pool.get("ids"):
        return pool
    log("refreshing candidate pool from the Met API")
    ids = fetch_all_ids("q=landscape&hasImages=true&isPublicDomain=true&medium=Paintings")
    if not ids:
        # medium filter rejected everything (unsupported param, etc.); fall back.
        ids = fetch_all_ids("q=landscape&hasImages=true&isPublicDomain=true")
    pool = {"fetched_at": time.time(), "ids": ids}
    save_json(POOL_FILE, pool)
    return pool


def load_index():
    return load_json(INDEX_FILE, {"used_ids": [], "queue": []})


def save_index(index):
    index["used_ids"] = index["used_ids"][-USED_HORIZON:]
    save_json(INDEX_FILE, index)


def next_candidate(index, pool):
    while True:
        if not index["queue"]:
            used = set(index["used_ids"])
            fresh = [i for i in pool["ids"] if i not in used]
            if not fresh:
                fresh = list(pool["ids"])
            random.shuffle(fresh)
            index["queue"] = fresh
            if not index["queue"]:
                return None
        yield index["queue"].pop(0)


def looks_like_painting(obj):
    classification = (obj.get("classification") or "").lower()
    medium = (obj.get("medium") or "").lower()
    return "paint" in classification or "paint" in medium


# The Met API has no content-rating field. Its `tags` array uses a controlled
# subject vocabulary that does include nudity as a genre, e.g. "Female Nudes"
# and "Male Nudes" (confirmed against real objects) - this is a heuristic, not
# a guarantee, but it catches the tagged cases without needing image analysis.
NSFW_TAG_KEYWORDS = ("nude", "erotic", "sexual")


def is_safe_for_work(obj):
    for tag in obj.get("tags") or []:
        term = (tag.get("term") or "").lower()
        if any(keyword in term for keyword in NSFW_TAG_KEYWORDS):
            return False
    return True


def artist_timerange(obj):
    begin, end = obj.get("artistBeginDate"), obj.get("artistEndDate")
    if begin and end:
        return f"{begin}–{end}"
    return ""


def build_caption(obj):
    lines = []
    title = (obj.get("title") or "Untitled").strip()
    lines.append(title)

    artist = (obj.get("artistDisplayName") or "Unknown artist").strip()
    origin = (obj.get("artistNationality") or "").strip()
    timerange = artist_timerange(obj)
    artist_line = artist
    extra = " · ".join(p for p in (origin, timerange) if p)
    if extra:
        artist_line += f" ({extra})"
    lines.append(artist_line)

    date_display = (obj.get("objectDate") or "").strip()
    if date_display:
        lines.append(date_display)

    return lines


def find_font(candidates, size):
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def render_caption(image, lines):
    image = image.convert("RGB")
    width, height = image.size
    title_size = max(16, width // 65)
    body_size = max(13, width // 90)
    title_font = find_font(FONT_CANDIDATES_BOLD, title_size)
    body_font = find_font(FONT_CANDIDATES_REGULAR, body_size)

    wrap_width = max(20, width // (title_size // 2 or 1))
    wrapped = [(textwrap.fill(lines[0], wrap_width), title_font)]
    for line in lines[1:]:
        wrapped.append((textwrap.fill(line, wrap_width + 10), body_font))

    draw = ImageDraw.Draw(image, "RGBA")
    padding = max(12, width // 100)
    line_gap = max(4, padding // 3)

    rendered = []
    block_w = 0
    block_h = 0
    for text, font in wrapped:
        bbox = draw.multiline_textbbox((0, 0), text, font=font, spacing=line_gap)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        rendered.append((text, font, w, h))
        block_w = max(block_w, w)
        block_h += h + line_gap
    block_h -= line_gap

    margin = max(20, width // 60)

    # Extra inset so the caption survives being cropped again for a different
    # monitor's aspect ratio (see detect_target_aspect()), not just the one
    # TARGET_ASPECT was computed for.
    extra_right = 0
    extra_bottom = 0
    if MIN_ASPECT and MIN_ASPECT < TARGET_ASPECT:
        extra_right = round((1 - MIN_ASPECT / TARGET_ASPECT) / 2 * width)
    if MAX_ASPECT and MAX_ASPECT > TARGET_ASPECT:
        extra_bottom = round((1 - TARGET_ASPECT / MAX_ASPECT) / 2 * height)

    box_x1 = width - margin - extra_right
    box_y1 = height - margin - extra_bottom
    box_x0 = box_x1 - block_w - 2 * padding
    box_y0 = box_y1 - block_h - 2 * padding
    draw.rounded_rectangle(
        (box_x0, box_y0, box_x1, box_y1), radius=padding, fill=(0, 0, 0, 140)
    )

    y = box_y0 + padding
    for text, font, w, h in rendered:
        x = box_x1 - padding - w
        draw.multiline_text((x, y), text, font=font, fill=(255, 255, 255, 235), spacing=line_gap)
        y += h + line_gap

    return image


def fetch_and_process(object_id):
    obj = json.loads(http_get(OBJECT_URL.format(object_id)))
    if not obj.get("isPublicDomain") or not obj.get("primaryImage"):
        return None
    if not looks_like_painting(obj):
        return None
    if not is_safe_for_work(obj):
        return None

    raw = http_get_bytes(obj["primaryImage"])
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception:
        return None

    if image.width <= image.height:
        return None  # prefer landscape-oriented canvases for a desktop background

    image = crop_to_aspect(image, TARGET_ASPECT)

    if image.width > MAX_IMAGE_WIDTH:
        ratio = MAX_IMAGE_WIDTH / image.width
        image = image.resize((MAX_IMAGE_WIDTH, int(image.height * ratio)), Image.LANCZOS)

    captioned = render_caption(image, build_caption(obj))

    meta = {
        "object_id": object_id,
        "title": obj.get("title"),
        "artist": obj.get("artistDisplayName"),
        "origin": obj.get("artistNationality"),
        "timerange": artist_timerange(obj),
        "date_display": obj.get("objectDate"),
        "source_url": obj.get("objectURL"),
    }
    return captioned, meta


def day_dir(d):
    return CACHE_DIR / d.isoformat()


def load_day_manifest(d):
    manifest_path = day_dir(d) / "manifest.json"
    data = load_json(manifest_path, {})
    slots = data.get("slots", [None] * IMAGES_PER_DAY)
    if len(slots) != IMAGES_PER_DAY:
        slots = (slots + [None] * IMAGES_PER_DAY)[:IMAGES_PER_DAY]
    return slots


def save_day_manifest(d, slots):
    save_json(day_dir(d) / "manifest.json", {"date": d.isoformat(), "slots": slots})


def slot_valid(d, slot):
    if not slot:
        return False
    f = day_dir(d) / slot["file"]
    return f.exists() and f.stat().st_size > 0


def ensure_day(d, index, pool, budget):
    slots = load_day_manifest(d)
    missing = [i for i in range(IMAGES_PER_DAY) if not slot_valid(d, slots[i])]
    if not missing:
        return slots, budget

    day_dir(d).mkdir(parents=True, exist_ok=True)
    candidates = next_candidate(index, pool)
    for hour in missing:
        if budget <= 0:
            break
        filled = False
        for _ in range(30):  # bounded search for a usable candidate per slot
            if budget <= 0:
                break
            object_id = next(candidates, None)
            if object_id is None:
                break
            budget -= 1
            try:
                result = fetch_and_process(object_id)
            except OfflineError:
                raise
            except Exception as e:
                log(f"skipping object {object_id}: {e}")
                continue
            if result is None:
                continue
            image, meta = result
            filename = f"{hour:02d}.jpg"
            image.save(day_dir(d) / filename, "JPEG", quality=88)
            slots[hour] = {"file": filename, **meta}
            index["used_ids"].append(object_id)
            filled = True
            break
        if not filled:
            log(f"could not fill slot {hour:02d} for {d.isoformat()} this run")

    save_day_manifest(d, slots)
    return slots, budget


def prune_old_days(keep_dates):
    if not CACHE_DIR.exists():
        return
    keep = {d.isoformat() for d in keep_dates}
    for entry in CACHE_DIR.iterdir():
        if entry.is_dir() and entry.name not in keep:
            for f in entry.iterdir():
                f.unlink()
            entry.rmdir()
            log(f"pruned stale cache day {entry.name}")


def cmd_plan():
    global TARGET_ASPECT, MIN_ASPECT, MAX_ASPECT
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    TARGET_ASPECT, MIN_ASPECT, MAX_ASPECT = detect_target_aspect()
    today = date.today()
    window = [today + timedelta(days=i) for i in range(FORWARD_DAYS)]

    try:
        pool = load_pool()
    except OfflineError as e:
        log(f"offline, cannot refresh pool yet: {e}")
        pool = load_json(POOL_FILE, None)
        if not pool:
            print("offline:no-pool")
            return
    except urllib.error.HTTPError as e:
        log(f"Met API search returned {e.code}, cannot refresh pool yet: {e}")
        pool = load_json(POOL_FILE, None)
        if not pool:
            print("error:no-pool")
            return

    index = load_index()
    budget = MAX_FETCHES_PER_RUN
    offline = False
    for d in window:
        try:
            _, budget = ensure_day(d, index, pool, budget)
        except OfflineError as e:
            log(f"network unavailable while filling {d.isoformat()}: {e}")
            offline = True
            break
        if budget <= 0:
            break

    save_index(index)
    prune_old_days(window)
    print("offline" if offline else "ok")


def cmd_status():
    today = date.today()
    window = [today + timedelta(days=i) for i in range(FORWARD_DAYS)]
    report = {"today": today.isoformat(), "days": []}
    for d in window:
        slots = load_day_manifest(d)
        valid = sum(1 for s in slots if slot_valid(d, s))
        report["days"].append({"date": d.isoformat(), "ready": valid, "total": IMAGES_PER_DAY})
    print(json.dumps(report, indent=2))


def cmd_path(hour):
    today = date.today()
    slots = load_day_manifest(today)
    slot = slots[hour % IMAGES_PER_DAY]
    if not slot_valid(today, slot):
        return 1
    print(day_dir(today) / slot["file"])
    return 0


CURRENT_BACKGROUND_LINK = Path.home() / ".local/state/omarchy/current/background"


def current_background_path():
    try:
        return CURRENT_BACKGROUND_LINK.resolve()
    except OSError:
        return None


def ready_hours(d, slots):
    return [h for h in range(IMAGES_PER_DAY) if slot_valid(d, slots[h])]


def cmd_list():
    today = date.today()
    slots = load_day_manifest(today)
    current = current_background_path()
    items = []
    for hour in ready_hours(today, slots):
        slot = slots[hour]
        path = day_dir(today) / slot["file"]
        items.append({
            "hour": hour,
            "file": str(path),
            "title": slot.get("title") or "Untitled",
            "artist": slot.get("artist") or "Unknown artist",
            "active": current is not None and path.resolve() == current,
        })
    paused = load_index().get("paused", False)
    print(json.dumps({"paused": paused, "items": items}))


def cmd_toggle_pause():
    index = load_index()
    index["paused"] = not index.get("paused", False)
    save_index(index)
    print("paused" if index["paused"] else "resumed")


def cmd_is_paused():
    return 0 if load_index().get("paused", False) else 1


def cmd_skip():
    today = date.today()
    slots = load_day_manifest(today)
    hours = ready_hours(today, slots)
    if not hours:
        return 1
    current = current_background_path()
    cur_idx = None
    for i, h in enumerate(hours):
        if current is not None and (day_dir(today) / slots[h]["file"]).resolve() == current:
            cur_idx = i
            break
    next_hour = hours[(cur_idx + 1) % len(hours)] if cur_idx is not None else hours[0]
    print(day_dir(today) / slots[next_hour]["file"])
    return 0


def cmd_use(hour):
    today = date.today()
    slots = load_day_manifest(today)
    if hour < 0 or hour >= IMAGES_PER_DAY or not slot_valid(today, slots[hour]):
        return 1
    print(day_dir(today) / slots[hour]["file"])
    return 0


def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    cmd = sys.argv[1]
    if cmd == "plan":
        cmd_plan()
        return 0
    if cmd == "status":
        cmd_status()
        return 0
    if cmd == "path":
        hour = int(sys.argv[2]) if len(sys.argv) > 2 else datetime.now().hour
        return cmd_path(hour)
    if cmd == "list":
        cmd_list()
        return 0
    if cmd == "skip":
        return cmd_skip()
    if cmd == "use":
        if len(sys.argv) < 3:
            print("usage: use <hour>", file=sys.stderr)
            return 2
        return cmd_use(int(sys.argv[2]))
    if cmd == "toggle-pause":
        cmd_toggle_pause()
        return 0
    if cmd == "is-paused":
        return cmd_is_paused()
    print(f"unknown subcommand: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
