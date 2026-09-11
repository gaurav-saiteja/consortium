import requests
from curl_cffi import requests as cffi_requests
import time
import json
import os
import subprocess
from datetime import datetime

# --- CONFIGURATION ---
DATES = ["2026-09-11"]
CINEMA_ID = "1039848"
TARGET_MOVIE_ID = "OBCISK" # E.g., 'Epic' from the sample response
TARGET_PREMIUM_LABEL = "64CH ATMOS"
GUEST_TOKEN = "1789"

STATE_FILE = "district_seat_state.json"
MAX_RUNTIME_SECONDS = (5 * 3600) + (55 * 60) # 5 hours 55 mins

# Track WARP State natively
USE_WARP = False

# Cloudflare WARP local proxy
PROXIES = {
    "http": "socks5://127.0.0.1:40000",
    "https": "socks5://127.0.0.1:40000"
}

# 1. Headers for district.in (Web Spoofing for GET)
GET_HEADERS = {
    "Host": "www.district.in",
    "X-Guest-Token": GUEST_TOKEN,
    "Api_source": "district",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9"
}

# 2. Headers for api.edition.in (Android App Spoofing for POST)
POST_HEADERS = {
    "Host": "api.edition.in",
    "X-Guest-Token": GUEST_TOKEN,
    "Content-Type": "application/json; charset=utf-8",
    "User-Agent": "com.application.zomato.district/1000026600 (Linux; U; Android 13; en_US; sdk_gphone64_x86_64; Build/TE1A.240213.009; Cronet/113.0.5672.51)",
    "Accept-Encoding": "gzip, deflate, br"
}

def humanize_datetime(iso_str):
    """Parses District's ISO 8601 strings (2026-09-11T13:25) into human readable text."""
    try:
        dt = datetime.fromisoformat(iso_str)
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

def make_request(method, url, max_retries=3, **kwargs):
    for attempt in range(1, max_retries + 1):
        current_proxies = PROXIES if USE_WARP else None
        try:
            if method.upper() == 'GET':
                resp = cffi_requests.get(url, proxies=current_proxies, impersonate="chrome", timeout=15, **kwargs)
            else:
                resp = cffi_requests.post(url, proxies=current_proxies, impersonate="chrome", timeout=15, **kwargs)
            
            print(f"    -> Status: {resp.status_code} (Using WARP: {USE_WARP})")
            
            if resp.status_code in [429, 403]:
                print(f"    -> ⚠️ Rate limited ({resp.status_code}) on attempt {attempt}/{max_retries}.")
                if attempt < max_retries:
                    toggle_warp()
                    continue
            return resp
            
        except Exception as e:
            print(f"    -> ⚠️ Network exception on attempt {attempt}: {e}")
            if attempt < max_retries:
                time.sleep(3)
                continue
    return None

def fetch_sessions():
    sessions = []
    for date_str in DATES:
        time.sleep(1)
        print(f"\n[NETWORK] Fetching District sessions for Date: {date_str}...")
        
        url = (
            f"https://www.district.in/gw/consumer/movies/v3/cinema?"
            f"meta=1&reqData=1&version=3&site_id=1&channel=web&child_site_id=1"
            f"&platform=district&cinemaId={CINEMA_ID}&date={date_str}"
        )
        
        resp = make_request('GET', url, headers=GET_HEADERS)
        if not resp or resp.status_code != 200:
            print(f"    -> Failed fetching {date_str}. Skipping...")
            continue
            
        try:
            data = resp.json()
            all_sessions = data.get("pageData", {}).get("sessions", [])
            print(f"    -> Found {len(all_sessions)} total shows. Filtering for target movie and format...")
            
            target_count = 0
            for show in all_sessions:
                # Filter precisely by Movie ID and Premium format type
                if show.get("mid") == TARGET_MOVIE_ID and show.get("premiumLabel") == TARGET_PREMIUM_LABEL:
                    sessions.append({
                        "sessionId": show.get("sid"),
                        "showTime": show.get("showTime")
                    })
                    target_count += 1
                    
            print(f"    -> Filtered {target_count} target sessions for {date_str}.")
            
        except Exception as e:
            print(f"    -> JSON Parse error for {date_str}: {e}")
            
    return sessions

def fetch_seat_layout(session_id):
    url = "https://api.edition.in/gw/consumer/movies/v1/select-seat?version=3&site_id=1&channel=ANDROIDAPPINSIDER&child_site_id=1&platform=district"
    payload = {
        "cinemaId": CINEMA_ID,
        "sessionId": session_id
    }
    
    print(f"    -> [POST] {url.split('?')[0]} (Session: {session_id})")
    resp = make_request('POST', url, headers=POST_HEADERS, json=payload)
    
    if not resp or resp.status_code != 200:
        return {}
        
    try:
        return resp.json()
    except Exception as e:
        print(f"    -> Exception parsing layout JSON: {e}")
        return {}

def parse_layout(json_data):
    """
    Lean JSON-tree traversal algorithm. Extracts exactly what we need
    without heavy Regex string processing.
    """
    available_seats_by_row = {}
    
    # Safely navigate the nested JSON structure
    areas = json_data.get("seatLayout", {}).get("colAreas", {}).get("objArea", [])
    
    for area in areas:
        for row in area.get("objRow", []):
            row_letter = row.get("PhyRowId")
            if not row_letter: 
                continue
                
            available_in_row = []
            for seat in row.get("objSeat", []):
                # Status "0" means Available on District platform
                if seat.get("SeatStatus") == "0":
                    available_in_row.append(str(seat.get("displaySeatNumber")))
                    
            if available_in_row:
                if row_letter not in available_seats_by_row:
                    available_seats_by_row[row_letter] = []
                available_seats_by_row[row_letter].extend(available_in_row)
                
    return available_seats_by_row

def main():
    start_time = time.time()
    
    print("==================================================")
    print("🚀 STARTING DISTRICT SEAT SCRAPER")
    print("==================================================")
    
    target_sessions = fetch_sessions()
    total_sessions = len(target_sessions)
    
    if total_sessions == 0:
        print("No valid target sessions found. Exiting.")
        return

    print(f"\n✅ Found {total_sessions} matching sessions to monitor.")
    print("==================================================")

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
        
        state = load_state()
        deltas = {} 
        
        for index, session in enumerate(target_sessions, 1):
            s_id = session["sessionId"]
            s_time = session["showTime"]
            
            print(f"\n[{index}/{total_sessions}] Checking Session {s_id} (Time: {s_time})")
            print("    -> Sleeping for 26 seconds (Rate Limit Prevention)...")
            time.sleep(4) 
            
            raw_layout_json = fetch_seat_layout(s_id)
            if not raw_layout_json:
                print("    -> Error: Failed to retrieve or parse seat layout.")
                continue
                
            current_seats = parse_layout(raw_layout_json)
            current_total = sum(len(seats) for seats in current_seats.values())
            print(f"    -> Parse successful. Current Available Seats: {current_total}")
            
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
                            f"[{newly_unblocked_count}] {TARGET_PREMIUM_LABEL} seats unblocked!\n"
                            f"Movie: {TARGET_MOVIE_ID}\n"
                            f"Rows Unblocked: {rows_str}\n"
                            f"Time: {human_time}"
                        )
                        trigger_ntfy(msg)
                    else:
                        print(f"    -> 🟡 Less than 3 seats unblocked ({newly_unblocked_count}). Skipping notification (Spam Protection).")
                
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
