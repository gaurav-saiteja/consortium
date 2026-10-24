import requests
from curl_cffi import requests as cffi_requests
import time
import json
import os
import re
import random
import io
from supabase import create_client, Client
import base64
import urllib.request
from PIL import Image, ImageDraw, ImageFont
import subprocess
from datetime import datetime
import threading
import builtins
from collections import defaultdict

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
DATES = ["20261028"]
VENUE_CODE = "PRHN"
TARGET_MOVIE = "jailer"     
TARGET_SCREEN = "4"
STATE_FILE = "thirddd_day_state.json"
MAX_RUNTIME_SECONDS = (5 * 3600) + (55 * 60)

SUPABASE_URL = "https://edqxafyqqkhxuipzcjcd.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVkcXhhZnlxcWtoeHVpcHpjamNkIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODcyOTYzNzIsImV4cCI6MjEwMjg3MjM3Mn0.dAerrc1sTh8CSnY6vZ4NtuvPXWNwjsHCl9gWZ436MLk"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

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
    clean_candidate = re.sub(r'[^a-z0-9]', '', str(candidate_title).lower())
    clean_target = re.sub(r'[^a-z0-9]', '', str(target).lower())
    return bool(clean_target and clean_target in clean_candidate)

def matches_screen(screen_name, target_screen):
    """Extracts numeric digits to accurately match screen names (e.g. 'SCREEN 6' -> '6')."""
    screen_digits = re.findall(r'\d+', str(screen_name))
    target_digits = re.findall(r'\d+', str(target_screen))
    return bool(screen_digits and target_digits and screen_digits[0] == target_digits[0])

def parse_grid_data(str_data):
    """Parses the raw layout string into a 2D grid preserving exact spatial coordinates."""
    grid = []
    if not str_data: return grid
    
    parts = str_data.split("||")
    rows_data = parts[1] if len(parts) > 1 else parts[0]
    rows = rows_data.split("|")
    
    for row in rows:
        if not row or ":" not in row: continue
        elements = row.split(":")
        if len(elements) < 3: continue
        
        row_letter = elements[1]
        is_valid_row = row_letter.isalpha()
        
        row_data = {"letter": row_letter if is_valid_row else "", "seats": []}
        x_index = 0
        
        for item in elements[2:]:
            if item == "A000": 
                continue # Ignore start marker
            if item == "A0+0":
                x_index += 1 # Empty space (aisle gap)
            else:
                # Capture availability (1,4) or booked (2), and seat label
                match = re.search(r"[A-Z]([124])\d*\+(\d+)", item)
                if match:
                    status, label = match.groups()
                    row_data["seats"].append({
                        "x": x_index,
                        "label": label,
                        "available": status in ['1', '4']
                    })
                    x_index += 1
        grid.append(row_data)
    return grid

