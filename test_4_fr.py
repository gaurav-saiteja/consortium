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
import queue
# --- LOGGING CONFIGURATION ---
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s"
)
logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
DATES = ["20260824"]
VENUE_CODE = "PRHN"
STATE_FILE = "sniped_state_125.json"
MAX_RUNTIME_SECONDS = (5 * 3600) + (55 * 60) # 5 hours 55 mins
TICKET_CATEGORY_3D = "0009"
TICKET_CATEGORY_2D = "0005"
# --- NEW: SHOWTIME CONSTRAINTS ---
TARGET_ATTRIBUTE = "PCX SCREEN"
TARGET_SHOW_INDEX = 2

task_queue = queue.Queue()
grabroom_queue = queue.Queue()
GRABROOM_WEBHOOK_URL = os.environ.get("GRABROOM_WEBHOOK_URL", "https://your-cloudflare-worker.workers.dev/api/ticket")
in_flight_seats = set()
state_lock = threading.Lock()
thread_mgmt_lock = threading.Lock()
free_threads = 10
seat_counts_memory = {}

SUPABASE_URL = "https://edqxafyqqkhxuipzcjcd.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVkcXhhZnlxcWtoeHVpcHpjamNkIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODcyOTYzNzIsImV4cCI6MjEwMjg3MjM3Mn0.dAerrc1sTh8CSnY6vZ4NtuvPXWNwjsHCl9gWZ436MLk"

# --- AUTO-LOCK / SNIPER SECRETS & CONFIG ---
EMAIL = os.environ.get("BMS_EMAIL") 
PHONE = os.environ.get("BMS_PHONE")
TOPIC = os.environ.get("NTFY_TOPIC")

DESIRED_SEATS = {
    "N": ["47", "46", "45"],
    "M": ["47", "46", "45", "23", "24"],
    "L": ["47", "46", "45"],
    "K": ["47", "46", "45"],
    "J": ["47", "46", "45"],
    "I": ["47", "46", "45"],
    "G": ["47", "46", "45"],
    "H": ["47", "46", "45"],
    "F": ["47", "46", "45"]
}

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
    # --- Original 15 User Agents ---
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

def quiet_git_pull():
    logger.debug("[GIT] Executing Git pull...")
    subprocess.run(["git", "fetch", "origin", "main"], capture_output=True, check=False)
    subprocess.run(["git", "reset", "--hard", "origin/main"], capture_output=True, check=False)

def quiet_git_push():
    logger.debug("[GIT] Executing Git push...")
    res = subprocess.run(["git", "push", "origin", "main"], capture_output=True, text=True, check=False)
    return res.returncode == 0

def load_state():
    logger.info("[GIT] Loading initial state from repository...")
    quiet_git_pull()
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f: 
                return dict(json.load(f)) # Parse as Dictionary
        except json.JSONDecodeError: 
            logger.warning("Failed to decode local state JSON. Returning empty dictionary.")
    return {}

state_commit_queue = queue.Queue()

def git_committer_worker_loop():
    while True:
        try:
            state_dict_snap = state_commit_queue.get()
            if state_dict_snap is None: break
            save_state(state_dict_snap)
            state_commit_queue.task_done()
        except Exception as e:
            logger.error(f"[GIT-THREAD] Error committing state: {e}")

def save_state(state_dict):
    logger.info("\n[GIT] State mutated. Saving sniped seat state to Git...")
    for attempt in range(3):
        quiet_git_pull()
        with open(STATE_FILE, "w") as f:
            json.dump(state_dict, f, indent=2)
            
        subprocess.run(["git", "add", STATE_FILE], capture_output=True, check=False)
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        
        if STATE_FILE in status.stdout:
            subprocess.run(["git", "commit", "-m", "Update sniped seats state"], capture_output=True, check=False)
            if quiet_git_push(): 
                logger.info("[STATE] ✅ State successfully saved and pushed to remote.")
                return
            logger.warning(f"Git push failed on attempt {attempt+1}/3. Retrying...")
            time.sleep(2) # Wait 2 seconds before retrying the Git push
        else:
            return
    logger.error("❌ Failed to push state updates after 3 attempts.")

