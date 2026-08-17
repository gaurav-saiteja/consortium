import requests
from curl_cffi import requests as cffi_requests
import time
import json
import os
import re
import subprocess
import random
import threading
import sys
import signal
import logging
from datetime import datetime

# --- LOGGING CONFIGURATION ---
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s"
)
logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
DATES = ["20260818"]
VENUE_CODE = "PVFS"
MAX_RUNTIME_SECONDS = (5 * 3600) + (55 * 60) # 5 hours 55 mins
TICKET_CATEGORY_3D = "4D"
TICKET_CATEGORY_2D = "4D"
# --- NEW: SHOWTIME CONSTRAINTS ---
TARGET_SCREEN_NAME = "AUDI 05 4DX"
TARGET_SHOW_INDEX = 2

# --- NEW: STRICT SEAT PRIORITY QUEUE ---
PRIORITY_SEATS = []

# Sequence 1: G, F, E, D in exact order for seats 06, 07, 05, 08
for row in ["G", "F", "E", "D", "C", "B", "A"]:
    for seat in ["06", "07", "05", "08"]:
        PRIORITY_SEATS.append((row, seat))

# Sequence 2: F, G, E, D in exact order for seats 09, 04, 10, 03, 11, 02, 12, 01
for row in ["F", "G", "E", "D", "C", "B"]:
    for seat in ["09", "04", "10", "03", "11", "02", "12", "01"]:
        PRIORITY_SEATS.append((row, seat))
for row in ["A"]:
    for seat in ["09", "04", "10", "03"]:
        PRIORITY_SEATS.append((row, seat))

PROXY_POOL = []
raw_proxies = os.environ.get("PROXY_LIST", "")
if raw_proxies:
    for line in raw_proxies.strip().split("\n"):
        parts = line.strip().split(":")
        if len(parts) == 4:
            ip, port, user, pwd = parts
            # curl_cffi requires the http://username:password@ip:port format
            PROXY_POOL.append(f"http://{user}:{pwd}@{ip}:{port}")

PROXIES = {
    "http": "socks5://127.0.0.1:40000",
    "https": "socks5://127.0.0.1:40000"
}

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
]

def generate_headers(is_post=False):
    bms_id = f"1.4{random.randint(1000000, 9999999)}.{random.randint(1000000000000, 9999999999999)}"
    device_id = "".join(random.choices("0123456789abcdef", k=16))
    ua = random.choice(user_agents)
    app_version_code = f"1{random.randint(10000, 99999)}"
    app_version = f"18.{random.randint(10, 99)}.{random.randint(10, 99)}"
    
    host = "services-in.bookmyshow.com" if is_post else "in.bookmyshow.com"
    
    return {
        "Host": host,
        "X-Bms-Id": bms_id,
        "X-Device-Id": device_id,
        "X-Latitude": "17.385044",
        "X-Subregion-Code": "HYD",
        "X-App-Code": "MOBAND2",
        "User-Agent": ua,
        "X-App-Version-Code": app_version_code,
        "X-Longitude": "78.48667",
        "X-Platform": "AND",
        "X-Region-Code": "HYD",
        "X-Region-Slug": "hyderabad",
        "X-Platform-Code": "ANDROID",
        "X-App-Version": app_version,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept-Encoding": "gzip, deflate"
    }

def humanize_date(date_str):
    if not date_str or len(date_str) != 8:
        return date_str
    dt = datetime.strptime(date_str, "%Y%m%d")
    day = dt.day
    if 11 <= (day % 100) <= 13:
        suffix = 'th'
    else:
        suffix = ['th', 'st', 'nd', 'rd', 'th'][min(day % 10, 4)]
    month_name = dt.strftime("%B")
    return f"{day}{suffix} {month_name}"



