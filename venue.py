import requests
from curl_cffi import requests as cffi_requests
import time
import json
import os
import re
import signal
import copy
import io
import base64
import urllib.request
from PIL import Image, ImageDraw, ImageFont
import subprocess
from datetime import datetime, timezone, timedelta
import threading
import builtins
import hashlib

# --- GLOBAL LOCKS ---
state_lock = threading.Lock()
warp_lock = threading.Lock()
print_lock = threading.Lock()

# Thread-safe print override
def safe_print(*args, **kwargs):
    with print_lock:
        builtins.print(*args, **kwargs)
print = safe_print

# --- CONFIGURATION ---
dates_env = os.getenv("DATES", "")
DATES = [d.strip() for d in dates_env.split(",") if d.strip()]

VENUE_CODE = os.getenv("VENUE_CODE", "").strip()
TARGET_MOVIE = os.getenv("TARGET_MOVIE", "").strip()
CITY = os.getenv("CITY", "hyderabad").strip().lower()
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL", "3"))

dates_hash = hashlib.md5(dates_env.encode('utf-8')).hexdigest()[:8]
clean_movie = "".join(c for c in TARGET_MOVIE if c.isalnum()) or "ALL"
STATE_FILE = f"state_shows_{VENUE_CODE}_{clean_movie}_{dates_hash}.json"

MAX_RUNTIME_SECONDS = (5 * 3600) + (55 * 60)

GLOBAL_STATE = {}
UNPUSHED_DELTAS = {}

SUPABASE_URL = "https://edqxafyqqkhxuipzcjcd.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVkcXhhZnlxcWtoeHVpcHpjamNkIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODcyOTYzNzIsImV4cCI6MjEwMjg3MjM3Mn0.dAerrc1sTh8CSnY6vZ4NtuvPXWNwjsHCl9gWZ436MLk"

def stage_tweet_supabase(tweet_text, image_base64):
    url = f"{SUPABASE_URL}/rest/v1/staged_tweets"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal"
    }
    payload = {
        "tweet_text": tweet_text,
        "image_base64": image_base64
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=15)
    if resp.status_code not in (200, 201, 204):
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")

USE_WARP = False

PROXIES = {
    "http": "socks5://127.0.0.1:40000",
    "https": "socks5://127.0.0.1:40000"
}

BMS_HEADERS = {
    "Host": "cinemas.bookmyshow.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Origin": "https://cinemas.bookmyshow.com",
    "Referer": "https://cinemas.bookmyshow.com"
}

def matches_movie(candidate_title, target):
    """Safest net: strips symbols and compares lowercase alphanumeric sequences."""
    if not target:
        return True
    clean_candidate = re.sub(r'[^a-z0-9]', '', str(candidate_title).lower())
    clean_target = re.sub(r'[^a-z0-9]', '', str(target).lower())
    return bool(clean_target and clean_target in clean_candidate)

def build_booking_url(venue_name, venue_code, date_code):
    """
    Constructs the exact BMS venue date URL from Venues.VenueName:
    e.g. 'Prasads Multiplex: Hyderabad' -> 'prasads-multiplex-hyderabad'
    """
    venue_slug = re.sub(r'[^a-z0-9]+', '-', str(venue_name).lower()).strip('-')
    if not venue_slug:
        venue_slug = venue_code.lower()
    return f"https://in.bookmyshow.com/cinemas/{CITY}/{venue_slug}/buytickets/{venue_code}/{date_code}"

def get_current_ist_str():
    """Returns current IST timestamp in YYYYMMDDHHMM format to match BMS CutOffDateTime."""
    ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    return ist_now.strftime("%Y%m%d%H%M")

# --- FONTS & BMS UI RENDERING ENGINE ---
SCALE = 4
FONT_TITLE = None
FONT_SUBTITLE = None
FONT_SHOWTIME = None
FONT_ATTR = None
FONT_BADGE = None

