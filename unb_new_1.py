import requests
from curl_cffi import requests as cffi_requests
import time
import json
import os
import re
import random
import subprocess
from datetime import datetime

# --- CONFIGURATION ---
DATES = ["20261018"]
VENUE_CODE = "PRHN"
TARGET_MOVIE = "jailer"     
TARGET_SCREEN = "4"
STATE_FILE = "third_day_state.json"
MAX_RUNTIME_SECONDS = (5 * 3600) + (55 * 60)

user_agents = [
    "Mozilla/5.0 (Linux; U; Android 16;zh-cn; TB375FC Build/BP2A.250605.031.A3) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/141.0.7389.0 MobileLenovoBrowser/9.2.7 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 8.1.0; vivo Y83) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/70.0.3538.110 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; Infinix X6532 Build/UP1A.231005.007) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.7871.181 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 11; Lenovo YT-J706X) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.45 Safari/537.36",
    "Mozilla/5.0 (Linux; Android 15; SM-S908B Build/AP3A.240905.015.A2; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/150.0.7871.181 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; U; Android 13; zh-cn; M2012K11AC Build/TKQ1.221114.001) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.7049.79 Mobile Safari/537.36 XiaoMi/MiuiBrowser/20.25.1020805",
    "Mozilla/5.0 (Linux; Android 16; 23129RN51X Build/BP2A.250605.031.A3) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/150.0.7871.181 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; 2210132C) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; V2247) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 10; A001SH) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.138 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; moto g power (2022)) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.122 Mobile Safari/537.36", # Samsung Galaxy S24 Ultra
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.165 Mobile Safari/537.36", # Google Pixel 8 Pro
    "Mozilla/5.0 (Linux; Android 14; CPH2581) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.179 Mobile Safari/537.36", # OnePlus 12
    "Mozilla/5.0 (Linux; Android 14; SM-A546B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.110 Mobile Safari/537.36", # Samsung Galaxy A54 5G
    "Mozilla/5.0 (Linux; Android 13; 22101316G) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.119 Mobile Safari/537.36", # Xiaomi Redmi Note 12 Pro
    "Mozilla/5.0 (Linux; Android 14; A065) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.122 Mobile Safari/537.36", # Nothing Phone (2)
    "Mozilla/5.0 (Linux; Android 13; CPH2525) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.6167.178 Mobile Safari/537.36", # Oppo Reno 10 Pro
    "Mozilla/5.0 (Linux; Android 13; RMX3561) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.6312.100 Mobile Safari/537.36", # Realme GT Neo 3
    "Mozilla/5.0 (Linux; Android 14; Pixel 6a) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.122 Mobile Safari/537.36", # Google Pixel 6a
    "Mozilla/5.0 (Linux; Android 14; V2324A) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.113 Mobile Safari/537.36", # Vivo X100 Pro
    "Mozilla/5.0 (Linux; Android 14; SM-F946B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.122 Mobile Safari/537.36", # Samsung Galaxy Z Fold 5
    "Mozilla/5.0 (Linux; Android 14; XQ-DQ72) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.112 Mobile Safari/537.36", # Sony Xperia 1 V
    "Mozilla/5.0 (Linux; Android 13; motorola edge 40 neo) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.179 Mobile Safari/537.36", # Motorola Edge 40 Neo
    "Mozilla/5.0 (Linux; Android 14; 23049PCD8G) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.122 Mobile Safari/537.36", # Poco F5
    "Mozilla/5.0 (Linux; Android 14; SM-X710) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.122 Safari/537.36" # Samsung Galaxy Tab S9 (Tablet)
]

# Track WARP State natively
USE_WARP = False

# Cloudflare WARP local proxy
PROXIES = {
    "http": "socks5://127.0.0.1:40000",
    "https": "socks5://127.0.0.1:40000"
}


