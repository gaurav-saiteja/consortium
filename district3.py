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
DATES = ["2026-09-13"]
THEATRE_ID = "1039848" #vimal 70mm
TARGET_MOVIE_TITLE = "epic"
SCREEN_LABEL = "64CH ATMOS"
BOOKING_QUEUE = queue.Queue()
# --- AUTOBUIER CONFIGURATION ---
TARGET_SEATS = ["Q13"] # Add the specific seats you want here!

DEVICE_ID = "e655d7074359cd97"
USER_ID = "94001055"
ACCESS_TOKEN = "1789212473111770172_711805081438907057_6d65d672fcf1c7b42f59237d40918f43fed620a9b53570ac642f9632198bb3b8"
REFRESH_TOKEN = "1789212473111774137_6651234817425529245_e28196895d819bdaf4707d1d3d3da4edfec986017e5281c6114f9e3bee3c3dac"

# The Cookie string for web endpoints (checkout & SDK token)
COOKIE_STRING = f"x-device-id={DEVICE_ID}; x-access-token={ACCESS_TOKEN}; x-refresh-token={REFRESH_TOKEN};"

# Thread-safe tracking to prevent double-buying
IN_PROGRESS_SEATS = set()

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
    A single, dedicated worker thread that pulls seats from a queue 
    and processes them one by one to avoid rate-limiting.
    """
    while True:
        # This line will wait patiently until a seat is added to the queue
        job = BOOKING_QUEUE.get()
        
        seat_label = job["seat_label"]
        seat_meta = job["seat_meta"]
        session_info = job["session_info"]
        temp_trans_id = job["temp_trans_id"]

        try:
            # Pick a random dedicated proxy for this specific job!
            thread_proxy = random.choice(SNIPER_PROXIES) if SNIPER_PROXIES else None
            
            print(f"\n[✅ WORKER] Picking up seat '{seat_label}'. Initiating checkout.")
            session_id = session_info["sessionId"]
            content_id = session_info["contentId"]
            show_time_str = session_info["showTime"] + ":00.000Z"
            
            # Match price from session areas
            ticket_price = 0
            for area in session_info.get("areas", []):
                if area.get("code") == seat_meta["areaCode"]:
                    # FIX 1: Explicitly cast to integer
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
            
            # FIX 2: Minify the payload perfectly with zero spaces
            minified_payload = json.dumps(lock_payload, separators=(',', ':'))
            
            print(f"    -> [WORKER - {seat_label}] Locking seat...")
            # FIX 3: Send using `data=minified_payload` instead of `json=lock_payload`
            lock_resp = make_request('POST', lock_url, headers=headers_android, data=minified_payload, proxy_override=thread_proxy)
            
            # --- DEBUG BLOCK: DUMP THE TRUTH ---
            if lock_resp is not None:
                print(f"\n========== 🕵️ RAW SERVER RESPONSE FOR {seat_label} ==========")
                print(f"STATUS CODE: {lock_resp.status_code}")
                print(f"RESPONSE BODY:\n{lock_resp.text}")
                print(f"PAYLOAD SENT:\n{lock_payload}")
                print(f"Headers:\n{headers_android}")
                print("=============================================================\n")
            else:
                print(f"\n[!] lock_resp is completely None. The request failed to send at all.")
            # -----------------------------------

            try:
                new_temp_trans_id = lock_resp.json().get("tempTransId")
            except Exception as e:
                raise Exception(f"Failed to parse JSON. Raw text was: {lock_resp.text[:200]}")

            if not new_temp_trans_id:
                raise Exception(f"Failed to lock seat! No tempTransId found in response.")

            # --- STEP 2: CHECKOUT ---
            checkout_url = "https://www.district.in/gw/consumer/movies/v2/checkout?version=3&site_id=1&channel=web&child_site_id=1&platform=district&native_withdraw=1"
            checkout_payload = {
                "contentId": content_id, "tempTransId": new_temp_trans_id,
                "paymentDetails": [{"payment_method_id": "50", "payment_method_type": "upi_qr", "wallet_balance": "0.0", "wallet_id": "0"}]
            }

            CHECKOUT_HEADERS = {
                "Host": "www.district.in",
                "Cookie": f"x-device-id={DEVICE_ID}; x-access-token={ACCESS_TOKEN}; x-refresh-token={REFRESH_TOKEN};",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",
                "Accept": "*/*",
                "Origin": "https://www.district.in",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Content-Type": "application/json; charset=utf-8",
                "Sec-Fetch-Dest": "empty",
                "Referer": "https://www.district.in",
                "Accept-Encoding": "gzip, deflate, br",
                "Accept-Language": "en-US,en;q=0.9",
                "Priority": "u=1, i"
            }
            
            print(f"    -> [WORKER - {seat_label}] Generating Order ID...")
            
            # 1. Minify the payload to remove all spaces, matching the actual client exactly
            minified_checkout_payload = json.dumps(checkout_payload, separators=(',', ':'))
            
            # 2. Use data= instead of json= to prevent the request library from reformatting it
            checkout_resp = make_request('POST', checkout_url, headers=CHECKOUT_HEADERS, data=minified_checkout_payload)
            print(CHECKOUT_HEADERS)
            print(checkout_payload)
            print(checkout_resp.status_code)
            print(checkout_resp.text)
            checkout_data = checkout_resp.json()
            order_id = checkout_data.get("ORDER_ID")
            pg_resp = checkout_data.get("pgResponse", {})
            grand_total = pg_resp.get("grandTotal")
            pay_hash = pg_resp.get("hash")

            # --- STEP 3: GET SDK TOKEN ---
            print(f"    -> [WORKER - {seat_label}] Fetching SDK Token...")
            token_url = "https://www.district.in/gw/payments/in/get-sdk-token?version=3&site_id=1&channel=web&child_site_id=1&platform=district"
            token_resp = make_request('POST', token_url, headers=headers_web, proxy_override=thread_proxy)
            sdk_token = token_resp.json().get("token")

            # --- STEP 4: MAKE PAYMENT (QR CODE) ---
            print(f"    -> [WORKER - {seat_label}] Generating UPI QR Code...")
            pay_url = "https://zpay.zomato.com/v2/sdk/make_payment"
            pay_payload = (
                f"host_redirect_url=https%3A%2F%2Fwww.district.in%2Fmovies%2Forder%2F{order_id}&"
                f"service_type=ED_MOVIES&user_id={USER_ID}&order_type=ED_MOVIES&order_id={order_id}&"
                f"country_id=1&payment_method_id=50&payment_method_type=upi_qr&amount={grand_total}&"
                f"payments_hash={pay_hash}&phone=9247191127&isMobileView=1"
            )
            pay_headers = {
                "Host": "zpay.zomato.com", "X-Pas-Token": sdk_token, "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": GET_HEADERS["User-Agent"], "Origin": "https://www.district.in"
            }
            
            pay_resp = make_request('POST', pay_url, headers=pay_headers, data=pay_payload, proxy_override=thread_proxy)
            qr_b64 = pay_resp.json().get("response", {}).get("transaction", {}).get("qr_data", {}).get("image")
            
            if qr_b64:
                # --- STEP 5: PUSH TO NTFY AS RAW IMAGE ---
                print(f"    -> [WORKER - {seat_label}] Success! Sending QR to phone...")
                clean_b64 = qr_b64.replace("\\", "")
                image_bytes = base64.b64decode(clean_b64)
                
                requests.post(
                    "https://ntfy.sh/dolby_unblock",
                    data=image_bytes,
                    headers={
                        "Title": f"🎫 SEAT LOCKED: {seat_meta['areaDesc']} - {seat_label}",
                        "Tags": "ticket,moneybag",
                        "Priority": "urgent",
                        "Filename": "pay_qr.png"
                    },
                    timeout=10
                )
            else:
                print(f"    -> [WORKER - {seat_label}] ❌ Failed to extract QR Code.")
                
        except Exception as e:
            print(f"    -> [WORKER - {seat_label}] ❌ Error in pipeline: {e}")
            IN_PROGRESS_SEATS.discard(seat_label) # Free it up to try again next cycle!
            
        # Mark the task as done, so the queue knows it can move on.
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
            
            # Step 1: Safely check if ANY movies are listed yet
            movies_meta = data.get("meta", {}).get("movies", [])
            if not movies_meta:
                print(f"    -> 0 shows listed currently for {date_str}.")
                continue
                
            # Step 2: Dynamic ID matching via Title (Contains, Case-Insensitive)
            target_mid = None
            target_content_id = None
            for movie in movies_meta:
                movie_name = movie.get("name", "").lower()
                if TARGET_MOVIE_TITLE.lower() in movie_name:
                    target_mid = movie.get("id")
                    target_content_id = movie.get("contentId")
                    print(f"    -> Matched title '{movie.get('name')}' to ID: {target_mid}")
                    break
                    
            if not target_mid:
                print(f"    -> Target movie containing '{TARGET_MOVIE_TITLE}' not found in listed movies for {date_str}.")
                continue
                
            # Step 3: Filter the sessions for the dynamically matched ID and Format
            all_sessions = data.get("pageData", {}).get("sessions", [])
            target_count = 0
            
            for show in all_sessions:
                if show.get("mid") == target_mid and show.get("premiumLabel") == SCREEN_LABEL:
                    sessions.append({
                        "sessionId": show.get("sid"),
                        "showTime": show.get("showTime"),
                        "contentId": target_content_id,
                        "areas": show.get("areas", [])
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
