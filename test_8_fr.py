import requests
from curl_cffi import requests as cffi_requests
import time
import json
import os
import queue
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
DATES = ["20260824"]
VENUE_CODE = "PRHN"
STATE_FILE = "sniped_state_105.json"
MAX_RUNTIME_SECONDS = (5 * 3600) + (55 * 60) # 5 hours 55 mins
TICKET_CATEGORY_3D = "0009"
TICKET_CATEGORY_2D = "0005"
# --- NEW: SHOWTIME CONSTRAINTS ---
TARGET_ATTRIBUTE = "PCX SCREEN"
TARGET_TIME_START = "07:00 AM"
TARGET_TIME_END = "11:59 PM"

# --- NEW: CONTINUOUS LOCKING CONFIG ---
MAX_LOCK_ATTEMPTS = 5
COOLDOWN_SECONDS = 240

MAX_WAVES = 5
MAX_DYNAMIC_THREADS = 24
STATIC_THREADS = 6
IDLE_TIMEOUT = 30
MAX_SEAT_RETRIES = 7

# --- AUTO-LOCK / SNIPER SECRETS & CONFIG ---
EMAIL = os.environ.get("BMS_EMAIL") 
PHONE = os.environ.get("BMS_PHONE")
TOPIC = os.environ.get("NTFY_TOPIC")

