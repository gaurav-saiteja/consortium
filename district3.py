import requests
from curl_cffi import requests as cffi_requests
import time
import json
import os
import subprocess
from datetime import datetime, timedelta
import random
import threading
import base64
import queue
from supabase import create_client, Client

# --- CONFIGURATION ---
DATES = ["2026-09-14"]
THEATRE_ID = "1039848" #vimal 70mm
TARGET_MOVIE_TITLE = "irumudi"
SCREEN_LABEL = "64CH ATMOS"
BOOKING_QUEUE = queue.Queue()
# --- AUTOBUIER CONFIGURATION ---
TARGET_SEATS = ["Q17"] # Add the specific seats you want here!

DEVICE_ID = "e655d7074359cd97"
USER_ID = "94001055"
ACCESS_TOKEN = "1789212473111770172_711805081438907057_6d65d672fcf1c7b42f59237d40918f43fed620a9b53570ac642f9632198bb3b8"
REFRESH_TOKEN = "1789212473111774137_6651234817425529245_e28196895d819bdaf4707d1d3d3da4edfec986017e5281c6114f9e3bee3c3dac"

# The Cookie string for web endpoints (checkout & SDK token)
COOKIE_STRING = f"x-device-id={DEVICE_ID}; x-access-token={ACCESS_TOKEN}; x-refresh-token={REFRESH_TOKEN};"

# Thread-safe tracking to prevent double-buying
IN_PROGRESS_SEATS = set()

SUPABASE_URL = "https://edqxafyqqkhxuipzcjcd.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVkcXhhZnlxcWtoeHVpcHpjamNkIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODcyOTYzNzIsImV4cCI6MjEwMjg3MjM3Mn0.dAerrc1sTh8CSnY6vZ4NtuvPXWNwjsHCl9gWZ436MLk"

if not SUPABASE_URL or not SUPABASE_KEY:
    print("[FATAL] Supabase URL or Key not found in environment variables. Exiting.")
    exit()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

STATE_FILE = "district_seat_state.json"
MAX_RUNTIME_SECONDS = (5 * 3600) + (55 * 60) # 5 hours 55 mins

# Track WARP State natively
USE_WARP = True

# Cloudflare WARP local proxy
PROXIES = {
    "http": "socks5://127.0.0.1:40000",
    "https": "socks5://127.0.0.1:40000"
}

RAW_PROXY_LIST = os.environ.get("PROXY_LIST", "")
SNIPER_PROXIES = []

for line in RAW_PROXY_LIST.strip().split("\n"):
    line = line.strip()
    if line:
        parts = line.split(":")
        if len(parts) == 4:
            ip, port, user, pwd = parts
            uri = f"http://{user}:{pwd}@{ip}:{port}"
            SNIPER_PROXIES.append({"http": uri, "https": uri})

print(f"[CONFIG] Loaded {len(SNIPER_PROXIES)} dedicated proxies for Sniper Threads.")

# 1. Headers for district.in (Web Spoofing for GET)
GET_HEADERS = {
    "Host": "www.district.in",
    "Api_source": "district",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9"
}

# 2. Headers for api.edition.in (Android App Spoofing for POST)
POST_HEADERS = {
    "Host": "api.edition.in",
    "Content-Type": "application/json; charset=utf-8",
    "User-Agent": "com.application.zomato.district/1000026600 (Linux; U; Android 13; en_US; sdk_gphone64_x86_64; Build/TE1A.240213.009; Cronet/113.0.5672.51)",
    "Accept-Encoding": "gzip, deflate, br"
}

def humanize_datetime(iso_str):
    """Parses District's ISO 8601 strings (2026-09-11T13:25) into human readable text."""
    try:
        dt = datetime.fromisoformat(iso_str)
        dt = dt + timedelta(hours=5, minutes=30)
        day = dt.day
        if 11 <= (day % 100) <= 13:
            suffix = 'th'
        else:
            suffix = ['th', 'st', 'nd', 'rd', 'th'][min(day % 10, 4)]
            
        return dt.strftime(f"{day}{suffix} %B, %I:%M %p")
    except Exception:
        return iso_str