def make_bms_request(method, url, network_state, max_retries=5, **kwargs):
    for attempt in range(1, max_retries + 1):
        current_proxies = None
        
        # State Machine: 0 (Raw IP), 1 (WARP), 2 (Pool Proxy)
        if network_state["state"] == 1:
            current_proxies = PROXIES
        elif network_state["state"] == 2:
            if not network_state.get("pool_proxy") and PROXY_POOL:
                proxy_url = random.choice(PROXY_POOL)
                network_state["pool_proxy"] = {"http": proxy_url, "https": proxy_url}
            current_proxies = network_state.get("pool_proxy")

        try:
            if method.upper() == 'GET':
                resp = cffi_requests.get(url, proxies=current_proxies, impersonate="chrome", timeout=15, **kwargs)
                logger.info(f"{resp.status_code}")
            else:
                resp = cffi_requests.post(url, proxies=current_proxies, impersonate="chrome", timeout=15, **kwargs)
                logger.info(f"{resp.status_code}")
            
            if resp.status_code in [429, 403]:
                logger.warning(f"    -> 🚧 WAF Block (HTTP {resp.status_code}) on {method}. Thread jumping network state. (Attempt {attempt}/{max_retries})")
                if attempt < max_retries:
                    max_states = network_state.get("max_states", 2)
                    
                    # Jumping Loop:
                    # If max_states=2: 0 -> 1 -> 0
                    # If max_states=3: 0 -> 1 -> 2 -> 0
                    network_state["state"] = (network_state["state"] + 1) % max_states
                    
                    if network_state["state"] == 2:
                        network_state["pool_proxy"] = None # Force a new random proxy to be picked next loop
                    time.sleep(1)
                    continue
            return resp
        except Exception as e:
            logger.error(f"🌐 Request error on {method} (State {network_state['state']}, Attempt {attempt}/{max_retries}): {e}")
            if attempt < max_retries: 
                time.sleep(3)
                continue
    return None

# =======================================================
# PHASE 1: SHOWTIME MONITORING (USING VENUE API)
# =======================================================
def find_target_session():
    network_state = {"state": 0, "pool_proxy": None, "max_states": 2}
    valid_shows = []
    
    for date_code in DATES:
        url = f"https://in.bookmyshow.com/api/v3/mobile/showtimes/byvenue?appCode=MOBAND2&venueCode={VENUE_CODE}&dateCode={date_code}"
        resp = make_bms_request('GET', url, network_state=network_state, headers=generate_headers(is_post=False))
        logger.info(f"{resp.status_code}")
        if not resp or resp.status_code != 200: 
            continue
            
        fallback_logged = False
            
        try:
            data = resp.json()
            show_details_list = data.get("ShowDetails", [])
            
            for show_detail in show_details_list:
                for event in show_detail.get("Event", []):
                    parent_code = event.get("EventCode", "")
                    for child in event.get("ChildEvents", []):
                        current_event_code = child.get("EventCode", parent_code)
                        current_event_dimension = child.get("EventDimension", "")
                        
                        for show in child.get("ShowTimes", []):
                            s_date_code = show.get("ShowDateCode", "")
                            if s_date_code != date_code:
                                if not fallback_logged:
                                    logger.info(f"    -> ⚠️ API fallback detected! Requested {date_code} but received {s_date_code}. Ignoring fallback shows.")
                                    fallback_logged = True
                                continue
                            
                            s_screen_name = show.get("ScreenName", "")
                            
                            # 1. Constraint Check: Exact ScreenName Match (Case-insensitive & space-stripped)
                            if TARGET_SCREEN_NAME.strip().lower() != s_screen_name.strip().lower():
                                continue
                                
                            s_time_str = show.get("ShowTime", "")
                            try:
                                s_time_obj = datetime.strptime(s_time_str, "%I:%M %p").time()
                            except:
                                continue
                            
                            valid_shows.append({
                                "sessionId": show.get("SessionId"),
                                "eventCode": current_event_code,
                                "eventDimension": current_event_dimension,
                                "dateCode": show.get("ShowDateCode"),
                                "time": s_time_str,
                                "attribute": s_screen_name,
                                "datetime_obj": s_time_obj,
                                "screen": s_screen_name
                            })
        except Exception as e:
            logger.error(f"    -> ❌ Error parsing venue JSON for {date_code}: {e}")
            pass
            
    if valid_shows:
        # Sort chronologically ascending
        valid_shows.sort(key=lambda x: x["datetime_obj"])
        
        if 1 <= TARGET_SHOW_INDEX <= len(valid_shows):
            selected_show = valid_shows[TARGET_SHOW_INDEX - 1]
            logger.info(f"    -> [INDEX] Selected Show Index {TARGET_SHOW_INDEX} out of {len(valid_shows)} matches: {selected_show['time']}")
            return [selected_show] # Returned as list to preserve compatibility with downstream loop
        else:
            logger.warning(f"    -> [INDEX ERROR] Requested Index {TARGET_SHOW_INDEX} is out of bounds! Only {len(valid_shows)} shows matched constraints.")
            return []
        
    return []