def generate_headers(is_post=False):
    app_sub1 = f"1{random.randint(10, 99)}"
    app_sub2 = f"1{random.randint(10, 99)}"
    bms_id = f"1.4{random.randint(1000000, 9999999)}.{random.randint(1000000000000, 9999999999999)}"
    device_id = f"{random.randint(0, 0xFFFFFFFFFFFFFFFF):016x}"
    ua = random.choice(user_agents) if user_agents else "Dalvik/2.1.0 (Linux; U; Android 10; Build/QSR1.211112.011)"

    headers = {
        "Host": "services-in.bookmyshow.com" if is_post else "in.bookmyshow.com",
        "X-Bms-Id": bms_id,
        "X-Device-Id": device_id,
        "User-Agent": ua,
        "X-App-Version": f"18.{app_sub1}.{app_sub2}",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive"
    }
    if is_post:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    else:
        headers["Lang"] = "en"
        
    return headers

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
    """Syncs with remote and loads the freshest state into memory."""
    quiet_git_pull()
    return read_local_state()

def save_state(deltas, commit_msg="Update seat state"):
    """
    Takes a dictionary of local session changes (deltas), cleanly merges them with the 
    absolute latest Git state, and pushes. Retries seamlessly if another runner pushes first.
    Returns the newly merged state so the runner can update its memory.
    """
    for attempt in range(3):
        # 1. Force sync local repo with remote (drops any failed local commits from prior attempts)
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

def trigger_ntfy(message):
    print(f"\n[!] ALERTING VIA NTFY: {message}")
    for i in range(1):
        try:
            resp = requests.post(
                "https://ntfy.sh/dolby_unblock",
                data=message.encode('utf-8'),
                headers={"Priority": "urgent"},
                timeout=10
            )
            print(f"    -> Ntfy ping {i+1}/1 sent! Status: {resp.status_code}")
        except Exception as e:
            print(f"    -> Ntfy ping {i+1} failed: {e}")

def toggle_warp():
    """Toggles Cloudflare WARP on/off and updates the proxy state."""
    global USE_WARP
    if USE_WARP:
        print("    -> 🚨 [IP ROTATION] WARP is currently ON. Disconnecting WARP (Switching to Runner IP)...")
        subprocess.run(["warp-cli", "--accept-tos", "disconnect"], capture_output=True, check=False)
        USE_WARP = False
    else:
        print("    -> 🚨 [IP ROTATION] WARP is currently OFF. Connecting to WARP (Switching to Cloudflare Proxy)...")
        subprocess.run(["warp-cli", "--accept-tos", "connect"], capture_output=True, check=False)
        time.sleep(5)  # Wait for the tunnel to establish
        USE_WARP = True

def make_bms_request(method, url, max_retries=3, **kwargs):
    endpoint_path = url.split("?")[0].split("/")[-1]
    is_post = method.upper() == 'POST'

    for attempt in range(1, max_retries + 1):
        current_proxies = PROXIES if USE_WARP else None
        headers = generate_headers(is_post=is_post)
        
        try:
            if is_post:
                resp = cffi_requests.post(url, headers=headers, proxies=current_proxies, impersonate="chrome", timeout=15, **kwargs)
            else:
                resp = cffi_requests.get(url, headers=headers, proxies=current_proxies, impersonate="chrome", timeout=15, **kwargs)
            
            # Request status code logging
            print(f"[{method.upper()}] /{endpoint_path} -> HTTP {resp.status_code} (WARP: {USE_WARP})")
            
            if resp.status_code in [429, 403]:
                print(f"    -> ⚠️ Rate limited ({resp.status_code}) on attempt {attempt}/{max_retries}.")
                if attempt < max_retries:
                    toggle_warp()
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
        url = f"https://in.bookmyshow.com/api/v3/mobile/showtimes/byvenue?appCode=MOBAND2&venueCode={VENUE_CODE}&dateCode={date_code}"
        
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
                    
                    # Safe-net movie search across master EventTitle and ChildEvent names
                    title_match = matches_movie(event_title, TARGET_MOVIE) or any(
                        matches_movie(c.get("EventName", ""), TARGET_MOVIE) for c in child_events
                    )
                    if not title_match:
                        continue
                        
                    for child in child_events:
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
                                    "screenName": screen_name
                                })
                                seen_session_ids.add(session_id)
                                matched_count += 1
                                
            print(f"    -> Matched {matched_count} session(s) for '{TARGET_MOVIE}' on Screen {TARGET_SCREEN} ({date_code}).")
            
        except Exception as e:
            print(f"    -> JSON parse error for {date_code}: {e}")
            
    return sessions