def init_fonts():
    """Loads pre-cached Roboto fonts into RAM once before threads start."""
    global FONT_TITLE, FONT_SUBTITLE, FONT_SHOWTIME, FONT_ATTR, FONT_BADGE
    roboto_reg_path = "Roboto-Regular.ttf"
    roboto_med_path = "Roboto-Medium.ttf"

    if not os.path.exists(roboto_reg_path):
        urllib.request.urlretrieve(
            "https://raw.githubusercontent.com/googlefonts/roboto/main/src/hinted/Roboto-Regular.ttf",
            roboto_reg_path
        )
    if not os.path.exists(roboto_med_path):
        urllib.request.urlretrieve(
            "https://raw.githubusercontent.com/googlefonts/roboto/main/src/hinted/Roboto-Medium.ttf",
            roboto_med_path
        )

    try:
        FONT_TITLE = ImageFont.truetype(roboto_reg_path, 14 * SCALE)
        FONT_SUBTITLE = ImageFont.truetype(roboto_reg_path, 12 * SCALE)
        FONT_SHOWTIME = ImageFont.truetype(roboto_reg_path, 12 * SCALE)
        FONT_ATTR = ImageFont.truetype(roboto_reg_path, 8 * SCALE)
        FONT_BADGE = ImageFont.truetype(roboto_med_path, 6 * SCALE)
    except Exception as e:
        print(f"    -> ⚠️ Font load error: {e}. Falling back to default.")
        default_f = ImageFont.load_default()
        FONT_TITLE = FONT_SUBTITLE = FONT_SHOWTIME = FONT_ATTR = FONT_BADGE = default_f

def measure_spaced_text(text, font, letter_spacing_px):
    """Calculates exact width of text with CSS letter-spacing applied."""
    if not text:
        return 0.0
    total = 0.0
    for i, ch in enumerate(text):
        adv = font.getlength(text[:i + 1]) - font.getlength(text[:i])
        total += adv
        if i < len(text) - 1:
            total += letter_spacing_px
    return total

def draw_spaced_text(draw, x, y_top, line_height, text, font, fill, letter_spacing_px):
    """
    Draws text using CSS line-height vertical centering and exact CSS letter-spacing.
    """
    if not text:
        return
    try:
        ascent, descent = font.getmetrics()
        baseline_y = y_top + (line_height - (ascent + descent)) / 2.0 + ascent
        anchor = "ls"
    except Exception:
        baseline_y = y_top + (line_height / 2.0)
        anchor = "lm"

    cur_x = float(x)
    for i, ch in enumerate(text):
        draw.text((cur_x, baseline_y), ch, font=font, fill=fill, anchor=anchor)
        adv = font.getlength(text[:i + 1]) - font.getlength(text[:i])
        cur_x += adv + letter_spacing_px

def truncate_with_ellipsis(text, font, max_width_px, letter_spacing_px):
    """Replicates CSS white-space: nowrap; overflow: hidden; text-overflow: ellipsis;"""
    if measure_spaced_text(text, font, letter_spacing_px) <= max_width_px:
        return text
    ellipsis = "..."
    for end_idx in range(len(text), 0, -1):
        candidate = text[:end_idx] + ellipsis
        if measure_spaced_text(candidate, font, letter_spacing_px) <= max_width_px:
            return candidate
    return ellipsis

def draw_subtitle_badge(draw, bx, by, acronym, scale):
    """
    Draws the authentic BMS green [ENG] speech-bubble icon next to the showtime.
    """
    badge_green = (31, 173, 62)
    bw = 17 * scale
    bh = 9 * scale
    border_w = max(1, int(0.8 * scale))

    # Main rectangular outline
    draw.rectangle([bx, by, bx + bw, by + bh], outline=badge_green, width=border_w, fill=(255, 255, 255))

    # Speech bubble tail at bottom-left
    tail_x = bx + (2 * scale)
    tail_h = int(2.2 * scale)
    tail_w = int(2.8 * scale)
    # Erase bottom border segment inside the tail opening
    draw.line([(tail_x + border_w, by + bh), (tail_x + tail_w - border_w, by + bh)], fill=(255, 255, 255), width=border_w + 1)
    # Draw tail edges
    draw.line([(tail_x, by + bh), (tail_x, by + bh + tail_h)], fill=badge_green, width=border_w)
    draw.line([(tail_x, by + bh + tail_h), (tail_x + tail_w, by + bh)], fill=badge_green, width=border_w)

    # Acronym text ('ENG') centered inside badge
    draw.text(
        (bx + bw / 2.0, by + bh / 2.0),
        acronym.upper(),
        font=FONT_BADGE,
        fill=badge_green,
        anchor="mm"
    )