# =======================================================
# PHASE 2 & 3: THREAD WORKER / LAYOUT PARSING
# =======================================================
def fetch_seat_layout(session_id, network_state):
    url = "https://services-in.bookmyshow.com/doTrans.aspx"
    payload = f"strParam4=&strParam5=Y&strParam6=&strParam7=N&strParam1={session_id}&strParam2=WEB&strParam3=&strVenueCode={VENUE_CODE}&lngTransactionIdentifier=0&strAppCode=MOBAND2&strFormat=json&strCommand=GETSEATLAYOUT"
    resp = make_bms_request('POST', url, network_state=network_state, headers=generate_headers(is_post=True), data=payload)
    logger.info(f"{resp.status_code}")
    if not resp or resp.status_code != 200: 
        return ""
    try: 
        return resp.json().get("BookMyShow", {}).get("strData", "")
    except Exception: 
        return ""

def parse_layout(str_data):
    if not str_data: return {}, {}, {}, 0
    parts = str_data.split("||")
    header_data = parts[0]
    
    categories = {}
    for cat in header_data.split("|"):
        c_parts = cat.split(":")
        if len(c_parts) >= 4:
            categories[c_parts[1]] = {"cat_code": c_parts[2], "area_id": c_parts[3]}
            
    rows_data = parts[1] if len(parts) > 1 else parts[0]
    rows = rows_data.split("|")
    
    available_seats_by_row = {}
    seat_metadata = {} 
    
    for row in rows:
        if not row or ":" not in row: continue
        elements = row.split(":")
        row_index = elements[0]
        row_letter = elements[1]
        seats = elements[2:]
        
        available_in_row = []
        for seat in seats:
            match = re.search(r"([A-Z])([14])(\d+)", seat)
            if match:
                block_code = match.group(1)
                backend_seat = match.group(3)
                seat_num = match.group(3)
                
                available_in_row.append(seat_num)
                seat_metadata[f"{row_letter}_{seat_num}"] = {
                    "block_code": block_code,
                    "row_index": row_index,
                    "backend_seat": backend_seat
                }
                
        if available_in_row:
            available_seats_by_row[row_letter] = available_in_row

    total_available_seats = sum(len(seats) for seats in available_seats_by_row.values())
    return available_seats_by_row, categories, seat_metadata, total_available_seats