# --- GIT STATE MANAGEMENT ---
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

def load_state():
    quiet_git_pull()
    return read_local_state()

def save_state(deltas, commit_msg="Update district seat state"):
    for attempt in range(3):
        quiet_git_pull()
        latest_state = read_local_state()
        
        for s_id, s_data in deltas.items():
            latest_state[s_id] = s_data
            
        with open(STATE_FILE, "w") as f:
            json.dump(latest_state, f, indent=2)
            
        subprocess.run(["git", "add", STATE_FILE], capture_output=True, check=False)
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        
        if STATE_FILE in status.stdout:
            print(f"[GIT] Committing changes (Attempt {attempt+1})...")
            subprocess.run(["git", "commit", "-m", commit_msg], capture_output=True, check=False)
            
            if quiet_git_push():
                print(f"[GIT] Successfully pushed merged state.")
                return latest_state
            else:
                time.sleep(2)
        else:
            return latest_state
            
    print("[GIT] ❌ Failed to push after 3 attempts.")
    return latest_state

def trigger_ntfy(message):
    print(f"\n[!] ALERTING VIA NTFY:\n{message}")
    for i in range(1):
        try:
            requests.post(
                "https://ntfy.sh/dolby_unblock",
                data=message.encode('utf-8'),
                headers={"Priority": "urgent"},
                timeout=10
            )
        except Exception as e:
            print(f"    -> Ntfy ping {i+1} failed: {e}")

# --- NETWORK & PARSING ---
def toggle_warp():
    global USE_WARP
    if USE_WARP:
        print("    -> 🚨 Disconnecting WARP (Switching to Runner IP)...")
        subprocess.run(["warp-cli", "--accept-tos", "disconnect"], capture_output=True, check=False)
        USE_WARP = False
    else:
        print("    -> 🚨 Connecting to WARP (Switching to Cloudflare Proxy)...")
        subprocess.run(["warp-cli", "--accept-tos", "connect"], capture_output=True, check=False)
        time.sleep(3) 
        USE_WARP = True

def make_request(method, url, max_retries=3, proxy_override=None, **kwargs):
    for attempt in range(1, max_retries + 1):
        # If a thread provides its own proxy, use it! Otherwise, fallback to the main WARP logic.
        current_proxies = proxy_override if proxy_override else (PROXIES if USE_WARP else None)

        if 'headers' in kwargs:
            headers = kwargs['headers'].copy()
            if "api.edition.in" not in url:
                headers['X-Guest-Token'] = str(random.randint(1000000, 9999999))
            kwargs['headers'] = headers

        try:
            if method.upper() == 'GET':
                resp = cffi_requests.get(url, proxies=current_proxies, impersonate="chrome", timeout=15, **kwargs)
            else:
                resp = cffi_requests.post(url, proxies=current_proxies, impersonate="chrome", timeout=15, **kwargs)
            
            if resp.status_code in [429, 403]:
                print(f"    -> ⚠️ Rate limited ({resp.status_code}) on attempt {attempt}/{max_retries}.")
                if attempt < max_retries:
                    # ONLY toggle WARP if this is the main polling thread.
                    if not proxy_override:
                        toggle_warp()
                    else:
                        # Sniper threads just pause for a second and try their private proxy again
                        time.sleep(1) 
                    continue
            return resp
            
        except Exception as e:
            print(f"    -> ⚠️ Network exception on attempt {attempt}: {e}")
            if attempt < max_retries:
                time.sleep(3)
                continue
    return None