def render_seat_image(grid, new_seats_dict):
    """
    Renders a pixel-perfect, 4X supersampled WebP image of the seat layout.
    Focuses dynamically on the upper or lower half based on unblocked seat coordinates.
    """
    target_y_indices = []
    
    # 1. Locate the Y-coordinates of the newly unblocked seats
    for y, row in enumerate(grid):
        if row["letter"] in new_seats_dict:
            new_labels = new_seats_dict[row["letter"]]
            for seat in row["seats"]:
                if seat["label"] in new_labels:
                    target_y_indices.append(y)
                    
    if not target_y_indices: return None
        
    total_rows = len(grid)
    mid_row = total_rows / 2.0
    avg_y = sum(target_y_indices) / len(target_y_indices)
    
    # 2. Dynamic Half-Screen Cropping
    if avg_y < mid_row:
        min_y = 0
        max_y = min(total_rows - 1, int(mid_row + 2)) # Upper half + 1 row overlap
    else:
        min_y = max(0, int(mid_row - 3)) # Lower half + 1 row overlap
        max_y = total_rows - 1

    # Find the maximum X coordinate to determine image width
    max_grid_x = 0
    for row in grid:
        for seat in row["seats"]:
            max_grid_x = max(max_grid_x, seat["x"])
            
    # 3. Supersampling Engine (4X Scale) for flawless Anti-Aliasing
    SCALE = 4
    
    # UI Layout Metrics (Mimicking BMS CSS)
    CELL_SIZE = 26 * SCALE
    GAP = 7 * SCALE
    STEP = CELL_SIZE + GAP
    BORDER_RADIUS = 4 * SCALE
    BORDER_WIDTH = int(1.5 * SCALE)
    
    # Exact BookMyShow Color Palette
    COLOR_AVAILABLE_OUTLINE = "#1FAD3E" # Vibrant BMS Emerald
    COLOR_AVAILABLE_TEXT = "#454545"    # Dark Grey
    COLOR_BOOKED_FILL = "#E5E5E5"    
    COLOR_BOOKED_TEXT = "#FFFFFF"       # White
    COLOR_ROW_TEXT = "#2A2A2A"          # Black
    
    width = (max_grid_x + 2) * STEP + (40 * SCALE)
    height = (max_y - min_y + 1) * STEP + (40 * SCALE)
    
    img = Image.new("RGB", (width, height), "#FFFFFF")
    draw = ImageDraw.Draw(img)
    
    # 4. Enforce Roboto Font (Auto-download if missing to guarantee OS compatibility)
    roboto_reg_path = "Roboto-Regular.ttf"
    roboto_bold_path = "Roboto-Medium.ttf"
    
    # Download directly from Google's official font repository if not cached
    if not os.path.exists(roboto_reg_path):
        urllib.request.urlretrieve("https://raw.githubusercontent.com/googlefonts/roboto/main/src/hinted/Roboto-Regular.ttf", roboto_reg_path)
    if not os.path.exists(roboto_bold_path):
        urllib.request.urlretrieve("https://raw.githubusercontent.com/googlefonts/roboto/main/src/hinted/Roboto-Medium.ttf", roboto_bold_path)
        
    try:
        # Load the newly cached Roboto fonts
        font = ImageFont.truetype(roboto_reg_path, 11 * SCALE)
        row_font = ImageFont.truetype(roboto_bold_path, 13 * SCALE) # Using Medium weight for row letters to match BMS
    except Exception as e:
        print(f"    -> ⚠️ Font load error: {e}. Falling back to default.")
        font = ImageFont.load_default()
        row_font = font
        
    for y in range(min_y, max_y + 1):
        row = grid[y]
        row_letter = row["letter"]
        draw_y = (y - min_y) * STEP + (20 * SCALE)
        
        if row_letter:
            draw.text(
                (20 * SCALE, draw_y + (CELL_SIZE // 2)), 
                row_letter, 
                fill=COLOR_ROW_TEXT, 
                font=row_font, 
                anchor="lm"
            )
        
        for seat in row["seats"]:
            sx = seat["x"]
            # +1 shift to the right to leave space for the Row Alphabets
            draw_x = (sx + 1) * STEP + (20 * SCALE) 
            
            rect = [draw_x, draw_y, draw_x + CELL_SIZE, draw_y + CELL_SIZE]
            
            center_pt = (draw_x + (CELL_SIZE // 2), draw_y + (CELL_SIZE // 2)) 
            
            if seat["available"]:
                draw.rounded_rectangle(rect, radius=BORDER_RADIUS, outline=COLOR_AVAILABLE_OUTLINE, width=BORDER_WIDTH, fill="#FFFFFF")
                draw.text(center_pt, seat["label"], fill=COLOR_AVAILABLE_TEXT, font=font, anchor="mm")
            else:
                draw.rounded_rectangle(rect, radius=BORDER_RADIUS, outline=COLOR_BOOKED_FILL, width=BORDER_WIDTH, fill=COLOR_BOOKED_FILL)
                draw.text(center_pt, seat["label"], fill=COLOR_BOOKED_TEXT, font=font, anchor="mm")
                
    # 6. Apply Anti-Aliasing (Downsample back to 1X scale using high-quality Lanczos filter)
    img = img.resize((width // 2, height // 2), Image.Resampling.LANCZOS)
    
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

def quiet_git_pull():
    """Fetches and hard resets to exactly match remote. Wipes any failed local commits to prevent JSON merge conflicts."""
    subprocess.run(["git", "fetch", "origin", "main"], capture_output=True, check=False)
    subprocess.run(["git", "reset", "--hard", "origin/main"], capture_output=True, check=False)

def quiet_git_push():
    res = subprocess.run(["git", "push", "origin", "main"], capture_output=True, text=True, check=False)
    return res.returncode == 0

def read_local_state():
    """Reads the JSON from disk without touching Git."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            print(f"[STATE] ⚠️ JSON Error reading state: {e}")
            return {}
    return {}

def load_state():
    """Syncs with remote and loads the freshest state into memory (Thread-Safe)."""
    with state_lock:
        quiet_git_pull()
        return read_local_state()

def save_state(deltas, commit_msg="Update seat state"):
    """Thread-Safe save and push to Git."""
    with state_lock:
        for attempt in range(3):
            # 1. Force sync local repo with remote
            quiet_git_pull()
            
            # 2. Read the newly synced remote state
            latest_state = read_local_state()
            
            # 3. Merge our locally tracked changes (deltas) into this state
            for s_id, s_data in deltas.items():
                latest_state[s_id] = s_data
                
            # 4. Save the merged state to disk
            with open(STATE_FILE, "w") as f:
                json.dump(latest_state, f, indent=2)
                
            # 5. Commit
            subprocess.run(["git", "add", STATE_FILE], capture_output=True, check=False)
            status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
            
            if STATE_FILE in status.stdout:
                print(f"[GIT] Committing changes to {STATE_FILE} (Attempt {attempt+1})...")
                subprocess.run(["git", "commit", "-m", commit_msg], capture_output=True, check=False)
                
                # 6. Push
                if quiet_git_push():
                    print(f"[GIT] Successfully pushed merged state to repository.")
                    return latest_state
                else:
                    print(f"[GIT] Push attempt {attempt+1} failed (likely concurrent push). Retrying merge...")
                    time.sleep(2)
            else:
                print("[GIT] Merged state is identical to remote. Nothing to push.")
                return latest_state
                
        print("[GIT] ❌ Failed to push after 3 attempts. Local memory updated with last known merge.")
        return latest_state

def trigger_ntfy(message, image_bytes=None, click_url=None):
    print(f"\n[!] ALERTING VIA NTFY: {message.replace(chr(10), ' | ')}")
    
    headers = {
        "Priority": "urgent",
    }
    
    if click_url:
        headers["Click"] = click_url
        
    max_retries = 5
    for attempt in range(max_retries):
        try:
            if image_bytes:
                headers["Message"] = message.replace('\n', ' | ').encode('utf-8')
                headers["Filename"] = "seats.webp"
                resp = requests.post(
                    "https://ntfy.sh/dolby_unblock",
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
            sleep_time = 2 ** attempt # Exponential backoff: 1s, 2s, 4s, 8s
            print(f"    -> Waiting {sleep_time}s before next ntfy attempt...")
            time.sleep(sleep_time)

def toggle_warp(expected_warp_state):
    """Toggles Cloudflare WARP on/off and updates proxy state (Thread-Safe)."""
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
            print("    -> 🚨 [IP ROTATION] WARP is OFF. Connecting (Switching to Cloudflare Proxy)...")
            subprocess.run(["warp-cli", "--accept-tos", "connect"], capture_output=True, check=False)
            time.sleep(3)  # Wait for the tunnel to establish
            USE_WARP = True

def poll_date_sessions(date_code, target_sessions, start_time, is_first_run):
    cycle_count = 1
    total_sessions = len(target_sessions)
    
    while (time.time() - start_time) < MAX_RUNTIME_SECONDS:
        print(f"\n[{date_code}] ==================================================")
        print(f"[{date_code}] 🔄 STARTING POLLING CYCLE {cycle_count}")
        print(f"[{date_code}] ==================================================")
        
        state = load_state()
        deltas = {}
        
        for index, session in enumerate(target_sessions, 1):
            s_id = session["sessionId"]
            s_date = session["dateCode"]
            s_time = session["time"]
            
            print(f"\n[{date_code} | {index}/{total_sessions}] Checking Session {s_id} (Time: {s_time})")
            print(f"[{date_code}]    -> Sleeping for 3 seconds...")
            time.sleep(3) 
            
            str_data = fetch_seat_layout(s_id)
            if not str_data:
                continue
                
            current_seats = parse_layout(str_data)
            current_total = sum(len(seats) for seats in current_seats.values())
            
            print(f"[{date_code}]    -> Session [{s_id}] ({s_time}) | Available Seats: {current_total}")
            
            if s_id not in state:
                state[s_id] = {"date": s_date, "time": s_time, "total": 0, "rows": {}}
            
            previous_total = state[s_id].get("total", 0)
            previous_rows = state[s_id].get("rows", {})
            
            newly_unblocked_count = 0
            unblocked_rows_list = []
            
            for row, seats in current_seats.items():
                old_seats_in_row = previous_rows.get(row, [])
                new_seats = set(seats) - set(old_seats_in_row)
                
                if new_seats:
                    newly_unblocked_count += len(new_seats)
                    unblocked_rows_list.append(row)
            
            if newly_unblocked_count > 0:
                print(f"[{date_code}]    -> 🟢 DETECTED UNBLOCKS: +{newly_unblocked_count} new seats!")
                
                if not is_first_run:
                    if newly_unblocked_count >= 3:
                        rows_str = ", ".join(sorted(unblocked_rows_list))
                        human_date = humanize_date(s_date)
                        movie_name = session.get("movieTitle", TARGET_MOVIE)
                        screen_label = session.get("screenName", f"Screen {TARGET_SCREEN}")
                        s_event_code = session.get("eventCode", "") 
                    
                        msg = (
                            f"[{newly_unblocked_count}] {movie_name} ({screen_label})\n"
                            f"Rows: {rows_str}\n\n"
                            f"{human_date}, {s_time}"
                        )
                        
                        seat_layout_url = f"https://in.bookmyshow.com/movies/hyderabad/seat-layout/{s_event_code}/{VENUE_CODE}/{s_id}/{s_date}"
                        tweet_payload = f"{msg}\n\nBook Here: {seat_layout_url}"
                        
                        print(f"[{date_code}]    -> Rendering and compressing UI layout map...")
                        try:
                            new_seats_dict = {}
                            for r in unblocked_rows_list:
                                old_seats_in_row = previous_rows.get(r, [])
                                cur_seats_in_row = current_seats.get(r, [])
                                new_seats_dict[r] = set(cur_seats_in_row) - set(old_seats_in_row)
                                
                            grid_data = parse_grid_data(str_data)
                            img_bytes = render_seat_image(grid_data, new_seats_dict)
                            
                            trigger_ntfy(msg, image_bytes=img_bytes, click_url=seat_layout_url) 
                            b64_string = base64.b64encode(img_bytes).decode('utf-8')
                            
                            try:
                                supabase.table("staged_tweets").insert({
                                    "tweet_text": tweet_payload,
                                    "image_base64": b64_string
                                }).execute()
                                print(f"[{date_code}]    -> 🚀 Tweet successfully staged in Supabase!")
                            except Exception as e:
                                print(f"[{date_code}]    -> ⚠️ Failed to stage tweet in Supabase: {e}")
                        except Exception as e:
                            print(f"[{date_code}]    -> ⚠️ Failed to render UI image: {e}")
                            trigger_ntfy(msg, click_url=seat_layout_url)
                    else:
                        print(f"[{date_code}]    -> 🟡 Less than 3 seats unblocked. Skipping notification.")
                
                state[s_id]["rows"] = current_seats
                state[s_id]["total"] = current_total
                deltas[s_id] = state[s_id]

            elif current_total < previous_total:
                print(f"[{date_code}]    -> 🔴 Seats booked. Total dropped from {previous_total} down to {current_total}.")
                state[s_id]["rows"] = current_seats
                state[s_id]["total"] = current_total
                deltas[s_id] = state[s_id]
                
            else:
                print(f"[{date_code}]    -> ⚪ No changes detected.")

        if deltas:
            print(f"\n[{date_code}] [STATE] Cycle finished. Changes detected, merging and saving...")
            state = save_state(deltas, f"State update at cycle {cycle_count} for date {date_code}")
        else:
            print(f"\n[{date_code}] [STATE] Cycle finished. No changes detected.")
            
        if is_first_run:
            is_first_run = False
            print(f"[{date_code}] [STATE] First run baseline has been successfully established.")
            
        cycle_count += 1

def make_bms_request(method, url, max_retries=9, **kwargs):
    endpoint_path = url.split("?")[0].split("/")[-1]
    is_post = method.upper() == 'POST'

    for attempt in range(1, max_retries + 1):
        # Safely read current proxy configuration
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
                    toggle_warp(current_warp_state) # Pass the state we had when the request failed
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

def fetch_sessions():
    sessions = []
    seen_session_ids = set()

    for date_code in DATES:
        time.sleep(3)
        url = f"https://cinemas.bookmyshow.com/api/getShowtimesByVenue?venueCode={VENUE_CODE}&dateCode={date_code}"
        
        resp = make_bms_request('GET', url)
        if not resp or resp.status_code != 200:
            print(f"    -> Failed fetching showtimes for {date_code}.")
            continue
            
        try:
            data = resp.json()
            show_details = data.get("ShowDetails", [])
            matched_count = 0

            for detail in show_details:
                for event in detail.get("Event", []):
                    event_title = event.get("EventTitle", "")
                    child_events = event.get("ChildEvents", [])
                    
                    title_match = matches_movie(event_title, TARGET_MOVIE) or any(
                        matches_movie(c.get("EventName", ""), TARGET_MOVIE) for c in child_events
                    )
                    if not title_match:
                        continue
                        
                    for child in child_events:
                        event_code = child.get("EventCode", "") # ADD THIS LINE
                        for show in child.get("ShowTimes", []):
                            session_id = show.get("SessionId")
                            screen_name = show.get("ScreenName", "")
                            
                            # Screen intersection check
                            if session_id and session_id not in seen_session_ids and matches_screen(screen_name, TARGET_SCREEN):
                                sessions.append({
                                    "sessionId": session_id,
                                    "dateCode": show.get("ShowDateCode", date_code),
                                    "time": show.get("ShowTime", ""),
                                    "movieTitle": event_title,
                                    "screenName": screen_name,
                                    "eventCode": event_code
                                })
                                seen_session_ids.add(session_id)
                                matched_count += 1
                                
            print(f"    -> Matched {matched_count} session(s) for '{TARGET_MOVIE}' on Screen {TARGET_SCREEN} ({date_code}).")
            
        except Exception as e:
            print(f"    -> JSON parse error for {date_code}: {e}")
            
    return sessions

def fetch_seat_layout(session_id):
    url = "https://cinemas.bookmyshow.com/api/doTrans"
    payload = {
        "venueCode": VENUE_CODE,
        "sessionId": str(session_id),
        "cmd": "GETSEATLAYOUT"
    }
    
    resp = make_bms_request('POST', url, json=payload)
    if not resp or resp.status_code != 200:
        return ""
        
    try:
        return resp.json().get("BookMyShow", {}).get("strData", "")
    except Exception as e:
        print(f"    -> Exception parsing layout JSON (Session {session_id}): {e}")
        return ""

def parse_layout(str_data):
    if not str_data: return {}
    
    parts = str_data.split("||")
    rows_data = parts[1] if len(parts) > 1 else parts[0]
    rows = rows_data.split("|")
    
    available_seats_by_row = {}
    
    for row in rows:
        if not row or ":" not in row: continue
        elements = row.split(":")
        row_letter = elements[1]
        seats = elements[2:]
        
        available_in_row = []
        for seat in seats:
            # --- THE ONLY CHANGED LINE ---
            # [A-Z] : Match Category (A, B, C, D)
            # [24]  : Match Status (2 = Available, 4 = Best Available)
            # \d+   : Match seat index/coordinate
            # \+    : Match literal '+' delimiter
            # (\d+) : Capture the physical seat number
            match = re.search(r"[A-Z][14]\d+\+(\d+)", seat)
            
            if match:
                available_in_row.append(match.group(1))
                
        if available_in_row:
            available_seats_by_row[row_letter] = available_in_row
            
    return available_seats_by_row

def main():
    start_time = time.time()
    
    print("==================================================")
    print("🚀 STARTING BMS SEAT SCRAPER (MULTI-THREADED)")
    print("==================================================")
    print("Fetching valid sessions...")
    target_sessions = fetch_sessions()
    
    total_sessions = len(target_sessions)
    print(f"\n✅ Found a total of {total_sessions} DOLBY CINEMA sessions to monitor.")
    print("==================================================")
    
    if total_sessions == 0:
        print("No valid sessions found. Exiting.")
        return

    print("\n[GIT] Loading initial state from repository...")
    state = load_state()
    is_first_run = len(state) == 0
    if is_first_run:
        print("[STATE] Empty state found. Initializing baselines silently...")
    else:
        print(f"[STATE] Loaded existing state for {len(state)} sessions.")

    # Group sessions by date
    sessions_by_date = defaultdict(list)
    for session in target_sessions:
        sessions_by_date[session["dateCode"]].append(session)
        
    print(f"\n🚀 Spawning {len(sessions_by_date)} thread(s) for monitoring...")
    
    threads = []
    for date_code, sessions in sessions_by_date.items():
        t = threading.Thread(
            target=poll_date_sessions, 
            args=(date_code, sessions, start_time, is_first_run)
        )
        threads.append(t)
        t.start()
        
    # Wait for all threads to finish (which takes MAX_RUNTIME_SECONDS)
    for t in threads:
        t.join()
        
    print("\n🏁 All threads finished. Time limit reached. Gracefully shutting down.")


if __name__ == "__main__":
    main()