def monitor_and_snipe_worker(session, assigned_seats, thread_id, start_time):
    # Independent thread-local network states starting at 0 (Raw IP)
    snipe_network_state = {"state": 0, "pool_proxy": None, "max_states": 2}
    layout_network_state = {"state": 0, "pool_proxy": None, "max_states": 2}
    
    s_id = session["sessionId"]
    logger.info(f"    -> [THREAD {thread_id}] 🚀 Started managing {len(assigned_seats)} seats.")
    
    # --- PHASE A: ONE-TIME LAYOUT FETCH ---
    # We fetch layout EXACTLY ONCE just to get the backend mapping (cat_code, area_id, backend_seat).
    seat_metadata_cache = {}
    categories_map_cache = {}
    
    logger.info(f"    -> [THREAD {thread_id}] Fetching initial layout for metadata mapping...")
    while (time.time() - start_time) < MAX_RUNTIME_SECONDS:
        str_data = fetch_seat_layout(s_id, layout_network_state)
        if str_data:
            _, categories_map_cache, full_seat_metadata, _ = parse_layout(str_data)
            
            for row, seat in assigned_seats:
                meta_key = f"{row}_{seat}"
                if meta_key in full_seat_metadata:
                    seat_metadata_cache[meta_key] = full_seat_metadata[meta_key]
                else:
                    logger.warning(f"       [!] [THREAD {thread_id}] {row}_{seat} physically doesn't exist in layout!")
                    
            break # 🛑 BREAK FOREVER. No more layout polling.
        time.sleep(2)

    # Initialize State Tracker
    seat_tracker = {}
    for row, seat in assigned_seats:
        if f"{row}_{seat}" not in seat_metadata_cache:
            continue
        seat_tracker[(row, seat)] = {
            "status": "READY", # States: READY, COOLDOWN, DEAD
            "failures": 0,
            "cooldown_until": 0
        }

    # --- PHASE B: INFINITE BLIND SNIPE LOOP ---
    while (time.time() - start_time) < MAX_RUNTIME_SECONDS:
        current_time = time.time()
        active_seats = 0
        
        for (row, seat), data in seat_tracker.items():
            if data["status"] == "DEAD":
                continue
                
            active_seats += 1
            
            # Check if Cooldown is finished
            if data["status"] == "COOLDOWN" and current_time >= data["cooldown_until"]:
                logger.info(f"    -> [THREAD {thread_id}] ⏱️ Cooldown finished for {row}_{seat}. Attempting blind re-snipe.")
                data["status"] = "READY"
                data["failures"] = 0 # Reset failures for this new attempt
                
            # Attempt Lock (Blind Fire)
            if data["status"] == "READY":
                meta = seat_metadata_cache[f"{row}_{seat}"]
                
                # execute_snipe calls lock_seat directly. NO layout API is hit.
                success = execute_snipe(session, row, seat, meta, categories_map_cache, snipe_network_state)
                
                if success:
                    logger.info(f"✅ [THREAD {thread_id}] Successfully locked: {s_id}_{row}_{seat}")
                    data["status"] = "COOLDOWN"
                    data["cooldown_until"] = time.time() + 790 # 13 mins 10 secs
                    data["failures"] = 0
                else:
                    data["failures"] += 1
                    logger.info(f"       [!] [THREAD {thread_id}] {row}_{seat} lock failed (Attempt {data['failures']}/7).")
                    
                    if data["failures"] >= 7:
                        logger.warning(f"       🚫 [THREAD {thread_id}] Max retries (7) reached for {row}_{seat}. Marking DEAD.")
                        data["status"] = "DEAD"

        # Check if thread is completely done
        if active_seats == 0:
            logger.info(f"    -> [THREAD {thread_id}] 🏁 All assigned seats are DEAD. Thread terminating.")
            break
            
        # Global pacing: Sleep 1 second.
        # This paces retries to 1 attempt per second per seat.
        # Also handles idle waiting when all seats are on 13m 10s cooldown.
        time.sleep(1)

# =======================================================
# PHASE 3 (cont): AUTO-LOCK / PAYMENT SNIPER 
# =======================================================
def lock_seat(session_id, row_index, backend_seat, cat_code, area_id, ticket_category, event_code, network_state):
    logger.info(f"    -> 🔒 [SNIPER] Request 1: Attempting to lock internal Row {row_index} Seat {backend_seat} ({cat_code})...")
    url = "https://in.bookmyshow.com/api/v2/mobile/booking/movies"
    
    # Generate dynamic parameters
    dynamic_email = "".join(random.choices("0123456789abcdef", k=9)) + "@gmail.com"
    dynamic_phone = "9" + "".join(random.choices("0123456789", k=9))
    dynamic_bms_id = f"1.4{random.randint(1000000, 9999999)}.{random.randint(1000000000000, 9999999999999)}"
    dynamic_version = f"1{random.randint(10000, 99999)}"
    dynamic_ua = random.choice(user_agents)

    payload = {
        "appCode": "MOBAND2",
        "venueCode": VENUE_CODE,
        "sessionId": str(session_id),
        "ticketCategory": ticket_category,
        "numberOfTickets": "1",
        "selectedSeats": f"|1|{cat_code}|{area_id}|{row_index}|{backend_seat}|",
        "email": dynamic_email,
        "eventCode": event_code,
        "version": dynamic_version,
        "platform": "ANDROID",
        "phone": dynamic_phone,
        "bmsId": dynamic_bms_id,
        "seatLayoutType": "Y",
        "offerData": {}
    }

    headers = {
        "Host": "in.bookmyshow.com",
        "X-Latitude": "17.385044",
        "X-Subregion-Code": "HYD",
        "X-App-Code": "MOBAND2",
        "X-Phone": dynamic_phone,
        "User-Agent": dynamic_ua,
        "X-Longitude": "78.48667",
        "X-Platform": "AND",
        "X-Region-Code": "HYD",
        "X-Platform-Code": "ANDROID",
        "X-Email": dynamic_email,
        "Content-Type": "text/plain; charset=utf-8",
        "Accept-Encoding": "gzip, deflate, br"
    }

    data_str = json.dumps(payload, separators=(',', ':'))
    resp = make_bms_request('POST', url, network_state=network_state, headers=headers, data=data_str.encode('utf-8'))
    logger.info(f"{resp.status_code}")
    if resp and resp.status_code == 200:
        try:
            r_json = resp.json()
            t_id = r_json.get("transactionId")
            t_uid = r_json.get("transactionUID")
            if t_id and t_uid:
                logger.info(f"    -> 🟢 [SNIPER] SUCCESS! Seat locked! TransID: {t_id}")
                return t_id, t_uid
        except Exception as e:
            logger.error(f"    -> ❌ [SNIPER] Error parsing Request 1: {e}")
    
    logger.error("    -> 🔴 [SNIPER] FAILED to lock seat.")
    return None, None


