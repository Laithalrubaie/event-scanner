import streamlit as st
from streamlit_webrtc import webrtc_streamer, VideoProcessorBase, RTCConfiguration
import cv2
import numpy as np
import av
import queue
import re
import os
import json
import base64
import time
from twilio.rest import Client
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

# --- CONFIGURATION ---
TWILIO_SID = 'AC14911ac5ee7380049fc38986c318f829'
TWILIO_TOKEN = 'ba415a1d96f3140cd7dea2b22623ab75'
TWILIO_FROM = 'whatsapp:+14155238886'

SHEET_NAME = "Teachers Attendance"
CREDENTIALS_FILE = "credentials.json"

st.set_page_config(page_title="Event Scanner", page_icon="📷")

# --- 1. SIDEBAR & RESET BUTTON ---
st.sidebar.title("🔧 Controls")
if st.sidebar.button("🔄 Reset / Reload DB", type="primary"):
    # This wipes the Python memory so you can scan people again
    st.session_state.scanned_phones = set()
    st.session_state.db_loaded = False
    st.toast("🧹 Memory Cleared! Reloading from Sheet...", icon="♻️")
    time.sleep(1)

st.title("📷 Live Scanner Pro")

# --- 2. GLOBAL MEMORY ---
# We use this to remember who is scanned so we don't spam the sheet
if 'scanned_phones' not in st.session_state:
    st.session_state.scanned_phones = set()

# --- 3. CONNECT SERVICES ---
@st.cache_resource
def init_services():
    sheet_obj = None
    twilio_obj = None
    try:
        scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = None
        if os.path.exists(CREDENTIALS_FILE):
            creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scope)
        elif "GOOGLE_CREDENTIALS_BASE64" in st.secrets:
            try:
                b64_str = st.secrets["GOOGLE_CREDENTIALS_BASE64"]
                json_str = base64.b64decode(b64_str).decode("utf-8")
                key_dict = json.loads(json_str)
                creds = Credentials.from_service_account_info(key_dict, scopes=scope)
            except: pass
        if creds:
            g_client = gspread.authorize(creds)
            sheet_obj = g_client.open(SHEET_NAME).sheet1
    except: pass

    try:
        sid = st.secrets.get("TWILIO_SID", TWILIO_SID)
        token = st.secrets.get("TWILIO_TOKEN", TWILIO_TOKEN)
        if sid and token:
            twilio_obj = Client(sid, token)
    except: pass
    
    return sheet_obj, twilio_obj

sheet, twilio_client = init_services()

# --- 4. LOAD EXISTING DATA ---
# If we just started (or clicked Reset), download the list from the sheet
if sheet and ('db_loaded' not in st.session_state or not st.session_state.db_loaded):
    try:
        phone_column = sheet.col_values(2)[1:] # Get Column 2, skip header
        for p in phone_column:
            clean_num = re.sub(r'\D', '', str(p))
            if clean_num:
                st.session_state.scanned_phones.add(clean_num)
        st.session_state.db_loaded = True
        st.toast(f"✅ Loaded {len(st.session_state.scanned_phones)} existing guests.")
    except: pass

# --- 5. CAMERA & PROCESSOR ---
result_queue = queue.Queue()

# Share the memory with the camera thread
PHONE_CACHE = st.session_state.scanned_phones

class QRProcessor(VideoProcessorBase):
    def __init__(self):
        self.qr_detector = cv2.QRCodeDetector()

    def recv(self, frame):
        img = frame.to_ndarray(format="bgr24")
        data, points, _ = self.qr_detector.detectAndDecode(img)
        
        message = ""
        color = (0, 255, 0)

        if data:
            if points is not None:
                pts = np.array(points, np.int32).reshape((-1, 1, 2))
                
                # Check Local Memory
                raw_phone = re.sub(r'\D', '', data)
                
                # If they are already in the set, show RED
                if raw_phone in PHONE_CACHE:
                    message = "ALREADY SCANNED"
                    color = (0, 0, 255) # Red
                    cv2.polylines(img, [pts], True, color, 4)
                else:
                    # New person -> Green
                    message = "NEW GUEST!"
                    color = (0, 255, 0) # Green
                    cv2.polylines(img, [pts], True, color, 4)
                    result_queue.put(data) # Send to main app to save

            cv2.putText(img, message, (50, 80), cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 4)

        return av.VideoFrame.from_ndarray(img, format="bgr24")