def trigger_ntfy(message, attach_url=None):
    logger.info(f"🔔 ALERTING VIA NTFY:\n{message}")
    headers = {"Priority": "urgent"}
    if attach_url:
        headers["Attach"] = attach_url
        
    for i in range(1):
        try:
            resp = requests.post(f"https://ntfy.sh/{TOPIC}", data=message.encode('utf-8'), headers=headers, timeout=10)
            if resp.status_code == 200:
                logger.info(f"✅ Ntfy ping sent! Status: {resp.status_code}")
        except Exception as e:
            logger.error(f"❌ Ntfy ping failed: {e}")

def start_warp():
    logger.info("    -> 🛡️ Connecting Cloudflare WARP (Switching to Proxy)...")
    subprocess.run(["warp-cli", "--accept-tos", "connect"], capture_output=True, check=False)
    time.sleep(5) # Wait 5 seconds for WARP proxy connection to fully establish

def make_bms_request(method, url, network_state, max_retries=5, **kwargs):
    for attempt in range(1, max_retries + 1):
        current_proxies = None
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
            else:
                resp = cffi_requests.post(url, proxies=current_proxies, impersonate="chrome", timeout=15, **kwargs)
            
            if resp.status_code in [429, 403]:
                logger.warning(f"    -> 🚧 WAF Block (HTTP {resp.status_code}) on {method}. Thread jumping network state. (Attempt {attempt}/{max_retries})")
                if attempt < max_retries:
                    max_states = network_state.get("max_states", 3)
                    network_state["state"] = (network_state["state"] + 1) % max_states
                    if network_state["state"] == 2:
                        network_state["pool_proxy"] = None
                    time.sleep(1) # 1 second short delay before jumping to the next proxy network state
                    continue
            return resp
        except Exception as e:
            logger.error(f"🌐 Request error on {method} (State {network_state['state']}, Attempt {attempt}/{max_retries}): {e}")
            if attempt < max_retries: 
                time.sleep(3) # 3 second cooldown before retrying the failed network request
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
                    event_title = event.get("EventTitle", "Unknown Title") # <-- NEW
                    
                    for child in event.get("ChildEvents", []):
                        current_event_code = child.get("EventCode", parent_code)
                        current_event_dimension = child.get("EventDimension", "")
                        event_language = child.get("EventLanguage", "Unknown Language") # <-- NEW
                        
                        for show in child.get("ShowTimes", []):
                            s_date_code = show.get("ShowDateCode", "")
                            if s_date_code != date_code:
                                if not fallback_logged:
                                    logger.info(f"    -> ⚠️ API fallback detected! Requested {date_code} but received {s_date_code}. Ignoring fallback shows.")
                                    fallback_logged = True
                                continue
                            
                            s_attr = show.get("Attributes", "")
                            
                            # 1. Constraint Check: Screen Attribute Only
                            if TARGET_ATTRIBUTE.lower() not in s_attr.lower():
                                continue
                                
                            s_time_str = show.get("ShowTime", "")
                            try:
                                s_time_obj = datetime.strptime(s_time_str, "%I:%M %p").time()
                            except:
                                continue
                            
                            valid_shows.append({
                                "sessionId": show.get("SessionId"),
                                "eventCode": current_event_code,
                                "eventTitle": event_title,               # <-- NEW
                                "eventLanguage": event_language,         # <-- NEW
                                "eventDimension": current_event_dimension,
                                "dateCode": show.get("ShowDateCode"),
                                "time": s_time_str,
                                "attribute": s_attr,
                                "datetime_obj": s_time_obj,
                                "screen": show.get("ScreenName", "Unknown")
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
TEMP_HEADERS = {
    "Host": "services-in.bookmyshow.com",
    "X-Latitude": "17.385044",
    "X-Subregion-Code": "HYD",
    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 10; Android SDK built for x86_64 Build/QSR1.211112.011)",
    "X-Longitude": "78.48667",
    "X-Region-Code": "HYD",
    "X-Platform-Code": "ANDROID",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

def fetch_seat_layout(session_id, network_state):
    url = "https://services-in.bookmyshow.com/doTrans.aspx"
    payload = f"strParam4=&strParam5=Y&strParam6=&strParam7=N&strParam1={session_id}&strParam2=WEB&strParam3=&strVenueCode={VENUE_CODE}&lngTransactionIdentifier=0&strAppCode=MOBAND2&strFormat=json&strCommand=GETSEATLAYOUT"
    #resp = make_bms_request('POST', url, network_state=network_state, headers=generate_headers(is_post=True), data=payload)
    resp = make_bms_request('POST', url, network_state=network_state, headers=TEMP_HEADERS, data=payload)
    
    status_code = resp.status_code if resp else None
    if resp:
        logger.info(f"{resp.status_code}")
        
    if not resp or resp.status_code != 200: 
        return "", status_code
    try: 
        return resp.json().get("BookMyShow", {}).get("strData", ""), status_code
    except Exception: 
        return "", status_code

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
            match = re.search(r"([A-Z])[14](\d+)\+(\d+)", seat)
            if match:
                block_code = match.group(1)
                backend_seat = match.group(2)
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

def persistent_worker():
    global free_threads
    # Base network state is 0. Independent for every worker.
    network_state = {"state": 0, "pool_proxy": None, "max_states": 3}
    last_active = time.time()
    
    while True:
        task = task_queue.get()
        if task is None: break
        session, row, seat_num, meta, categories = task
        seat_key = f"{session['sessionId']}_{row}_{seat_num}"
        
        with thread_mgmt_lock: 
            free_threads -= 1
        
        # Idle check: Reset network state to 0 if idle for > 10 seconds
        if time.time() - last_active > 10:
            network_state["state"] = 0
            network_state["pool_proxy"] = None
            
        success = False
        # Maximum of 7 retries for locking and intent generation
        for attempt in range(1, 8):
            logger.info(f"    -> [WORKER] Attempt {attempt}/7 locking {row}{seat_num}")
            if execute_snipe(session, row, seat_num, meta, categories, network_state):
                success = True
                break
            time.sleep(1.5) # 2 second cooldown before retrying the lock sequence for this seat
            
        with state_lock:
            if success:
                # Increment the dictionary count. If 2, remove it entirely.
                current_count = seat_counts_memory.get(seat_key, 0) + 1
                if current_count >= 2:
                    if seat_key in seat_counts_memory:
                        del seat_counts_memory[seat_key]
                else:
                    seat_counts_memory[seat_key] = current_count
                
                # Push a dict copy to the Git Daemon Queue
                state_commit_queue.put(dict(seat_counts_memory))
            
            # Unmark from In-Flight Tracker
            if seat_key in in_flight_seats:
                in_flight_seats.remove(seat_key)
        
        last_active = time.time()
        with thread_mgmt_lock: 
            free_threads += 1
        task_queue.task_done()

def layout_poller(session, start_time):
    # Dedicated poller with independent network state
    poller_network = {"state": 0, "pool_proxy": None, "max_states": 2}
    s_id = session["sessionId"]
    logger.info(f"    -> [POLLER] 🚀 Dedicated poller active for Session {s_id}")
    
    while (time.time() - start_time) < MAX_RUNTIME_SECONDS:
        with state_lock:
            # Terminate if state dictionary is entirely empty (all seats sniped 2x)
            if not seat_counts_memory:
                logger.info("    -> [POLLER] All desired seats reached 2 snipes! Shutting down gracefully.")
                os.kill(os.getpid(), signal.SIGTERM)
                break
                
        str_data, status_code = fetch_seat_layout(s_id, poller_network)
        if not str_data:
            if status_code in [403, 429]:
                logger.warning(f"    -> 🚧 [POLLER] Persistent WAF block (HTTP {status_code}). Nuking poisoned thread...")
                return  # Commit thread suicide
            else:
                time.sleep(2) # Short 2 second delay before retrying a failed layout fetch
            continue
            
        current_seats, categories_map, seat_metadata, total_avail = parse_layout(str_data)
        logger.info(f"    -> [POLL] Session {s_id} | Total available seats: {total_avail}")
        
        with state_lock:
            for target_row, target_seat_list in DESIRED_SEATS.items():
                if target_row not in current_seats: continue
                avail_in_row = current_seats[target_row]
                
                for target_seat in target_seat_list:
                    seat_key = f"{s_id}_{target_row}_{target_seat}"
                    
                    # Only assign if it's in memory (count < 2) AND not currently being processed
                    if seat_key in seat_counts_memory and seat_key not in in_flight_seats:
                        if target_seat in avail_in_row:
                            in_flight_seats.add(seat_key)
                            meta = seat_metadata.get(f"{target_row}_{target_seat}")
                            task = (session, target_row, target_seat, meta, categories_map)
                            
                            # Dynamic Worker Dispatcher Logic
                            with thread_mgmt_lock:
                                global free_threads
                                if free_threads <= 0:
                                    t = threading.Thread(target=persistent_worker, daemon=True)
                                    t.start()
                                    free_threads += 1
                                    logger.info("    -> [DISPATCHER] Threads busy! Spawned new Worker Thread.")
                                    
                            task_queue.put(task)
                            
        time.sleep(20) # 20 second hard cooldown between layout polls

# =======================================================
# PHASE 3 (cont): AUTO-LOCK / PAYMENT SNIPER 
# =======================================================
def lock_seat(session_id, row_index, backend_seat, cat_code, area_id, ticket_category, event_code, network_state):
    logger.info(f"    -> 🔒 [SNIPER] Request 1: Attempting to lock internal Row {row_index} Seat {backend_seat} ({cat_code})...")
    url = "https://in.bookmyshow.com/api/v2/mobile/booking/movies"
    
    payload = {
        "appCode": "MOBAND2",
        "venueCode": VENUE_CODE,
        "sessionId": str(session_id),
        "ticketCategory": ticket_category,
        "numberOfTickets": "1",
        "selectedSeats": f"|1|{cat_code}|{area_id}|{row_index}|{backend_seat}|",
        "email": EMAIL,
        "eventCode": event_code,
        "version": "18234",
        "platform": "ANDROID",
        "phone": PHONE,
        "bmsId": "1.42419972.1785913202920",
        "seatLayoutType": "Y",
        "offerData": {}
    }

    headers = {
        "Host": "in.bookmyshow.com",
        "X-Latitude": "17.385044",
        "X-Subregion-Code": "HYD",
        "X-App-Code": "MOBAND2",
        "X-Phone": PHONE,
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 10; Android SDK built for x86_64 Build/QSR1.211112.011)",
        "X-Longitude": "78.48667",
        "X-Platform": "AND",
        "X-Region-Code": "HYD",
        "X-Platform-Code": "ANDROID",
        "X-Email": EMAIL,
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
            b_id = r_json.get("bookingId") # <-- NEW
            if t_id and t_uid:
                logger.info(f"    -> 🟢 [SNIPER] SUCCESS! Seat locked! TransID: {t_id}")
                return t_id, t_uid, b_id   # <-- MODIFIED
        except Exception as e:
            logger.error(f"    -> ❌ [SNIPER] Error parsing Request 1: {e}")
    
    logger.error("    -> 🔴 [SNIPER] FAILED to lock seat.")
    return None, None, None # <-- MODIFIED

def initiate_payment(trans_id, trans_uid, network_state):
    logger.info(f"    -> 💸 [SNIPER] Request 2: Initiating payment intent for {trans_id}...")
    url = "https://services-in.bookmyshow.com/doTrans.aspx"
    
    rand_hex = "".join(random.choices("0123456789abcdef", k=7))
    boundary = f"----geckoformboundary4549d0c459b45033a86405c7a{rand_hex}"

    headers = {
        "Host": "services-in.bookmyshow.com",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:154.0) Gecko/20100101 Firefox/154.0",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Referer": "https://in.bookmyshow.com/",
        "X-Region-Code": "HYD",
        "X-Region-Slug": "hyderabad",
        "X-Latitude": "17.385044",
        "X-Longitude": "78.486671",
        "X-Deemed-Email": EMAIL,
        "X-Deemed-Mobile": PHONE,
        "X-Phone": PHONE,
        "X-Mobile": PHONE,
        "X-Email": EMAIL,
        "X-Transaction-Uid": trans_uid,
        "X-App-Code": "WEB",
        "X-Platform-Code": "WEB",
        "X-Platform": "WEB",
        "Origin": "https://in.bookmyshow.com",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site"
    }

    form_fields = [
        f"--{boundary}", 'Content-Disposition: form-data; name="strAppCode"', '', 'WEB',
        f"--{boundary}", 'Content-Disposition: form-data; name="lngTransactionIdentifier"', '', trans_id,
        f"--{boundary}", 'Content-Disposition: form-data; name="strCommand"', '', 'SETPAYMENT',
        f"--{boundary}", 'Content-Disposition: form-data; name="strVenueCode"', '', VENUE_CODE,
        f"--{boundary}", 'Content-Disposition: form-data; name="strParam1"', '', "'|TYPE=UPI|UPITYPE=QRCODE|IMAGEURL=''|PROCESSTYPE=REQUEST|LSID=|MEMBERID=|CLIENTID=movies|",
        f"--{boundary}", 'Content-Disposition: form-data; name="strParam2"', '', '|ETICKET=Y|MTICKET=Y|',
        f"--{boundary}", 'Content-Disposition: form-data; name="strParam3"', '', EMAIL,
        f"--{boundary}", 'Content-Disposition: form-data; name="strParam4"', '', PHONE,
        f"--{boundary}", 'Content-Disposition: form-data; name="strFormat"', '', 'json',
        f"--{boundary}--", ''
    ]
    
    payload_str = '\r\n'.join(form_fields)
    resp = make_bms_request('POST', url, network_state=network_state, headers=headers, data=payload_str.encode('utf-8'))
    logger.info(f"{resp.status_code}")
    if resp and resp.status_code == 200:
        try:
            data = resp.json()
            bms = data.get("BookMyShow", {})
            if bms.get("blnSuccess") == "true":
                str_data = bms.get("strData", [])
                if len(str_data) > 0:
                    upi_url = str_data[0].get("BMSUPIQRPAYURL")
                    logger.info(f"    -> 🟢 [SNIPER] SUCCESS! Payment Intent Generated!")
                    return upi_url
        except Exception as e:
            logger.error(f"    -> ❌ [SNIPER] Error parsing Request 2: {e}")
            
    logger.error("    -> 🔴 [SNIPER] FAILED to generate payment intent.")
    return None

def execute_snipe(session, row, seat_num, meta, categories, network_state):
    cat_info = categories.get(meta["block_code"])
    if not cat_info: return False
    
    c_code, a_id = cat_info["cat_code"], cat_info["area_id"]
    
    if "3D" in session.get("eventDimension", "").upper():
        ticket_category = "0009"
    else:
        ticket_category = "0006"
    
    logger.info(f"    -> 🎯 [SNIPER] MATCH FOUND! Auto-locking Row {row}, Seat {seat_num} (Internal Cat: {c_code}, Area: {a_id})")
    
    # 1. Lock (Note the added b_id unpack)
    t_id, t_uid, b_id = lock_seat(session["sessionId"], meta["row_index"], meta["backend_seat"], c_code, a_id, ticket_category, session["eventCode"], network_state)
    if not t_id: return False
    
    # 2. Pay
    upi_intent = initiate_payment(t_id, t_uid, network_state)
    if not upi_intent: return False
    
    # 3. Format Notification & Trigger Push
    qr_image_url = f"https://in.bookmyshow.com/secure/barcode/?IsImage=Y&strBarcodeType=qrcode&strBarcodeTxt={upi_intent}&intHeight=300&intWidth=300"
    hum_date = humanize_date(session["dateCode"])
    msg = f"{row}{seat_num} is locked. {hum_date} {session['time']} {session['attribute']} {VENUE_CODE} {session['eventCode']}"
    
    threading.Thread(target=trigger_ntfy, args=(msg, qr_image_url), daemon=True).start()
    # --- NEW: GRABROOM PRODUCER LOGIC ---
    ticket_payload = {
        "seat": f"{row}{seat_num}",
        "booking_id": b_id,                   # Changed key
        "transaction_id": t_id,               # Changed key
        "event_title": session.get("eventTitle"),
        "event_language": session.get("eventLanguage"),
        "event_dimension": session.get("eventDimension"),
        "show_date_code": session.get("dateCode"),
        "show_time": session.get("time"),
        "screen_name": session.get("screen"),
        "attributes": session.get("attribute"),
        "qr_image_url": qr_image_url,         # Changed key
        "snipe_timestamp": int(time.time() * 1000)
    }
    grabroom_queue.put(ticket_payload)
    # ------------------------------------
    return True

def grabroom_producer_loop():
    logger.info("[GRABROOM] 📡 Supabase Delivery thread started.")
    
    # Supabase REST API Endpoint for the 'tickets' table
    endpoint = f"{SUPABASE_URL}/rest/v1/tickets"
    
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation, resolution=merge-duplicates"
    }

    while True:
        try:
            payload = grabroom_queue.get()
            if payload is None: break
            
            # Guaranteed delivery retry loop
            while True:
                try:
                    resp = requests.post(
                        endpoint, 
                        json=payload, 
                        headers=headers,
                        timeout=10
                    )
                    
                    if resp.status_code in [200, 201]:
                        logger.info(f"[GRABROOM] 🟢 Successfully saved ticket {payload['seat']} to Supabase!")
                        break # Break retry loop, move to next queue item
                    elif resp.status_code == 409:
                        # 409 means Conflict (Primary Key already exists). 
                        # In case a seat is sniped again later, we can optionally handle an update here,
                        # but for now, we just break so it doesn't get stuck in an infinite loop.
                        logger.warning(f"[GRABROOM] ⚠️ Seat {payload['seat']} already exists in DB. Skipping.")
                        break
                    else:
                        logger.warning(f"[GRABROOM] ⚠️ Supabase returned {resp.status_code}: {resp.text}. Retrying in 2s...")
                except Exception as e:
                    logger.error(f"[GRABROOM] ❌ Network error sending to Supabase: {e}. Retrying in 2s...")
                
                time.sleep(2) # Cooldown before retry
                
            grabroom_queue.task_done()
        except Exception as e:
            logger.error(f"[GRABROOM] Thread error: {e}")