def booking_worker():
    """
    A single, dedicated worker thread that pulls seats from a queue,
    locks them, and logs them to a Supabase database.
    """
    while True:
        job = BOOKING_QUEUE.get()
        
        seat_label = job["seat_label"]
        seat_meta = job["seat_meta"]
        session_info = job["session_info"]
        temp_trans_id = job["temp_trans_id"]

        try:
            thread_proxy = random.choice(SNIPER_PROXIES) if SNIPER_PROXIES else None
            
            print(f"\n[✅ WORKER] Picking up seat '{seat_label}'. Initiating lock.")
            session_id = session_info["sessionId"]
            show_time_str = session_info["show_date_code"] + "T" + session_info["show_time"] + ":00.000Z"
            
            ticket_price = 0
            for area in session_info.get("areas", []):
                if area.get("code") == seat_meta["areaCode"]:
                    ticket_price = int(area.get("price", 0))
                    break
                    
            ticket_type = f"{session_id}_{seat_meta['areaDesc']}"

            # --- STEP 1: SET SEAT (Locking) ---
            lock_url = "https://api.edition.in/gw/consumer/movies/v2/set-seat?version=3&site_id=1&channel=ANDROIDAPPINSIDER&child_site_id=1&platform=district"
            lock_payload = {
                "cancelOld": False, "cinemaId": int(THEATRE_ID), "sessionId": session_id,
                "providerId": 1896, "tempTransId": temp_trans_id, "ticketType": ticket_type,
                "noOfTickets": 1, "freeSeating": False, "showDateTime": show_time_str,
                "ticketPrice": ticket_price, "stateCode": "36", "ticketCount": 1, 
                "totalTicketPrice": ticket_price, 
                "areaNumber": [int(seat_meta["areaNum"])], 
                "gridSeatRowId": [seat_meta["gridRowId"]],
                "gridSeatNumber": [seat_meta["gridSeatNum"]],
                "areaCategoryCode": [seat_meta["areaCode"]]
            }
            
            headers_android = POST_HEADERS.copy()
            headers_android.update({"X-Device-Id": DEVICE_ID, "X-Access-Token": ACCESS_TOKEN, "X-Refresh-Token": REFRESH_TOKEN})
            minified_payload = json.dumps(lock_payload, separators=(',', ':'))
            
            print(f"    -> [WORKER - {seat_label}] Locking seat...")
            lock_resp = make_request('POST', lock_url, headers=headers_android, data=minified_payload, proxy_override=thread_proxy)

            if lock_resp is None or lock_resp.status_code != 200:
                print(f"    -> [WORKER - {seat_label}] ❌ Lock request failed with status: {lock_resp.status_code if lock_resp else 'None'}. Response: {lock_resp.text[:200] if lock_resp else ''}")
                IN_PROGRESS_SEATS.discard(seat_label)
                BOOKING_QUEUE.task_done()
                continue

            lock_data = lock_resp.json()
            booking_id = lock_data.get("bookingId")
            
            if not booking_id:
                raise Exception(f"Failed to lock seat! No bookingId found in response. Full response: {lock_data}")

            # --- STEP 2: PARSE & LOG TO SUPABASE ---
            print(f"    -> [WORKER - {seat_label}] Lock successful. Preparing to log to database.")
            
            seat_info_raw = lock_data.get("seatInfo", "")
            seating_class, seat_num = seat_info_raw.split("-", 1) if "-" in seat_info_raw else (None, seat_info_raw)

            data_to_insert = {
                "transaction_id": booking_id,
                "booking_id": booking_id,
                "trans_uid": booking_id,
                "seat": seat_num,
                "seating_class": seating_class,
                "snipe_timestamp": lock_data.get("cartCreatedAt"),
                "status": "AVAILABLE",
                "venue_code": THEATRE_ID,
                "platform_name": "district",
                "event_title": session_info.get("event_title"),
                "event_language": session_info.get("event_language"),
                "event_dimension": session_info.get("event_dimension"),
                "show_date_code": session_info.get("show_date_code"),
                "show_time": session_info.get("show_time"),
                "screen_name": session_info.get("screen_name"),
                "attributes": session_info.get("attributes")
            }
            
            # --- Retry Logic for Database Insert ---
            logged_successfully = False
            for attempt in range(3):
                try:
                    print(f"    -> [DB - {seat_label}] Attempting to insert (Try {attempt + 1}/3)...")
                    supabase.table("tickets").insert(data_to_insert).execute()
                    print(f"    -> [DB - {seat_label}] ✅ Successfully logged to database.")
                    logged_successfully = True
                    break 
                except Exception as e:
                    print(f"    -> [DB - {seat_label}] ⚠️ Insert failed: {e}")
                    if attempt < 2:
                        time.sleep(2) # Wait before retrying
            
            if not logged_successfully:
                print(f"    -> [DB - {seat_label}] ❌ FAILED to log to Supabase after 3 attempts. Moving on.")

        except Exception as e:
            print(f"    -> [WORKER - {seat_label}] ❌ Fatal error in pipeline: {e}")
            IN_PROGRESS_SEATS.discard(seat_label)
            
        BOOKING_QUEUE.task_done()