# --- 6. UI SETUP ---
# Simple Google STUN servers (Most reliable for you)
rtc_config = RTCConfiguration({"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]})

camera_mode = st.radio("Camera:", ["Back (Mobile)", "Front/Laptop"], horizontal=True)
v_constraints = {"facingMode": {"ideal": "environment"}, "width": {"ideal": 640}} if camera_mode == "Back (Mobile)" else {"facingMode": "user", "width": {"ideal": 640}}

webrtc_ctx = webrtc_streamer(
    key="scanner",
    video_processor_factory=QRProcessor,
    rtc_configuration=rtc_config,
    media_stream_constraints={"video": v_constraints, "audio": False},
    async_processing=True,
)
# --- 6. DEBUG PROCESSING LOOP ---
if webrtc_ctx.state.playing:
    message_box = st.empty()
    debug_box = st.empty() # New box for error logs
    
    while True:
        if not webrtc_ctx.state.playing:
            break
            
        try:
            scanned_data = result_queue.get(timeout=0.1)
            
            if scanned_data:
                # STEP 1: Acknowledge Scan
                message_box.info(f"⚡ Found QR: {scanned_data}")
                debug_box.write("Log: Starting process...")
                
                # STEP 2: Check Connections
                if sheet is None:
                    debug_box.error("❌ ERROR: Google Sheet is NOT connected. check init_services()")
                else:
                    debug_box.success("✅ Google Sheet is connected.")

                if twilio_client is None:
                    debug_box.error("❌ ERROR: Twilio is NOT connected. check tokens.")
                else:
                    debug_box.success("✅ Twilio is connected.")

                # STEP 3: Parse Data
                raw_text = scanned_data
                # Extract numbers only
                phone_numeric = re.sub(r'\D', '', raw_text) 
                # Extract text only
                name = re.sub(r'[0-9,.-]', '', raw_text).strip()
                if not name: name = "Unknown"
                
                debug_box.write(f"Log: Parsed Name: {name}, Phone: {phone_numeric}")

                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # STEP 4: Save to Sheet
                if sheet:
                    try:
                        debug_box.write("Log: Attempting to save to sheet...")
                        sheet.append_row([name, phone_numeric, timestamp, "ARRIVED"])
                        debug_box.write("Log: Saved to sheet successfully.")
                    except Exception as e:
                        debug_box.error(f"❌ CRITICAL SHEET ERROR: {e}")

                # STEP 5: Send WhatsApp
                if twilio_client:
                    try:
                        debug_box.write("Log: Attempting to send WhatsApp...")
                        
                        # ⚠️ TWILIO REQUIRES COUNTRY CODE
                        # If your QR doesn't have it, hardcode it here (e.g., '1' for USA, '91' for India)
                        if not phone_numeric.startswith("1"): 
                             # Example: Force add +1 if missing. CHANGE THIS to your country code!
                             final_phone = f"whatsapp:+1{phone_numeric}"
                        else:
                             final_phone = f"whatsapp:+{phone_numeric}"

                        debug_box.write(f"Log: Sending to {final_phone}...")

                        message = twilio_client.messages.create(
                            body=f"Hello {name}, Welcome! 📷",
                            from_=TWILIO_FROM,
                            to=final_phone
                        )
                        debug_box.write(f"Log: Message Sent! SID: {message.sid}")
                        
                    except Exception as e:
                        # This is usually where it fails
                        debug_box.error(f"❌ CRITICAL TWILIO ERROR: {e}")

                st.balloons()
                time.sleep(5) # Pause to read errors
                
        except queue.Empty:
            time.sleep(0.1)