# =======================================================
# MAIN LOOP STATE MACHINE
# =======================================================
def gha_sigterm_handler(signum, frame):
    logger.warning("\n⚠️ SIGTERM received (GitHub Actions cancellation). Forcing early shutdown...")
    sys.exit(0) # Triggers the 'finally' block

def main():
    start_time = time.time()
    signal.signal(signal.SIGTERM, gha_sigterm_handler)

    # --- START GRABROOM PRODUCER THREAD ---
    grabroom_thread = threading.Thread(target=grabroom_producer_loop, daemon=True)
    grabroom_thread.start()
    
    logger.info("==================================================")
    logger.info("🚀 STARTING TARGETED SEAT SNIPER (MULTI-THREADED)")
    logger.info("==================================================\n")

    logger.info("[GIT] Loading initial state from GitHub repository...")
    global seat_counts_memory
    seat_counts_memory = load_state()
    logger.info(f"[STATE] Loaded tracking data for {len(seat_counts_memory)} seats.\n")

    # --- START GIT COMMITTER THREAD ---
    git_thread = threading.Thread(target=git_committer_worker_loop, daemon=True)
    git_thread.start()

    # --- PHASE 1: Wait for Showtimes to list ---
    target_sessions = []
    logger.info("    -> [PHASE 1] Scanning Venue API for target showtimes...")
    
    while (time.time() - start_time) < MAX_RUNTIME_SECONDS:
        target_sessions = find_target_session()
        
        if not target_sessions:
            time.sleep(8) # 5 second polling interval to check if target showtimes are listed
        else:
            logger.info(f"\n    -> 🎉 MATCH FOUND! Detected {len(target_sessions)} matching shows.")
            break

    if not target_sessions:
        logger.info("🏁 Max runtime reached before shows were listed. Shutting down.")
        return

    # --- INITIALIZE DICTIONARY STATE ---
    # Append any un-tracked desired seats into the dictionary with a value of 0.
    state_changed = False
    for session in target_sessions:
        s_id = session["sessionId"]
        for target_row, target_seat_list in DESIRED_SEATS.items():
            for target_seat in target_seat_list:
                key = f"{s_id}_{target_row}_{target_seat}"
                if key not in seat_counts_memory:
                    seat_counts_memory[key] = 0
                    state_changed = True
                    
    if state_changed:
        logger.info("[STATE] New target seats added. Committing baseline state to Git...")
        save_state(seat_counts_memory)
        
    if not seat_counts_memory:
        logger.info("🏁 State dict is empty (all target seats successfully sniped twice). Exiting.")
        return

    # --- PROXY WARMUP ---
    logger.info("    -> 🛡️ Pre-warming WARP Proxy before spawning threads...")
    start_warp()
        
    # --- PHASE 2 & 3: Parallel Dispatcher-Worker Engine ---
    logger.info("    -> 🚀 Spawning 10 Idle Workers and 1 Dedicated Layout Poller...")
    
    # Spawn the 10 Idle Workers
    global free_threads
    free_threads = 10
    worker_threads = []
    for _ in range(10):
        wt = threading.Thread(target=persistent_worker, daemon=True)
        wt.start()
        worker_threads.append(wt)

    # Spawn Poller (For simplicity targeting the first session, matching older logic)
    main_session = target_sessions[0]
    poller_thread = threading.Thread(
        target=layout_poller, 
        args=(main_session, start_time)
    )
    poller_thread.daemon = True
    poller_thread.start()

    # Block main thread, waiting until the poller finishes (either via Timeout or SIGTERM)
    try:
       while (time.time() - start_time) < MAX_RUNTIME_SECONDS:
            if poller_thread.is_alive():
                poller_thread.join(1) # Block the main thread, check thread status every 1 second
            else:
                # Thread died (returned due to WAF block)
                logger.info("    -> ⏳ [MAIN] Poller thread dead. Initiating 320-second cooldown in main thread...")
                time.sleep(320)
                
                logger.info("    -> 🚀 [MAIN] Cooldown complete. Respawning fresh layout poller thread...")
                poller_thread = threading.Thread(
                    target=layout_poller, 
                    args=(main_session, start_time)
                )
                poller_thread.daemon = True
                poller_thread.start()
    finally:
        # --- PHASE 4: Deferred Cleanup & Git Commit ---
        logger.info("\n🏁 Flushing queues. Waiting for Grabroom delivery to complete...")
        
        # Wait up to 10 seconds for any pending Grabroom tickets to be delivered
        # before the script gets killed completely.
        def _flush_queue():
            grabroom_queue.join()
        
        flush_thread = threading.Thread(target=_flush_queue)
        flush_thread.start()
        flush_thread.join(timeout=10.0) 

        logger.info("🏁 Executing deferred state commit to Git...")
        save_state(seat_counts_memory)
        logger.info("🏁 Script shutting down gracefully.")
        
if __name__ == "__main__":
    main()