def render_bms_section(child_event, visible_shows):
    """
    Renders a pixel-accurate BookMyShow ChildEvent section (Title, Subtitle, and Showtime Grid).
    """
    if not visible_shows:
        return None

    S = SCALE
    LETTER_SPACING = 0.2 * S

    # Exact BMS CSS Colors
    COLOR_BG = (255, 255, 255)
    COLOR_TITLE = (51, 51, 51)         # rgb(51, 51, 51)
    COLOR_SUBTITLE = (153, 153, 153)  # rgb(153, 153, 153)
    COLOR_BORDER = (204, 204, 204)    # rgb(204, 204, 204)
    COLOR_GREEN = (31, 173, 62)       # rgb(31, 173, 62)
    COLOR_YELLOW = (232, 169, 0)      # rgb(232, 169, 0)

    # Compose Header & Subheader strings
    event_name = child_event.get("eventName", "").strip()
    censor = child_event.get("eventCensor", "").strip()
    title_str = f"{event_name} ({censor})" if censor else event_name

    lang = child_event.get("eventLanguage", "").strip()
    dim = child_event.get("eventDimension", "").strip()
    subtitle_str = ", ".join([p for p in (lang, dim) if p])

    # Layout Metrics (CSS pixels * SCALE)
    PAD_X = 16 * S
    PAD_TOP = 14 * S
    PAD_BOTTOM = 14 * S

    TITLE_LH = 20 * S
    TITLE_MB = 3 * S

    SUBTITLE_LH = 16 * S
    SUBTITLE_MB = 12 * S

    BOX_W = 92 * S
    BOX_H = 38 * S
    BOX_RADIUS = 2 * S
    BOX_BORDER = 1 * S
    BOX_PAD = 4 * S
    BOX_GAP_X = 8 * S
    BOX_GAP_Y = 8 * S
    MAX_COLS = 4

    SHOWTIME_LH = 16 * S
    ATTR_LH = 12 * S
    # CSS: width: 90% inside the 4px-padded 92px box -> 0.90 * 84px = 75.6px
    ATTR_MAX_W = 0.90 * (BOX_W - (2 * BOX_PAD))

    num_shows = len(visible_shows)
    num_rows = (num_shows + MAX_COLS - 1) // MAX_COLS
    used_cols = min(num_shows, MAX_COLS)

    grid_w = (used_cols * BOX_W) + (max(0, used_cols - 1) * BOX_GAP_X)
    title_w = int(measure_spaced_text(title_str, FONT_TITLE, LETTER_SPACING))
    subtitle_w = int(measure_spaced_text(subtitle_str, FONT_SUBTITLE, LETTER_SPACING))

    content_w = max(grid_w, title_w, subtitle_w)
    canvas_w = content_w + (2 * PAD_X)

    grid_h = (num_rows * BOX_H) + (max(0, num_rows - 1) * BOX_GAP_Y)
    canvas_h = PAD_TOP + TITLE_LH + TITLE_MB + SUBTITLE_LH + SUBTITLE_MB + grid_h + PAD_BOTTOM

    img = Image.new("RGB", (canvas_w, canvas_h), COLOR_BG)
    draw = ImageDraw.Draw(img)

    # 1. Draw Title
    cur_y = PAD_TOP
    draw_spaced_text(draw, PAD_X, cur_y, TITLE_LH, title_str, FONT_TITLE, COLOR_TITLE, LETTER_SPACING)

    # 2. Draw Subtitle
    cur_y += TITLE_LH + TITLE_MB
    draw_spaced_text(draw, PAD_X, cur_y, SUBTITLE_LH, subtitle_str, FONT_SUBTITLE, COLOR_SUBTITLE, LETTER_SPACING)

    # 3. Draw Showtime Boxes
    grid_start_y = cur_y + SUBTITLE_LH + SUBTITLE_MB

    for idx, show in enumerate(visible_shows):
        row_idx = idx // MAX_COLS
        col_idx = idx % MAX_COLS

        bx = PAD_X + col_idx * (BOX_W + BOX_GAP_X)
        by = grid_start_y + row_idx * (BOX_H + BOX_GAP_Y)

        avail_status = str(show.get("availStatus", "3")).strip()
        text_color = COLOR_GREEN if avail_status == "3" else COLOR_YELLOW

        # Draw 92x38 rounded rectangle border
        draw.rounded_rectangle(
            [bx, by, bx + BOX_W - 1, by + BOX_H - 1],
            radius=BOX_RADIUS,
            outline=COLOR_BORDER,
            width=BOX_BORDER,
            fill=COLOR_BG
        )

        time_str = show.get("time", "").strip()
        attr_str = show.get("attributes", "").strip()
        sub_acronym = show.get("subAcronym", "").strip()

        time_w = measure_spaced_text(time_str, FONT_SHOWTIME, LETTER_SPACING)
        badge_w = (17 * S) if sub_acronym else 0
        badge_gap = (4 * S) if sub_acronym else 0
        top_row_total_w = time_w + badge_gap + badge_w

        # Flex column vertical centering inside 38px box
        if attr_str:
            total_inner_h = SHOWTIME_LH + ATTR_LH  # 16px + 12px = 28px
            time_y = by + (BOX_H - total_inner_h) / 2.0
            attr_y = time_y + SHOWTIME_LH
        else:
            time_y = by + (BOX_H - SHOWTIME_LH) / 2.0
            attr_y = None

        time_x = bx + (BOX_W - top_row_total_w) / 2.0
        draw_spaced_text(draw, time_x, time_y, SHOWTIME_LH, time_str, FONT_SHOWTIME, text_color, LETTER_SPACING)

        if sub_acronym:
            badge_x = time_x + time_w + badge_gap
            badge_y = time_y + (SHOWTIME_LH - (9 * S)) / 2.0 - (0.5 * S)
            draw_subtitle_badge(draw, badge_x, badge_y, sub_acronym, S)

        if attr_str and attr_y is not None:
            clipped_attr = truncate_with_ellipsis(attr_str, FONT_ATTR, ATTR_MAX_W, LETTER_SPACING)
            clipped_w = measure_spaced_text(clipped_attr, FONT_ATTR, LETTER_SPACING)
            attr_x = bx + (BOX_W - clipped_w) / 2.0
            draw_spaced_text(draw, attr_x, attr_y, ATTR_LH, clipped_attr, FONT_ATTR, text_color, LETTER_SPACING)

    # Downsample 4x -> 2x Retina using high-quality Lanczos filter
    img = img.resize((canvas_w // 2, canvas_h // 2), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="WEBP", lossless=True, quality=100)
    return buf.getvalue()

def humanize_date(date_str):
    dt = datetime.strptime(date_str, "%Y%m%d")
    day = dt.day
    if 11 <= (day % 100) <= 13:
        suffix = 'th'
    else:
        suffix = ['th', 'st', 'nd', 'rd', 'th'][min(day % 10, 4)]
    month_name = dt.strftime("%B")
    return f"{day}{suffix} {month_name}"

def clean_venue_name(venue_name):
    """Strips the ': City' suffix from BMS VenueName while preserving native casing."""
    return str(venue_name).split(":")[0].strip()

def clean_movie_title(child_event):
    """Returns the clean parent EventTitle without trailing language suffixes."""
    title = (child_event.get("parentTitle") or child_event.get("eventName") or "").strip()
    lang = child_event.get("eventLanguage", "").strip()
    if lang:
        title = re.sub(rf'\s*-\s*{re.escape(lang)}$', '', title, flags=re.IGNORECASE).strip()
        title = re.sub(rf'\s*\(\s*{re.escape(lang)}\s*\)$', '', title, flags=re.IGNORECASE).strip()
    return title

# --- GIT & STATE MANAGEMENT ---
def quiet_git_pull():
    subprocess.run(["git", "fetch", "origin", "main"], capture_output=True, check=False)
    subprocess.run(["git", "reset", "--hard", "origin/main"], capture_output=True, check=False)

def quiet_git_push():
    res = subprocess.run(["git", "push", "origin", "main"], capture_output=True, text=True, check=False)
    return res.returncode == 0

def read_local_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            print(f"[STATE] ⚠️ JSON Error reading state: {e}")
            return {}
    return {}

def load_initial_state():
    global GLOBAL_STATE
    with state_lock:
        quiet_git_pull()
        GLOBAL_STATE = read_local_state()
        return copy.deepcopy(GLOBAL_STATE)

def get_date_state(date_code):
    """Returns (is_initialized, dict_of_known_sessions) for a date_code."""
    with state_lock:
        if date_code not in GLOBAL_STATE:
            return False, {}
        return True, copy.deepcopy(GLOBAL_STATE[date_code])

def update_date_state(date_code, sessions_dict):
    with state_lock:
        GLOBAL_STATE[date_code] = copy.deepcopy(sessions_dict)
        UNPUSHED_DELTAS[date_code] = copy.deepcopy(sessions_dict)
        with open(STATE_FILE, "w") as f:
            json.dump(GLOBAL_STATE, f, indent=2)

def push_state_to_git(commit_msg="Update showtimes state"):
    global GLOBAL_STATE
    with state_lock:
        if not UNPUSHED_DELTAS:
            print("[GIT] No unpushed state changes in memory. Skipping Git push.")
            return

        for attempt in range(3):
            quiet_git_pull()
            latest_state = read_local_state()
            latest_state.update(GLOBAL_STATE)
            latest_state.update(UNPUSHED_DELTAS)
            GLOBAL_STATE = copy.deepcopy(latest_state)

            with open(STATE_FILE, "w") as f:
                json.dump(GLOBAL_STATE, f, indent=2)

            subprocess.run(["git", "add", STATE_FILE], capture_output=True, check=False)
            status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)

            if STATE_FILE in status.stdout:
                print(f"[GIT] Committing changes to {STATE_FILE} (Attempt {attempt+1})...")
                subprocess.run(["git", "commit", "-m", commit_msg], capture_output=True, check=False)
                if quiet_git_push():
                    print("[GIT] Successfully pushed merged state to repository.")
                    UNPUSHED_DELTAS.clear()
                    return
                else:
                    print(f"[GIT] Push attempt {attempt+1} failed. Retrying merge...")
                    time.sleep(2)
            else:
                print("[GIT] Merged state is identical to remote. Clearing dirty queue.")
                UNPUSHED_DELTAS.clear()
                return

        print("[GIT] ❌ Failed to push after 3 attempts. Changes remain queued in UNPUSHED_DELTAS.")

# --- ALERTING & NETWORK ---
def trigger_ntfy(message, image_bytes=None, click_url=None):
    print(f"\n[!] ALERTING VIA NTFY:\n{message}")
    headers = {"Priority": "urgent"}
    if click_url:
        headers["Click"] = click_url

    max_retries = 5
    for attempt in range(max_retries):
        try:
            if image_bytes:
                headers["Filename"] = "showtimes.webp"
                resp = requests.post(
                    "https://ntfy.sh/dolby_unblock",
                    params={"message": message},
                    data=image_bytes,
                    headers=headers,
                    timeout=15
                )
            else:
                resp = requests.post(
                    "https://ntfy.sh/dolby_unblock",
                    data=message.encode('utf-8'),
                    headers=headers,
                    timeout=15
                )

            if resp.status_code == 200:
                print(f"    -> 🟢 Ntfy ping sent successfully on attempt {attempt + 1}!")
                break
            else:
                print(f"    -> ⚠️ Ntfy attempt {attempt + 1} returned status {resp.status_code}. Retrying...")
        except Exception as e:
            print(f"    -> ❌ Ntfy attempt {attempt + 1} failed: {e}")

        if attempt < max_retries - 1:
            sleep_time = 2 ** attempt
            time.sleep(sleep_time)

def wait_for_warp_connected(timeout_sec=10):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        res = subprocess.run(["warp-cli", "--accept-tos", "status"], capture_output=True, text=True, check=False)
        if "Connected" in res.stdout:
            return True
        time.sleep(0.2)
    return False

def toggle_warp(expected_warp_state):
    global USE_WARP
    with warp_lock:
        if USE_WARP != expected_warp_state:
            print("    -> 🚨 [IP ROTATION] WARP already toggled by another thread. Skipping.")
            return

        if USE_WARP:
            print("    -> 🚨 [IP ROTATION] WARP is ON. Disconnecting (Switching to Runner IP)...")
            subprocess.run(["warp-cli", "--accept-tos", "disconnect"], capture_output=True, check=False)
            USE_WARP = False
        else:
            print("    -> 🚨 [IP ROTATION] WARP is OFF. Switching to Cloudflare WARP Proxy...")
            if not wait_for_warp_connected(timeout_sec=0.4):
                subprocess.run(["warp-cli", "--accept-tos", "connect"], capture_output=True, check=False)
                wait_for_warp_connected(timeout_sec=8)
            USE_WARP = True

def make_bms_request(method, url, max_retries=9, **kwargs):
    endpoint_path = url.split("?")[0].split("/")[-1]
    is_post = method.upper() == 'POST'

    for attempt in range(1, max_retries + 1):
        with warp_lock:
            current_proxies = PROXIES if USE_WARP else None
            current_warp_state = USE_WARP

        try:
            if is_post:
                resp = cffi_requests.post(url, headers=BMS_HEADERS, proxies=current_proxies, impersonate="chrome", timeout=15, **kwargs)
            else:
                resp = cffi_requests.get(url, headers=BMS_HEADERS, proxies=current_proxies, impersonate="chrome", timeout=15, **kwargs)

            print(f"[{method.upper()}] /{endpoint_path} -> HTTP {resp.status_code} (WARP: {USE_WARP})")

            if resp.status_code in [429, 403]:
                print(f"    -> ⚠️ Rate limited ({resp.status_code}) on attempt {attempt}/{max_retries}.")
                if attempt < max_retries:
                    toggle_warp(current_warp_state)
                    time.sleep(2)
                    continue
                else:
                    print("    -> ❌ Max retries reached for this request.")

            return resp

        except Exception as e:
            print(f"[{method.upper()}] /{endpoint_path} -> Network error on attempt {attempt}: {e}")
            if attempt < max_retries:
                time.sleep(3)
                continue

    return None

def fetch_date_events(date_code):
    """
    Fetches getShowtimesByVenue for a single date_code and extracts matching ChildEvents.
    Returns (success_bool, date_matched_bool, venue_name, booking_url, list_of_child_events).
    """
    url = f"https://cinemas.bookmyshow.com/api/getShowtimesByVenue?venueCode={VENUE_CODE}&dateCode={date_code}"
    resp = make_bms_request('GET', url)
    if not resp or resp.status_code != 200:
        return False, False, "", "", []

    try:
        data = resp.json()
        show_details = data.get("ShowDetails", [])
        if not show_details:
            return True, False, VENUE_CODE, build_booking_url(VENUE_CODE, VENUE_CODE, date_code), []

        detail = show_details[0]
        returned_date = str(detail.get("Date", "")).strip()
        if returned_date != str(date_code).strip():
            print(f"[{date_code}]    -> ⏭️ Date mismatch: requested '{date_code}', but API returned '{returned_date}' (0 shows allotted yet).")
            return True, False, "", "", []

        venue_info = detail.get("Venues", {})
        venue_name = venue_info.get("VenueName", VENUE_CODE)
        booking_url = build_booking_url(venue_name, VENUE_CODE, date_code)

        matched_child_events = []

        for event in detail.get("Event", []):
            event_title = event.get("EventTitle", "")
            child_events = event.get("ChildEvents", [])

            for child in child_events:
                child_name = child.get("EventName", "")
                if not (matches_movie(event_title, TARGET_MOVIE) or matches_movie(child_name, TARGET_MOVIE)):
                    continue

                shows = []
                for st in child.get("ShowTimes", []):
                    s_id = str(st.get("SessionId", "")).strip()
                    if not s_id:
                        continue
                    shows.append({
                        "sessionId": s_id,
                        "time": st.get("ShowTime", ""),
                        "showDateTime": str(st.get("ShowDateTime", "")),
                        "cutOffDateTime": str(st.get("CutOffDateTime", "")),
                        "availStatus": str(st.get("AvailStatus", "3")),
                        "attributes": st.get("Attributes", ""),
                        "subAcronym": st.get("SessionSubTitleAcronym", ""),
                        "screenName": st.get("ScreenName", ""),
                        "dateCode": st.get("ShowDateCode", date_code)
                    })

                matched_child_events.append({
                    "eventCode": child.get("EventCode", ""),
                    "parentTitle": event_title,
                    "eventName": child_name or event_title,
                    "eventCensor": child.get("EventCensor", ""),
                    "eventLanguage": child.get("EventLanguage", ""),
                    "eventDimension": child.get("EventDimension", ""),
                    "shows": shows
                })

        return True, True, venue_name, booking_url, matched_child_events

    except Exception as e:
        print(f"[{date_code}]    -> ⚠️ JSON parse error: {e}")
        return False, False, "", "", []

def poll_date_showtimes(date_code, start_time):
    cycle_count = 1

    while (time.time() - start_time) < MAX_RUNTIME_SECONDS:
        print(f"\n[{date_code}] ==================================================")
        print(f"[{date_code}] 🔄 STARTING SHOWTIME POLLING CYCLE {cycle_count}")
        print(f"[{date_code}] ==================================================")

        ok, date_matched, venue_name, booking_url, child_events = fetch_date_events(date_code)
        if not ok:
            print(f"[{date_code}]    -> ⚠️ Failed fetching showtimes. Sleeping {POLL_INTERVAL_SECONDS}s...")
            time.sleep(POLL_INTERVAL_SECONDS)
            cycle_count += 1
            continue

        is_initialized, known_sessions = get_date_state(date_code)

        if not date_matched:
            if not is_initialized:
                update_date_state(date_code, {})
                print(f"[{date_code}] [STATE] Unallotted future date — empty baseline (0 sessions) established. Syncing to Git...")
                push_state_to_git(f"Initialize empty showtime baseline for {date_code}")
            else:
                print(f"[{date_code}]    -> ⚪ Date not allotted yet on BMS. Ignoring fallback response. Sleeping {POLL_INTERVAL_SECONDS}s...")
            time.sleep(POLL_INTERVAL_SECONDS)
            cycle_count += 1
            continue

        updated_sessions = copy.deepcopy(known_sessions)
        any_new_shows = False
        now_ist = get_current_ist_str()

        for child in child_events:
            new_shows_in_child = []
            for show in child["shows"]:
                s_id = show["sessionId"]
                if s_id not in known_sessions:
                    new_shows_in_child.append(show)
                updated_sessions[s_id] = {
                    "time": show["time"],
                    "eventName": child["eventName"],
                    "screenName": show["screenName"],
                    "attributes": show["attributes"]
                }

            if new_shows_in_child:
                any_new_shows = True
                if is_initialized:
                    # Filter out expired showtimes (past CutOffDateTime in IST) so UI matches live BMS
                    visible_shows = [
                        s for s in child["shows"]
                        if (not s["cutOffDateTime"] or s["cutOffDateTime"] > now_ist)
                        or s["sessionId"] in {ns["sessionId"] for ns in new_shows_in_child}
                    ]
                    visible_shows.sort(key=lambda s: s["showDateTime"])

                    # Render authentic BMS ChildEvent UI once for this ChildEvent
                    img_bytes = None
                    b64_string = None
                    try:
                        img_bytes = render_bms_section(child, visible_shows)
                        if img_bytes:
                            b64_string = base64.b64encode(img_bytes).decode('utf-8')
                    except Exception as e:
                        print(f"[{date_code}]    -> ⚠️ Failed to render BMS UI image: {e}")

                    movie_title = clean_movie_title(child)
                    clean_venue = clean_venue_name(venue_name)
                    human_date = humanize_date(date_code)

                    # Group new shows by attribute (if present) or by screenName (if attribute is blank)
                    screen_groups = {}
                    for ns in sorted(new_shows_in_child, key=lambda x: x["showDateTime"]):
                        raw_attr = (ns.get("attributes") or "").strip()
                        raw_screen = (ns.get("screenName") or "").strip()
                        screen_label = (raw_attr if raw_attr else (raw_screen or "SCREEN")).upper()
                        screen_groups.setdefault(screen_label, []).append(ns)

                    # Send a separate notification and stage a separate tweet per screen group
                    for screen_label, group_shows in screen_groups.items():
                        show_lines = "\n".join(f"{human_date}, {ns['time']}" for ns in group_shows)
                        msg = (
                            f"New Showtimes added for {movie_title} in {screen_label} at {clean_venue}\n\n"
                            f"{show_lines}"
                        )
                        tweet_payload = f"{msg}\n\nBook Here: {booking_url}"

                        times_log = ", ".join(ns["time"] for ns in group_shows)
                        print(f"[{date_code}]    -> 🟢 NEW SHOWS DETECTED for '{movie_title}' [{screen_label}]: {times_log}")

                        try:
                            trigger_ntfy(msg, image_bytes=img_bytes, click_url=booking_url)
                        except Exception as e:
                            print(f"[{date_code}]    -> ⚠️ Failed sending ntfy alert for [{screen_label}]: {e}")

                        try:
                            stage_tweet_supabase(tweet_payload, b64_string)
                            print(f"[{date_code}]    -> 🚀 Tweet successfully staged in Supabase for [{screen_label}]!")
                        except Exception as e:
                            print(f"[{date_code}]    -> ⚠️ Failed to stage tweet in Supabase for [{screen_label}]: {e}")

        if not is_initialized:
            update_date_state(date_code, updated_sessions)
            print(f"[{date_code}] [STATE] First-run baseline established ({len(updated_sessions)} existing session(s)). Syncing to Git...")
            push_state_to_git(f"Initialize showtime baseline for {date_code}")
        elif any_new_shows:
            update_date_state(date_code, updated_sessions)
            print(f"[{date_code}] [STATE] New showtimes recorded. Syncing to Git...")
            push_state_to_git(f"New showtimes added on {date_code}")
        else:
            print(f"[{date_code}]    -> ⚪ No new showtimes detected (Tracking {len(updated_sessions)} session(s)).")

        cycle_count += 1
        time.sleep(POLL_INTERVAL_SECONDS)

def main():
    start_time = time.time()
    init_fonts()
    print("==================================================")
    print("🚀 STARTING BMS NEW SHOWTIME POLLER (MULTI-THREADED)")
    print(f"📍 VENUE: {VENUE_CODE} | 🎬 MOVIE: {TARGET_MOVIE or 'ALL'} | 📅 DATES: {', '.join(DATES)}")
    print("==================================================")

    if not DATES or not VENUE_CODE:
        print("❌ DATES and VENUE_CODE environment variables must be set. Exiting.")
        return

    print("\n[GIT] Loading initial state from repository...")
    state = load_initial_state()
    if not state:
        print("[STATE] Empty state found. Threads will establish baselines silently on cycle 1.")
    else:
        print(f"[STATE] Loaded existing state for {len(state)} date(s).")

    signal.signal(signal.SIGTERM, lambda signum, frame: (_ for _ in ()).throw(SystemExit(0)))

    print(f"\n🚀 Spawning {len(DATES)} thread(s) for continuous showtime monitoring...")
    threads = []
    for idx, date_code in enumerate(DATES):
        if idx > 0:
            time.sleep(1.5)  # Stagger initial requests across date threads
        t = threading.Thread(target=poll_date_showtimes, args=(date_code, start_time), daemon=True)
        threads.append(t)
        t.start()

    try:
        for t in threads:
            t.join()
    except (KeyboardInterrupt, SystemExit):
        print("\n⚠️ Shutdown signal received! Preparing final state sync...")
    finally:
        print("\n🔄 [SHUTDOWN SYNC] Flushing any unpushed state deltas from RAM to Git...")
        push_state_to_git("Final showtime state sync on runner shutdown")
        print("🏁 All threads finished. Gracefully shutting down.")

if __name__ == "__main__":
    main()