def execute_snipe(session, row, seat_num, meta, categories, network_state):
    cat_info = categories.get(meta["block_code"])
    if not cat_info: return False
    
    c_code, a_id = cat_info["cat_code"], cat_info["area_id"]
    
    ticket_category = "4D"
    
    logger.info(f"    -> 🎯 [SNIPER] MATCH FOUND! Auto-locking Row {row}, Seat {seat_num} (Internal Cat: {c_code}, Area: {a_id})")
    
    # 1. Lock (The status code is already logged inside lock_seat)
    t_id, t_uid = lock_seat(session["sessionId"], meta["row_index"], meta["backend_seat"], c_code, a_id, ticket_category, session["eventCode"], network_state)
    
    if t_id:
        return True
    return False

# =======================================================
# MAIN LOOP STATE MACHINE
# =======================================================
def gha_sigterm_handler(signum, frame):
    logger.warning("\n⚠️ SIGTERM received (GitHub Actions cancellation). Forcing early shutdown...")
    sys.exit(0) # Triggers the 'finally' block

def main():
    start_time = time.time()
    signal.signal(signal.SIGTERM, gha_sigterm_handler)
    
    logger.info("==================================================")
    logger.info("🚀 STARTING TARGETED SEAT SNIPER (MULTI-THREADED)")
    logger.info("==================================================\n")


    # --- PHASE 1: Wait for Showtimes to list ---
    target_sessions = []
    logger.info("    -> [PHASE 1] Scanning Venue API for target showtimes...")
    
    while (time.time() - start_time) < MAX_RUNTIME_SECONDS:
        target_sessions = find_target_session()
        
        if not target_sessions:
            logger.info("    -> ⏳ No matching showtimes exist yet. Sleeping 23 seconds...")
            time.sleep(5)
        else:
            logger.info(f"\n    -> 🎉 MATCH FOUND! Detected {len(target_sessions)} matching shows.")
            break

    if not target_sessions:
        logger.info("🏁 Max runtime reached before shows were listed. Shutting down.")
        return

        
    # --- PHASE 2 & 3: Parallel Threading with Chunking ---
    num_threads = 10
    # Calculate chunk size (ceiling division)
    chunk_size = (len(PRIORITY_SEATS) + num_threads - 1) // num_threads 
    chunks = [PRIORITY_SEATS[i:i + chunk_size] for i in range(0, len(PRIORITY_SEATS), chunk_size)]
    
    logger.info(f"    -> 🚀 Spawning {len(chunks)} parallel threads, distributing {len(PRIORITY_SEATS)} seats...")
    
    threads = []
    # target_sessions[0] is used because we only found 1 matching showtime
    session = target_sessions[0] 
    
    for i, chunk in enumerate(chunks):
        if not chunk: continue # Skip if chunk is empty
        t = threading.Thread(
            target=monitor_and_snipe_worker, 
            args=(session, chunk, i + 1, start_time) # i+1 is the thread_id
        )
        t.daemon = True
        t.start()
        threads.append(t)

    # Block main thread, waiting for all threads to finish
    for t in threads:
        while t.is_alive():
            t.join(1) 
            
    logger.info("🏁 Script shutting down gracefully.")
        
if __name__ == "__main__":
    main()
