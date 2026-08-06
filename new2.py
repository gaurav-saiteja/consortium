import requests
from curl_cffi import requests as cffi_requests
import time
import json
import os
import re
import subprocess
import random
import logging
from datetime import datetime, timedelta

# --- LOGGING CONFIGURATION ---
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
DATES = ["20260807"]
VENUE_CODE = "ACAS"
EVENT_CODE = "ET00452034"
STATE_FILE = "temp_day_state.json"
MAX_RUNTIME_SECONDS = (5 * 3600) + (55 * 60) # 5 hours 55 mins
IGNORED_ROWS = []

# --- AUTO-LOCK / SNIPER SECRETS & CONFIG ---
EMAIL = os.environ.get("BMS_EMAIL") 
PHONE = os.environ.get("BMS_PHONE")

# Define your desired seats here. 
DESIRED_SEATS = {
    "L": ["04"]
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
    dt = datetime.strptime(date_str, "%Y%m%d")
    day = dt.day
    if 11 <= (day % 100) <= 13:
        suffix = 'th'
    else:
        suffix = ['th', 'st', 'nd', 'rd', 'th'][min(day % 10, 4)]
    month_name = dt.strftime("%B")
    return f"{day}{suffix} {month_name}"

def quiet_git_pull():
    logger.debug("Executing Git pull...")
    subprocess.run(["git", "fetch", "origin", "main"], capture_output=True, check=False)
    subprocess.run(["git", "reset", "--hard", "origin/main"], capture_output=True, check=False)

def quiet_git_push():
    logger.debug("Executing Git push...")
    res = subprocess.run(["git", "push", "origin", "main"], capture_output=True, text=True, check=False)
    return res.returncode == 0

def read_local_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f: return json.load(f)
        except json.JSONDecodeError: 
            logger.warning("Failed to decode local state JSON. Returning empty state.")
            return {}
    return {}

def load_state():
    quiet_git_pull()
    return read_local_state()

def save_state(deltas, commit_msg="Update seat state"):
    logger.info(f"💾 Saving state for {len(deltas)} updated session(s)...")
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
            subprocess.run(["git", "commit", "-m", commit_msg], capture_output=True, check=False)
            if quiet_git_push(): 
                logger.info(f"✅ State successfully saved and pushed to remote.")
                return latest_state
            logger.warning(f"Git push failed on attempt {attempt+1}/3. Retrying...")
            time.sleep(2)
        else:
            logger.debug("No state changes detected by Git.")
            return latest_state
    
    logger.error("❌ Failed to push state updates after 3 attempts.")
    return latest_state

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
        logger.info("🔌 Disconnecting Cloudflare WARP...")
        subprocess.run(["warp-cli", "--accept-tos", "disconnect"], capture_output=True, check=False)
        USE_WARP = False
    else:
        logger.info("🛡️ Connecting Cloudflare WARP to bypass blocks...")
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
                logger.warning(f"🚧 WAF Block (HTTP {resp.status_code}) on {method} request. (Attempt {attempt}/{max_retries})")
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
    sessions = []
    logger.info(f"🔎 Fetching target sessions for Event: {EVENT_CODE}, Venue: {VENUE_CODE}")
    for date_code in DATES:
        time.sleep(6)
        url = f"https://in.bookmyshow.com/api/movies-data/seatlayout/v1/primary?eventCode={EVENT_CODE}&dateCode={date_code}&regionCode=HYD&venueCode={VENUE_CODE}"
        resp = make_bms_request('GET', url, headers=GET_HEADERS)
        if not resp or resp.status_code != 200: 
            logger.error(f"❌ Failed to fetch sessions for date {date_code}.")
            continue
            
        try:
            shows = resp.json().get("data", {}).get("showTimes", [])
            for show in shows:
                if show.get("showTime") == "01:00 PM":
                    sessions.append({"sessionId": show["sessionId"], "dateCode": show["showDateCode"], "time": show["showTime"]})
        except Exception as e: 
            logger.error(f"❌ Error parsing session JSON for {date_code}: {e}")
            pass
            
    logger.info(f"✅ Found {len(sessions)} matching session(s).")
    return sessions

def fetch_seat_layout(session_id):
    logger.debug(f"Fetching raw layout for session {session_id}...")
    url = "https://services-in.bookmyshow.com/doTrans.aspx"
    payload = f"strParam4=&strParam5=Y&strParam6=&strParam7=N&strParam1={session_id}&strParam2=WEB&strParam3=&strVenueCode={VENUE_CODE}&lngTransactionIdentifier=0&strAppCode=MOBAND2&strFormat=json&strCommand=GETSEATLAYOUT"
    resp = make_bms_request('POST', url, headers=POST_HEADERS, data=payload)
    if not resp or resp.status_code != 200: 
        logger.warning(f"⚠️ Failed to fetch seat layout for {session_id}.")
        return ""
    try: 
        return resp.json().get("BookMyShow", {}).get("strData", "")
    except Exception as e: 
        logger.error(f"❌ Error parsing layout JSON for {session_id}: {e}")
        return ""

def parse_layout(str_data):
    if not str_data: return {}, {}, {}
    
    parts = str_data.split("||")
    header_data = parts[0]
    
    categories = {}
    for cat in header_data.split("|"):
        c_parts = cat.split(":")
        if len(c_parts) >= 4:
            # c_parts[2] extracts the internal category index (e.g. 0000000003) instead of "GOLD"
            categories[c_parts[1]] = {"cat_code": c_parts[2], "area_id": c_parts[3]}
            
    rows_data = parts[1] if len(parts) > 1 else parts[0]
    rows = rows_data.split("|")
    
    available_seats_by_row = {}
    seat_metadata = {} 
    
    for row in rows:
        if not row or ":" not in row: continue
        elements = row.split(":")
        row_index = elements[0]      # Extract internal row index (e.g. "11")
        row_letter = elements[1]
        seats = elements[2:]
        
        available_in_row = []
        for seat in seats:
            # Captures: 1=Block Code, 2=Backend Seat Index, 3=Display Seat Num
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
            
    return available_seats_by_row, categories, seat_metadata

# =======================================================
# AUTO-LOCK / PAYMENT SNIPER FUNCTIONS
# =======================================================

def lock_seat(session_id, row_index, backend_seat, cat_code, area_id):
    logger.info(f"    -> 🔒 [SNIPER] Request 1: Attempting to lock internal Row {row_index} Seat {backend_seat} ({cat_code})...")
    url = "https://in.bookmyshow.com/api/v2/mobile/booking/movies"
    
    padded_area = str(area_id).zfill(4) # creates "0002" out of "2"
    
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
            
            logger.info("    -> 📄 [SNIPER] Lock Seat Response:")
            for k, v in r_json.items():
                logger.info(f"        {k}: {v}")
                
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

def execute_snipe(session_id, row, seat_num, meta, categories):
    cat_info = categories.get(meta["block_code"])
    if not cat_info: return False
    
    c_code, a_id = cat_info["cat_code"], cat_info["area_id"]
    logger.info(f"    -> 🎯 [SNIPER] MATCH FOUND! Auto-locking Row {row}, Seat {seat_num} (Internal Cat: {c_code}, Area: {a_id})")
    
    # 1. Lock (passing backend row & backend seat instead of display names)
    t_id, t_uid = lock_seat(session_id, meta["row_index"], meta["backend_seat"], c_code, a_id)
    if not t_id: return False
    
    # 2. Pay
    upi_intent = initiate_payment(t_id, t_uid)
    if not upi_intent: return False
    
    # 3. Create Barcode link and Trigger Push
    qr_image_url = f"https://in.bookmyshow.com/secure/barcode/?IsImage=Y&strBarcodeType=qrcode&strBarcodeTxt={upi_intent}&intHeight=300&intWidth=300"
    
    msg = f"Row {row} Seat {seat_num} is locked and awaiting payment.\nClick the image attached below to scan your QR code!"
    trigger_ntfy(msg, attach_url=qr_image_url)
    
    return True

# =======================================================
# MAIN LOOP
# =======================================================

def main():
    start_time = time.time()
    logger.info("🚀 STARTING BMS SEAT SCRAPER & SNIPER")
    
    target_sessions = fetch_sessions()
    total_sessions = len(target_sessions)
    if total_sessions == 0: 
        logger.warning("No sessions found to monitor. Exiting.")
        return

    logger.info("Loading initial state from local file/git...")
    state = load_state()
    is_first_run = len(state) == 0

    cycle_count = 1
    while (time.time() - start_time) < MAX_RUNTIME_SECONDS:
        state = load_state()
        deltas = {} 
        
        logger.info(f"🔄 --- Starting Cycle {cycle_count} ---")
        
        for index, session in enumerate(target_sessions, 1):
            s_id = session["sessionId"]
            
            logger.info(f"⏳ Sleeping 23s before fetching layout for Session {s_id} ({index}/{total_sessions})...")
            time.sleep(23) 
            
            str_data = fetch_seat_layout(s_id)
            if not str_data: 
                continue
                
            current_seats, categories_map, seat_metadata = parse_layout(str_data)
            current_total = sum(len(seats) for seats in current_seats.values())
            
            if s_id not in state:
                state[s_id] = {"date": session["dateCode"], "time": session["time"], "total": 0, "rows": {}}
            
            previous_rows = state[s_id].get("rows", {})
            total_unblocked_count = 0
            
            has_sniped_this_cycle = False
            
            for row, seats in current_seats.items():
                old_seats_in_row = previous_rows.get(row, [])
                new_seats = set(seats) - set(old_seats_in_row)
                
                if new_seats:
                    total_unblocked_count += len(new_seats)
                    
                    if not is_first_run and not has_sniped_this_cycle and row in DESIRED_SEATS:
                        snipable_seats = new_seats.intersection(DESIRED_SEATS[row])
                        
                        if snipable_seats:
                            target_seat = list(snipable_seats)[0] 
                            meta = seat_metadata.get(f"{row}_{target_seat}")
                            
                            success = execute_snipe(s_id, row, target_seat, meta, categories_map)
                            if success:
                                has_sniped_this_cycle = True
                    
            if total_unblocked_count > 0:
                logger.info(f"    -> 🟢 DETECTED UNBLOCKS: +{total_unblocked_count} new seat(s) globally for {session['time']}.")
                state[s_id]["rows"] = current_seats
                state[s_id]["total"] = current_total
                deltas[s_id] = state[s_id]

            elif current_total < state[s_id].get("total", 0):
                logger.debug(f"    -> Seats were booked/removed in session {s_id}.")
                state[s_id]["rows"] = current_seats
                state[s_id]["total"] = current_total
                deltas[s_id] = state[s_id]
            else:
                logger.debug(f"    -> No layout changes for session {s_id}.")

        if deltas:
            state = save_state(deltas, f"State update at cycle {cycle_count}")
            
        elapsed = timedelta(seconds=int(time.time() - start_time))
        logger.info(f"🏁 Cycle {cycle_count} Complete. Runtime: {elapsed} / 05:55:00 limit.")
        
        if is_first_run: 
            is_first_run = False
        
        cycle_count += 1
        
if __name__ == "__main__":
    main()
