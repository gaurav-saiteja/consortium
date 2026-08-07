import requests
from curl_cffi import requests as cffi_requests
import time
import json
import os
import re
import subprocess
import random
import logging
from datetime import datetime

# --- LOGGING CONFIGURATION ---
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s"
)
logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
DATES = ["20260813"]
VENUE_CODE = "PRHN"
EVENT_CODE = "ET00502600"
MAX_RUNTIME_SECONDS = (5 * 3600) + (55 * 60) # 5 hours 55 mins
STATE_FILE = "sniped_state_2.json"

# --- AUTO-LOCK / SNIPER SECRETS & CONFIG ---
EMAIL = os.environ.get("BMS_EMAIL") 
PHONE = os.environ.get("BMS_PHONE")

# Define your desired seats here. 
#DESIRED_SEATS = {
#    "F": ["11", "12", "13"],
#    "K": ["09", "08"] 
#}
DESIRED_SEATS = {
    "F": ["11"]
}
# Track WARP State natively
USE_WARP = False
PROXIES = {
    "http": "socks5://127.0.0.1:40000",
    "https": "socks5://127.0.0.1:40000"
}

GET_HEADERS = {
    "Host": "in.bookmyshow.com",
    "Content-Type": "application/json",
    "X-Latitude": "17.385044",
    "X-Subregion-Code": "HYD",
    "X-App-Code": "MOBAND2",
    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 10; Android SDK built for x86_64 Build/QSR1.211112.011)",
    "X-App-Version": "18.2.3",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive"
}