def fetch_sessions():
    sessions = []
    for date_str in DATES:
        print(f"\n[NETWORK] Fetching District sessions for Date: {date_str}...")
        
        url = (
            f"https://www.district.in/gw/consumer/movies/v3/cinema?"
            f"meta=1&reqData=1&version=3&site_id=1&channel=web&child_site_id=1"
            f"&platform=district&cinemaId={THEATRE_ID}&date={date_str}"
        )
        
        resp = make_request('GET', url, headers=GET_HEADERS)
        if not resp or resp.status_code != 200:
            print(f"    -> Failed fetching {date_str}. Skipping...")
            continue
            
        try:
            data = resp.json()
            movies_meta = data.get("meta", {}).get("movies", [])
            if not movies_meta:
                print(f"    -> 0 shows listed currently for {date_str}.")
                continue
                
            target_movie_meta = None
            for movie in movies_meta:
                movie_name = movie.get("name", "").lower()
                if TARGET_MOVIE_TITLE.lower() in movie_name:
                    target_movie_meta = movie
                    print(f"    -> Matched title '{movie.get('name')}' to ID: {target_movie_meta.get('id')}")
                    break
                    
            if not target_movie_meta:
                print(f"    -> Target movie containing '{TARGET_MOVIE_TITLE}' not found for {date_str}.")
                continue
                
            all_sessions = data.get("pageData", {}).get("sessions", [])
            target_count = 0
            
            for show in all_sessions:
                if show.get("mid") == target_movie_meta.get("id") and show.get("premiumLabel") == SCREEN_LABEL:
                    # Parse date and time from the ISO string
                    show_datetime_str = show.get("showTime", "")
                    date_part, time_part = "", ""
                    if "T" in show_datetime_str:
                        date_part, time_part = show_datetime_str.split("T")

                    sessions.append({
                        # Original Data
                        "sessionId": show.get("sid"),
                        "showTime": show_datetime_str,
                        "contentId": target_movie_meta.get("contentId"),
                        "areas": show.get("areas", []),
                        
                        # Enriched Data for Supabase
                        "event_title": target_movie_meta.get("name"),
                        "event_language": target_movie_meta.get("lang"),
                        "event_dimension": target_movie_meta.get("scrnFmt"),
                        "show_date_code": date_part,
                        "show_time": time_part,
                        "screen_name": show.get("audi"),
                        "attributes": show.get("premiumLabel")
                    })
                    target_count += 1
                    
            print(f"    -> Filtered {target_count} target sessions for {date_str}.")
            
        except Exception as e:
            print(f"    -> JSON Parse error for {date_str}: {e}")
            
    return sessions