def fetch_seat_layout(session_id):
    url = "https://services-in.bookmyshow.com/doTrans.aspx"
    payload = f"strParam4=&strParam5=Y&strParam6=&strParam7=N&strParam1={session_id}&strParam2=WEB&strParam3=&strVenueCode={VENUE_CODE}&lngTransactionIdentifier=0&strAppCode=MOBAND2&strFormat=json&strCommand=GETSEATLAYOUT"
    
    resp = make_bms_request('POST', url, data=payload)
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
        row_letter = elements[1]  # Extracts row letter (e.g., 'A', 'B')
        seats = elements[2:]      # Extracts the seat strings
        
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
    print("🚀 STARTING BMS SEAT SCRAPER")
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
        print("[STATE] Empty state found. Initializing baseline silently...")
    else:
        print(f"[STATE] Loaded existing state for {len(state)} sessions.")

    cycle_count = 1
    
    while (time.time() - start_time) < MAX_RUNTIME_SECONDS:
        print(f"\n==================================================")
        print(f"🔄 STARTING POLLING CYCLE {cycle_count}")
        print(f"==================================================")
        
        # Pull latest state before starting the cycle
        state = load_state()
        deltas = {} # Track ONLY the sessions that change during this cycle
        
        for index, session in enumerate(target_sessions, 1):
            s_id = session["sessionId"]
            s_date = session["dateCode"]
            s_time = session["time"]
            
            print(f"\n[{index}/{total_sessions}] Checking Session {s_id} (Date: {s_date} Time: {s_time})")
            print("    -> Sleeping for 30 seconds (Rate Limit Prevention)...")
            time.sleep(10) 
            
            str_data = fetch_seat_layout(s_id)
            if not str_data:
                continue
                
            current_seats = parse_layout(str_data)
            current_total = sum(len(seats) for seats in current_seats.values())
            
            # Minimal seat logging
            print(f"    -> Session [{s_id}] ({s_time}) | Available Seats: {current_total}")
            
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
                print(f"    -> 🟢 DETECTED UNBLOCKS: +{newly_unblocked_count} new seats!")
                
                if not is_first_run:
                    if newly_unblocked_count >= 3:
                        rows_str = ", ".join(sorted(unblocked_rows_list))
                        human_date = humanize_date(s_date)
                        movie_name = session.get("movieTitle", TARGET_MOVIE)
                        screen_label = session.get("screenName", f"Screen {TARGET_SCREEN}")

                        msg = (
                            f"[{newly_unblocked_count}] {movie_name} ({screen_label})\n"
                            f"Rows: {rows_str}\n\n"
                            f"{human_date}, {s_time}"
                        )
                        trigger_ntfy(msg)
                    else:
                        print(f"    -> 🟡 Less than 3 seats unblocked ({newly_unblocked_count}). Skipping notification.")
                
                # Update memory & Track Delta
                state[s_id]["rows"] = current_seats
                state[s_id]["total"] = current_total
                deltas[s_id] = state[s_id]

            elif current_total < previous_total:
                print(f"    -> 🔴 Seats booked. Total dropped from {previous_total} down to {current_total}.")
                # Update memory & Track Delta
                state[s_id]["rows"] = current_seats
                state[s_id]["total"] = current_total
                deltas[s_id] = state[s_id]
                
            else:
                print("    -> ⚪ No changes detected.")

        if deltas:
            print("\n[STATE] Cycle finished. Changes detected, merging and saving to Git...")
            # Save state will handle merging our deltas with the newest Git data
            # and return the freshly synced state to update our memory
            state = save_state(deltas, f"State update at cycle {cycle_count}")
        else:
            print("\n[STATE] Cycle finished. No changes detected.")
            
        if is_first_run:
            is_first_run = False
            print("[STATE] First run baseline has been successfully established.")
            
        cycle_count += 1
        
    print("\n🏁 Time limit reached (5h 55m). Gracefully shutting down.")

if __name__ == "__main__":
    main()