POST_HEADERS = {
    "Host": "services-in.bookmyshow.com",
    "X-Timeout": "10",
    "X-Latitude": "17.385044",
    "X-Subregion-Code": "HYD",
    "X-App-Code": "MOBAND2",
    "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 10; Android SDK built for x86_64 Build/QSR1.211112.011)",
    "X-App-Version": "18.2.3",
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
                # Load the list and convert to a set for fast lookups
                return set(json.load(f))
        except json.JSONDecodeError: 
            logger.warning("Failed to decode local state JSON. Returning empty set.")
    return set()

def save_state(sniped_set):
    logger.info("\n[GIT] State mutated. Saving sniped seat state to Git...")
    for attempt in range(3):
        quiet_git_pull()
        
        # Write the set back as a JSON list
        with open(STATE_FILE, "w") as f:
            json.dump(list(sniped_set), f, indent=2)
            
        subprocess.run(["git", "add", STATE_FILE], capture_output=True, check=False)
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        
        if STATE_FILE in status.stdout:
            subprocess.run(["git", "commit", "-m", "Update sniped seats state"], capture_output=True, check=False)
            if quiet_git_push(): 
                logger.info("[STATE] ✅ State successfully saved and pushed to remote.")
                return
            logger.warning(f"Git push failed on attempt {attempt+1}/3. Retrying...")
            time.sleep(2)
        else:
            logger.debug("No state changes detected by Git.")
            return
    logger.error("❌ Failed to push state updates after 3 attempts.")

def trigger_ntfy(message, attach_url=None):
    logger.info(f"🔔 ALERTING VIA NTFY:\n{message}")
    headers = {"Priority": "urgent"}
    if attach_url:
        headers["Attach"] = attach_url
        
    for i in range(1):
        try:
            resp = requests.post("https://ntfy.sh/dolby_unblock", data=message.encode('utf-8'), headers=headers, timeout=10)
            if resp.status_code == 200:
                logger.info(f"✅ Ntfy ping sent! Status: {resp.status_code}")
            else:
                logger.warning(f"⚠️ Ntfy ping returned unexpected status: {resp.status_code}")
        except Exception as e:
            logger.error(f"❌ Ntfy ping failed: {e}")

def toggle_warp():
    global USE_WARP
    if USE_WARP:
        logger.info("    -> 🔌 Disconnecting Cloudflare WARP...")
        subprocess.run(["warp-cli", "--accept-tos", "disconnect"], capture_output=True, check=False)
        USE_WARP = False
    else:
        logger.info("    -> 🛡️ Connecting Cloudflare WARP to bypass blocks...")
        subprocess.run(["warp-cli", "--accept-tos", "connect"], capture_output=True, check=False)
        time.sleep(5)
        USE_WARP = True

def make_bms_request(method, url, max_retries=3, **kwargs):
    for attempt in range(1, max_retries + 1):
        current_proxies = PROXIES if USE_WARP else None
        try:
            if method.upper() == 'GET':
                resp = cffi_requests.get(url, proxies=current_proxies, impersonate="chrome", timeout=15, **kwargs)
            else:
                resp = cffi_requests.post(url, proxies=current_proxies, impersonate="chrome", timeout=15, **kwargs)
            
            if resp.status_code in [429, 403]:
                logger.warning(f"    -> 🚧 WAF Block (HTTP {resp.status_code}) on {method} request. (Attempt {attempt}/{max_retries})")
                if attempt < max_retries:
                    toggle_warp()
                    continue
            return resp
        except Exception as e:
            logger.error(f"🌐 Request error on {method} (Attempt {attempt}/{max_retries}): {e}")
            if attempt < max_retries: 
                time.sleep(3)
                continue
    return None

def fetch_sessions():
    logger.info("==================================================")
    logger.info("🚀 STARTING TARGETED SEAT SNIPER (CLEAN MODE)")
    logger.info("==================================================\nFetching valid sessions...\n")
    sessions = []
    
    for date_code in DATES:
        logger.info(f"[NETWORK] Fetching sessions for Date: {date_code}...")
        time.sleep(6)
        url = f"https://in.bookmyshow.com/api/movies-data/seatlayout/v1/primary?eventCode={EVENT_CODE}&dateCode={date_code}&regionCode=HYD&venueCode={VENUE_CODE}"
        
        resp = make_bms_request('GET', url, headers=GET_HEADERS)
        if not resp or resp.status_code != 200: 
            logger.error(f"    -> Status: {resp.status_code if resp else 'None'} | ❌ Failed to fetch sessions for date {date_code}.")
            continue
            
        logger.info(f"    -> Status: {resp.status_code} (Using WARP: {USE_WARP})")
        try:
            shows = resp.json().get("data", {}).get("showTimes", [])
            logger.info(f"    -> Found {len(shows)} total shows. Filtering for target time...")
            for show in shows:
                if show.get("showTime") == "02:30 PM":
                    s_attr = show.get("attributes", show.get("screenName", "Unknown Screen"))
                    
                    sessions.append({
                        "sessionId": show["sessionId"], 
                        "dateCode": show["showDateCode"], 
                        "time": show["showTime"],
                        "attribute": s_attr
                    })
        except Exception as e: 
            logger.error(f"    -> ❌ Error parsing session JSON for {date_code}: {e}")
            pass
            
    logger.info(f"\n✅ Found a total of {len(sessions)} desired sessions to monitor.")
    logger.info("==================================================\n")
    return sessions

def fetch_seat_layout(session_id):
    logger.info(f"    -> [POST] https://services-in.bookmyshow.com/doTrans.aspx (Session: {session_id})")
    url = "https://services-in.bookmyshow.com/doTrans.aspx"
    payload = f"strParam4=&strParam5=Y&strParam6=&strParam7=N&strParam1={session_id}&strParam2=WEB&strParam3=&strVenueCode={VENUE_CODE}&lngTransactionIdentifier=0&strAppCode=MOBAND2&strFormat=json&strCommand=GETSEATLAYOUT"
    resp = make_bms_request('POST', url, headers=POST_HEADERS, data=payload)
    if resp:
        logger.info(f"    -> Status: {resp.status_code} (Using WARP: {USE_WARP})")
    if not resp or resp.status_code != 200: 
        logger.warning(f"    -> ⚠️ Failed to fetch seat layout for {session_id}.")
        return ""
    try: 
        return resp.json().get("BookMyShow", {}).get("strData", "")
    except Exception as e: 
        logger.error(f"    -> ❌ Error parsing layout JSON for {session_id}: {e}")
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

# =======================================================
# AUTO-LOCK / PAYMENT SNIPER FUNCTIONS
# =======================================================

def lock_seat(session_id, row_index, backend_seat, cat_code, area_id):
    logger.info(f"    -> 🔒 [SNIPER] Request 1: Attempting to lock internal Row {row_index} Seat {backend_seat} ({cat_code})...")
    url = "https://in.bookmyshow.com/api/v2/mobile/booking/movies"
    
    padded_area = str(area_id).zfill(4) 
    
    payload = {
        "appCode": "MOBAND2",
        "venueCode": VENUE_CODE,
        "sessionId": str(session_id),
        "ticketCategory": padded_area,
        "numberOfTickets": "1",
        "selectedSeats": f"|1|{cat_code}|{area_id}|{row_index}|{backend_seat}|",
        "email": EMAIL,
        "eventCode": EVENT_CODE,
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
    resp = make_bms_request('POST', url, headers=headers, data=data_str.encode('utf-8'))
    
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

def initiate_payment(trans_id, trans_uid):
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
        f"--{boundary}", 'Content-Disposition: form-data; name="strParam5"', '', '',
        f"--{boundary}", 'Content-Disposition: form-data; name="strParam6"', '', '',
        f"--{boundary}", 'Content-Disposition: form-data; name="strParam7"', '', '',
        f"--{boundary}", 'Content-Disposition: form-data; name="strParam8"', '', '',
        f"--{boundary}", 'Content-Disposition: form-data; name="strParam9"', '', '',
        f"--{boundary}", 'Content-Disposition: form-data; name="strParam10"', '', '',
        f"--{boundary}", 'Content-Disposition: form-data; name="strFormat"', '', 'json',
        f"--{boundary}", 'Content-Disposition: form-data; name="facebookBrowserId"', '', '',
        f"--{boundary}--", ''
    ]
    
    payload_str = '\r\n'.join(form_fields)
    resp = make_bms_request('POST', url, headers=headers, data=payload_str.encode('utf-8'))
    
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

def execute_snipe(session, row, seat_num, meta, categories):
    cat_info = categories.get(meta["block_code"])
    if not cat_info: return False
    
    c_code, a_id = cat_info["cat_code"], cat_info["area_id"]
    logger.info(f"    -> 🎯 [SNIPER] MATCH FOUND! Auto-locking Row {row}, Seat {seat_num} (Internal Cat: {c_code}, Area: {a_id})")
    
    # 1. Lock 
    t_id, t_uid = lock_seat(session["sessionId"], meta["row_index"], meta["backend_seat"], c_code, a_id)
    if not t_id: return False
    
    # 2. Pay
    upi_intent = initiate_payment(t_id, t_uid)
    if not upi_intent: return False
    
    # 3. Format Notification & Trigger Push
    qr_image_url = f"https://in.bookmyshow.com/secure/barcode/?IsImage=Y&strBarcodeType=qrcode&strBarcodeTxt={upi_intent}&intHeight=300&intWidth=300"
    
    hum_date = humanize_date(session["dateCode"])
    msg = f"Row {row} Seat {seat_num} is locked and awaiting payment.\n\n{VENUE_CODE} {EVENT_CODE} {hum_date} {session['time']} {session['attribute']}"
    
    trigger_ntfy(msg, attach_url=qr_image_url)
    
    return True

# =======================================================
# MAIN LOOP
# =======================================================

def main():
    start_time = time.time()
    
    target_sessions = fetch_sessions()
    total_sessions = len(target_sessions)
    if total_sessions == 0: 
        logger.warning("No sessions found to monitor. Exiting.")
        return

    # In-memory tracking to ensure we don't spam snipe requests for the same seat
    logger.info("[GIT] Loading initial state from GitHub repository...")
    sniped_seats_memory = load_state()
    logger.info(f"[STATE] Loaded existing state for {len(sniped_seats_memory)} previously sniped seats.\n")

    cycle_count = 1
    while (time.time() - start_time) < MAX_RUNTIME_SECONDS:
        logger.info("==================================================")
        logger.info(f"🔄 STARTING POLLING CYCLE {cycle_count}")
        logger.info("==================================================\n")
        
        for index, session in enumerate(target_sessions, 1):
            s_id = session["sessionId"]
            
            hum_date = humanize_date(session["dateCode"])
            logger.info(f"[{index}/{total_sessions}] Checking Session {s_id} (Date: {hum_date} Time: {session['time']})")
            logger.info("    -> Sleeping for 23 seconds (Rate Limit Prevention)...")
            time.sleep(23)
            
            str_data = fetch_seat_layout(s_id)
            if not str_data: 
                continue
                
            current_seats, categories_map, seat_metadata, total_available = parse_layout(str_data)
            logger.info(f"    -> Parse successful. Current Available Seats: {total_available}")
            
            # Track if we successfully sniped anything in this specific session to batch the Git save
            state_mutated_this_session = False
            desired_seat_found = False
            
            # 1. Iterate strictly over DESIRED_SEATS to enforce ROW priority
            for target_row, target_seat_list in DESIRED_SEATS.items():
                
                # If this entire row is sold out or blocked, skip it entirely
                if target_row not in current_seats:
                    continue
                    
                available_in_row = current_seats[target_row]
                
                # 2. Iterate strictly over the array to enforce SEAT priority
                for target_seat in target_seat_list:
                    seat_memory_key = f"{s_id}_{target_row}_{target_seat}"
                    
                    # If seat is available AND we haven't sniped it yet
                    if target_seat in available_in_row and seat_memory_key not in sniped_seats_memory:
                        
                        meta = seat_metadata.get(f"{target_row}_{target_seat}")
                        
                        # Lock it, generate QR, and push notification
                        success = execute_snipe(session, target_row, target_seat, meta, categories_map)
                        
                        if success:
                            # Add to in-memory blacklist so we never snipe it again
                            desired_seat_found = True
                            sniped_seats_memory.add(seat_memory_key)
                            logger.info(f"✅ Successfully sniped and memorized: {seat_memory_key}")
                            
                            state_mutated_this_session = True
                            
                            # Required 1-second delay between successful lock requests
                            time.sleep(1)

            if not desired_seat_found:
                logger.info("    -> ⚪ No desired seats found.\n")
            else:
                logger.info("") # Just an empty line for spacing if actions were taken

            if not state_mutated_this_session:
                logger.info("[STATE] Cycle finished. No state changes detected.\n")
                            
            # 3. Batch Git Save (Execute only ONCE per session after all snipes are complete)
            if state_mutated_this_session:
                save_state(sniped_seats_memory)

        cycle_count += 1
        
    logger.info("🏁 Max runtime reached. Script shutting down gracefully.")
        
if __name__ == "__main__":
    main()