def fetch_seat_layout(session_id):
    url = "https://api.edition.in/gw/consumer/movies/v1/select-seat?version=3&site_id=1&channel=ANDROIDAPPINSIDER&child_site_id=1&platform=district"
    payload = {
        "cinemaId": THEATRE_ID,
        "sessionId": session_id
    }
    TEMP_HEADERS = {
        "Host": "api.edition.in",
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "com.application.zomato.district/1000026600 (Linux; U; Android 13; en_US; sdk_gphone64_x86_64; Build/TE1A.240213.009; Cronet/113.0.5672.51)",
        "Accept-Encoding": "gzip, deflate, br",
        "X-Guest-Token" : random.randint(1000000, 9999999)
    }
    print(f"    -> [POST] (Session: {session_id})")
    resp = make_request('POST', url, headers=TEMP_HEADERS, json=payload)
    print(resp.status_code)
    if not resp or resp.status_code != 200:
        return {}
        
    try:
        return resp.json()
    except Exception as e:
        print(f"    -> Exception parsing layout JSON: {e}")
        return {}

def parse_layout(json_data):
    """Parses layout and extracts rich metadata for target seats."""
    available_seats_by_row = {}
    target_seat_metadata = {}
    
    temp_trans_id = json_data.get("tempTransId")
    areas = json_data.get("seatLayout", {}).get("colAreas", {}).get("objArea", [])
    
    for area in areas:
        area_desc = area.get("AreaDesc")
        area_code = area.get("AreaCode")
        area_num = area.get("AreaNum")
        
        for row in area.get("objRow", []):
            row_letter = row.get("PhyRowId")
            grid_row_id = row.get("GridRowId")
            if not row_letter: 
                continue
                
            available_in_row = []
            for seat in row.get("objSeat", []):
                if seat.get("SeatStatus") == "0":
                    seat_num_str = str(seat.get("displaySeatNumber"))
                    grid_seat_num = str(seat.get("GridSeatNum"))
                    
                    available_in_row.append(seat_num_str)
                    
                    # If this is one of our sniper targets, save ALL its data!
                    full_seat_name = f"{row_letter}{seat_num_str}"
                    if full_seat_name in TARGET_SEATS:
                        target_seat_metadata[full_seat_name] = {
                            "areaDesc": area_desc,
                            "areaCode": area_code,
                            "areaNum": area_num,
                            "gridRowId": grid_row_id,
                            "gridSeatNum": grid_seat_num
                        }
                        
            if available_in_row:
                if row_letter not in available_seats_by_row:
                    available_seats_by_row[row_letter] = []
                available_seats_by_row[row_letter].extend(available_in_row)
                
    return available_seats_by_row, target_seat_metadata, temp_trans_id