DESIRED_SEATS = {
    "A": ["3","4","5", "6", "7", "8"],
    "N": ["23", "24"]
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

def notification_worker(notif_queue, start_time):
    logger.info("    -> [THREAD] 🔔 Notification sender background thread active.")
    while (time.time() - start_time) < MAX_RUNTIME_SECONDS:
        try:
            # Block for up to 2 seconds waiting for an item in the queue
            notif_data = notif_queue.get(timeout=2)
            trigger_ntfy(notif_data["msg"], attach_url=notif_data.get("attach_url"))
            notif_queue.task_done()
        except queue.Empty:
            continue

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
    if res.returncode != 0:
        logger.warning(f"[GIT] Push failed. Stderr: {res.stderr.strip()}")
    return res.returncode == 0

def load_state():
    logger.info("[GIT] Loading initial state from repository...")
    quiet_git_pull()
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                # Ensure it's the new format
                if "available_seats" in data:
                    return data
        except json.JSONDecodeError:
            logger.warning("Failed to decode local state JSON.")
    return {}

def save_state_async(state_dict):
    def _background_save():
        logger.info("\n[GIT] Background state save initiated...")
        for attempt in range(3):
            quiet_git_pull()
            with open(STATE_FILE, "w") as f:
                json.dump(state_dict, f, indent=2)
            
            subprocess.run(["git", "add", STATE_FILE], capture_output=True, check=False)
            status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
            if STATE_FILE in status.stdout:
                subprocess.run(["git", "commit", "-m", "Wave state update"], capture_output=True, check=False)
                if quiet_git_push():
                    logger.info("[STATE] ✅ State saved and pushed to remote.")
                    return
                time.sleep(2)
            else:
                return
    
    # Fire and forget thread
    threading.Thread(target=_background_save, daemon=True).start()

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
    time.sleep(5)

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
                    max_states = network_state.get("max_states", 3)
                    
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
    try:
        # Convert strings like "12:00 PM" into datetime.time objects for mathematical comparison
        target_time_start_obj = datetime.strptime(TARGET_TIME_START, "%I:%M %p").time()
        target_time_end_obj = datetime.strptime(TARGET_TIME_END, "%I:%M %p").time()
    except Exception as e:
        logger.error(f"❌ Time parsing error in config (TARGET_TIME_START/END). Error: {e}")
        return []

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
                    # Check both parent and child level for the matching Movie Code
                    parent_code = event.get("EventCode", "")
                    for child in event.get("ChildEvents", []):
                        # Extract the event code dynamically (prioritize child code, fallback to parent)
                        current_event_code = child.get("EventCode", parent_code)
                        current_event_dimension = child.get("EventDimension", "")
                        
                        for show in child.get("ShowTimes", []):
                            # --- NEW: Constraint Check 0: Strict Date Verification ---
                            s_date_code = show.get("ShowDateCode", "")
                            if s_date_code != date_code:
                                if not fallback_logged:
                                    logger.info(f"    -> ⚠️ API fallback detected! Requested {date_code} but received {s_date_code}. Ignoring fallback shows.")
                                    fallback_logged = True
                                continue
                            
                            s_attr = show.get("Attributes", "")
                            
                            # 1. Constraint Check: Screen Attribute
                            if TARGET_ATTRIBUTE.lower() not in s_attr.lower():
                                continue
                                
                            s_time_str = show.get("ShowTime", "")
                            try:
                                s_time_obj = datetime.strptime(s_time_str, "%I:%M %p").time()
                            except:
                                continue
                            
                            # 2. Constraint Check: Time Range
                            if target_time_start_obj <= s_time_obj <= target_time_end_obj:
                                valid_shows.append({
                                    "sessionId": show.get("SessionId"),
                                    "eventCode": current_event_code,
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
        valid_shows.sort(key=lambda x: x["datetime_obj"])
        return valid_shows[:6]  # <-- Returns up to 5 shows
        
    return []

def dynamic_sniper_worker(task_queue, notif_queue, wave_num):
    # Isolated network state per dynamic thread
    local_network = {"state": 0, "pool_proxy": None, "max_states": 3}
    
    while True:
        try:
            # Self-destruct if idle for 30 seconds
            task = task_queue.get(timeout=IDLE_TIMEOUT)
        except queue.Empty:
            logger.debug("    -> 💀 Dynamic thread idle for 30s. Self-destructing.")
            return

        s_id, meta, c_code, a_id, t_cat, e_code = task["session_id"], task["meta"], task["c_code"], task["a_id"], task["ticket_category"], task["event_code"]
        
        success = False
        for attempt in range(MAX_SEAT_RETRIES):
            t_id, t_uid = lock_seat(s_id, meta["row_index"], meta["backend_seat"], c_code, a_id, t_cat, e_code, local_network)
            if t_id:
                upi_intent = initiate_payment(t_id, t_uid, local_network)
                if upi_intent:
                    qr_url = f"https://in.bookmyshow.com/secure/barcode/?IsImage=Y&strBarcodeType=qrcode&strBarcodeTxt={upi_intent}&intHeight=300&intWidth=300"
                    msg = f"[WAVE {wave_num}] Row {task['row']} Seat {task['seat_num']} is locked.\n\n{VENUE_CODE} {e_code}"
                    notif_queue.put({"msg": msg, "attach_url": qr_url})
                    success = True
                    break
            time.sleep(1) # Delay between retries
            
        if not success:
            logger.warning(f"    -> ❌ Seat {task['row']}{task['seat_num']} exhausted {MAX_SEAT_RETRIES} retries. Declared dead for Wave {wave_num}.")

        task_queue.task_done()

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

def static_show_manager(session_id, my_seats, task_queue, notif_queue, state_dict, global_trigger, wave_barrier):
    # This thread stays alive for all remaining waves
    for wave_num in range(state_dict["completed_waves"] + 1, MAX_WAVES + 1):
        
        # 1. Cooldown Check
        time_passed = time.time() - state_dict["last_wave_finish_timestamp"]
        if state_dict["last_wave_finish_timestamp"] > 0 and time_passed < 870:
            sleep_time = 870 - time_passed
            logger.info(f"    -> 💤 Static Thread {session_id} waiting {int(sleep_time)}s for Wave {wave_num} cooldown...")
            time.sleep(sleep_time)

        # 2. Warmup Phase
        logger.info(f"    -> 🔥 Static Thread {session_id} entering Warmup for Wave {wave_num}...")
        warmup_network = {"state": 0, "pool_proxy": None, "max_states": 2}
        roster = list(my_seats.values())
        
        if roster:
            while not global_trigger.is_set():
                test_seat = random.choice(roster)
                t_id, t_uid = lock_seat(session_id, test_seat["meta"]["row_index"], test_seat["meta"]["backend_seat"], test_seat["c_code"], test_seat["a_id"], test_seat["ticket_category"], test_seat["event_code"], warmup_network)
                if t_id:
                    # SUCCESS! Fire the global trigger!
                    global_trigger.set()
                    upi = initiate_payment(t_id, t_uid, warmup_network)
                    if upi:
                        msg = f"[WAVE {wave_num} WARMUP] Row {test_seat['row']} Seat {test_seat['seat_num']} locked."
                        notif_queue.put({"msg": msg})
                    break
                time.sleep(3)
        
        # 3. Main Locking Triggered - Dump seats to queue
        # Wait until someone triggers the event (if this thread didn't)
        global_trigger.wait() 
        for seat in roster:
            task_queue.put(seat)
            
        # 4. Wait for all 6 static threads AND the main thread to reach this barrier before continuing to next wave
        wave_barrier.wait()

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
            if t_id and t_uid:
                logger.info(f"    -> 🟢 [SNIPER] SUCCESS! Seat locked! TransID: {t_id}")
                return t_id, t_uid
        except Exception as e:
            logger.error(f"    -> ❌ [SNIPER] Error parsing Request 1: {e}")
    
    logger.error("    -> 🔴 [SNIPER] FAILED to lock seat.")
    return None, None

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

    state_dict = load_state()
    notif_queue = queue.Queue()
    task_queue = queue.Queue()
    global_trigger = threading.Event()
    
    # --- Start Notification Worker ---
    notif_t = threading.Thread(target=notification_worker, args=(notif_queue, start_time))
    notif_t.daemon = True
    notif_t.start()

    # --- PHASE 1: State Setup / Initial Discovery ---
    if not state_dict:
        logger.info("    -> [PHASE 1] No state found. Scanning Venue API for target showtimes...")
        target_sessions = []
        
        while (time.time() - start_time) < MAX_RUNTIME_SECONDS:
            target_sessions = find_target_session()
            if not target_sessions:
                logger.info("    -> ⏳ No matching showtimes exist yet. Sleeping 8 seconds...")
                time.sleep(8)
            else:
                logger.info(f"\n    -> 🎉 MATCH FOUND! Detected {len(target_sessions)} matching shows.")
                break
                
        if not target_sessions:
            logger.info("🏁 Max runtime reached before shows were listed. Shutting down.")
            return

        logger.info("    -> [PHASE 1] Polling Seat Layouts for initial discovery...")
        available_seats = {}
        layout_network_state = {"state": 0, "pool_proxy": None, "max_states": 2}
        
        for session in target_sessions:
            s_id = session["sessionId"]
            str_data = ""
            
            # Fetch layout with wait loop
            while (time.time() - start_time) < MAX_RUNTIME_SECONDS:
                str_data = fetch_seat_layout(s_id, layout_network_state)
                if str_data: 
                    break
                time.sleep(2)
                
            current_seats, categories_map, seat_metadata, total_available = parse_layout(str_data)
            ticket_category = "0009" if "3D" in session.get("eventDimension", "").upper() else "0006"
            
            for target_row, target_seat_list in DESIRED_SEATS.items():
                if target_row not in current_seats: continue
                
                for target_seat in target_seat_list:
                    if target_seat in current_seats[target_row]:
                        seat_key = f"{s_id}_{target_row}_{target_seat}"
                        meta = seat_metadata.get(f"{target_row}_{target_seat}")
                        if not meta: continue
                        
                        cat_info = categories_map.get(meta["block_code"])
                        if not cat_info: continue
                        
                        available_seats[seat_key] = {
                            "session_id": s_id,
                            "row": target_row,
                            "seat_num": target_seat,
                            "meta": meta,
                            "c_code": cat_info["cat_code"],
                            "a_id": cat_info["area_id"],
                            "ticket_category": ticket_category,
                            "event_code": session["eventCode"]
                        }
        
        state_dict = {
            "completed_waves": 0,
            "last_wave_finish_timestamp": 0,
            "available_seats": available_seats
        }
        save_state_async(state_dict)
        logger.info(f"    -> [STATE] Initial state built with {len(available_seats)} available desired seats.")
        
    elif state_dict.get("completed_waves", 0) >= MAX_WAVES:
        logger.info("🏁 Previous runners completed all waves. Shutting down.")
        return
    else:
        logger.info(f"    -> 🔄 Resuming from state! Skipped API polling. Starting at Wave {state_dict['completed_waves'] + 1}")

    # --- Group Seats by Session ---
    seats_by_session = {}
    for key, seat_data in state_dict.get("available_seats", {}).items():
        s_id = seat_data["session_id"]
        if s_id not in seats_by_session:
            seats_by_session[s_id] = {}
        seats_by_session[s_id][key] = seat_data
        
    num_static_threads = len(seats_by_session)
    if num_static_threads == 0:
        logger.info("    -> 🛑 No desired seats were available to snipe. Shutting down.")
        return

    # Synchronize the main orchestrator thread with exact number of static threads
    wave_barrier = threading.Barrier(num_static_threads + 1)

    logger.info("    -> 🛡️ Pre-warming WARP Proxy before spawning threads...")
    start_warp()

    # --- PHASE 2: Spawn Static Producer Threads ---
    logger.info(f"    -> 🚀 Spawning {num_static_threads} static producer threads...")
    for s_id, seats in seats_by_session.items():
        t = threading.Thread(
            target=static_show_manager, 
            args=(s_id, seats, task_queue, notif_queue, state_dict, global_trigger, wave_barrier)
        )
        t.daemon = True
        t.start()

    # --- PHASE 3: Wave Orchestration (Main Thread) ---
    try:
        for wave in range(state_dict["completed_waves"] + 1, MAX_WAVES + 1):
            logger.info(f"\n================= [ WAVE {wave} ] =================")
            logger.info("    -> ⏳ Waiting for a static thread to trigger the main locking phase...")
            
            # 1. Wait for a static thread to get a 200 OK during Warmup
            global_trigger.wait()
            
            # 2. Trigger flipped! Spawn dynamic threads to consume the queue
            logger.info(f"    -> 🌊 WAVE {wave} TRIGGERED! Spawning {MAX_DYNAMIC_THREADS} dynamic threads...")
            for _ in range(MAX_DYNAMIC_THREADS):
                dt = threading.Thread(target=dynamic_sniper_worker, args=(task_queue, notif_queue, wave))
                dt.daemon = True
                dt.start()
                
            # 3. Wait for all tasks to be processed by the dynamic threads
            task_queue.join() 
            logger.info(f"    -> ✅ WAVE {wave} TASKS COMPLETED.")
            
            # 4. Save State asynchronously
            state_dict["completed_waves"] = wave
            state_dict["last_wave_finish_timestamp"] = time.time()
            save_state_async(state_dict)
            
            # 5. Reset global trigger for the next wave
            global_trigger.clear()
            
            # 6. Hit the barrier to sync all threads and move to next wave's cooldown together
            wave_barrier.wait()

        logger.info("\n🏁 All 5 Waves completed successfully. Shutting down cleanly.")
        
    finally:
        if not notif_queue.empty():
            logger.info("⏳ Waiting for remaining notifications to be sent...")
            notif_queue.join()
        
if __name__ == "__main__":
    main()