def main():
    start_time = time.time()
    
    print("==================================================")
    print("🚀 STARTING DISTRICT SEAT SCRAPER (PROACTIVE SNIPER)")
    print("==================================================")
    # Start the single, dedicated booking worker thread in the background
    worker_thread = threading.Thread(target=booking_worker, daemon=True)
    worker_thread.start()
    print("[CONFIG] Dedicated booking worker has been started.")
    state = load_state()
    is_first_run = len(state) == 0
    if is_first_run:
        print("[STATE] Empty state found. Initializing baseline silently...")
    else:
        print(f"[STATE] Loaded existing state for {len(state)} sessions.")

    cycle_count = 1
    
    while (time.time() - start_time) < MAX_RUNTIME_SECONDS:
        print(f"\n==================================================")
        print(f"🔄 STARTING POLLING CYCLE {cycle_count}")
        print(f"==================================================")
        
        # Fetch dynamically every cycle. 
        # If date 1 drops, it tracks it. Next cycle it still checks date 2 for new drops!
        target_sessions = fetch_sessions()
        total_sessions = len(target_sessions)
        
        if total_sessions == 0:
            print(f"\n[!] No sessions live yet. Waiting 5 seconds...")
            time.sleep(5)
            cycle_count += 1
            continue
            
        print(f"\n✅ Tracking {total_sessions} live matching sessions.")
        
        state = load_state()
        deltas = {} 
        
        for index, session in enumerate(target_sessions, 1):
            s_id = session["sessionId"]
            s_time = session["showTime"]

            ist_log_time = humanize_datetime(s_time)
            
            print(f"\n[{index}/{total_sessions}] Checking Session {s_id} (Time: {s_time})")
            print("    -> Sleeping for 1 seconds")
            time.sleep(1) 
            
            raw_layout_json = fetch_seat_layout(s_id)
            if not raw_layout_json:
                print("    -> Error: Failed to retrieve or parse seat layout.")
                continue
                
            current_seats, target_metadata, temp_trans_id = parse_layout(raw_layout_json)
            current_total = sum(len(seats) for seats in current_seats.values())
            print(f"    -> Parse successful. Current Available Seats: {current_total}")
            
            # --- FIRE SNIPER THREADS ---
            for target_seat, meta in target_metadata.items():
                if target_seat not in IN_PROGRESS_SEATS:
                    print(f"    -> 🎯 TARGET SEAT AVAILABLE: {target_seat}! Adding to booking queue.")
                    IN_PROGRESS_SEATS.add(target_seat)
                    
                    # Create a job package with all the data the worker needs
                    job = {
                        "seat_label": target_seat,
                        "seat_meta": meta,
                        "session_info": session,
                        "temp_trans_id": temp_trans_id
                    }
                    BOOKING_QUEUE.put(job)
            
            if s_id not in state:
                state[s_id] = {"showTime": s_time, "total": 0, "rows": {}}
            
            previous_total = state[s_id].get("total", 0)
            previous_rows = state[s_id].get("rows", {})
            
            newly_unblocked_count = 0
            unblocked_rows_list = []
            
            # Mathematical set diffing to find new seats
            for row, seats in current_seats.items():
                old_seats_in_row = previous_rows.get(row, [])
                new_seats = set(seats) - set(old_seats_in_row)
                
                if new_seats:
                    newly_unblocked_count += len(new_seats)
                    unblocked_rows_list.append(row)
            
            if newly_unblocked_count > 0:
                print(f"    -> 🟢 DETECTED UNBLOCKS: +{newly_unblocked_count} new seats!")
                
                if not is_first_run:
                    if newly_unblocked_count >= 3:
                        rows_str = ", ".join(sorted(unblocked_rows_list))
                        human_time = humanize_datetime(s_time)

                        msg = (
                            f"[{newly_unblocked_count}] {rows_str} unblocked\n"
                            f"{SCREEN_LABEL} {TARGET_MOVIE_TITLE} {human_time}"
                        )
                        trigger_ntfy(msg)
                    else:
                        print(f"    -> 🟡 Less than 3 seats unblocked ({newly_unblocked_count}). Skipping notification.")
                
                state[s_id]["rows"] = current_seats
                state[s_id]["total"] = current_total
                deltas[s_id] = state[s_id]

            elif current_total < previous_total:
                print(f"    -> 🔴 Seats booked. Total dropped from {previous_total} down to {current_total}.")
                state[s_id]["rows"] = current_seats
                state[s_id]["total"] = current_total
                deltas[s_id] = state[s_id]
                
            else:
                print("    -> ⚪ No changes detected.")

        if deltas:
            print("\n[STATE] Cycle finished. Changes detected, merging and saving to Git...")
            state = save_state(deltas, f"State update at cycle {cycle_count}")
        else:
            print("\n[STATE] Cycle finished. No changes detected.")
            
        if is_first_run:
            is_first_run = False
            print("[STATE] First run baseline has been successfully established.")
            
        cycle_count += 1
        
    print("\n🏁 Time limit reached. Gracefully shutting down.")

if __name__ == "__main__":
    main()
