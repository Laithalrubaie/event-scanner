import streamlit as st
from streamlit_webrtc import webrtc_streamer, VideoTransformerBase, RTCConfiguration
import cv2
import numpy as np
import av
import threading
import time
import re
import os
import queue
import json
import base64
from twilio.rest import Client
import gspread
from google.oauth2.service_account import Credentials # <--- NEW LIBRARY
from datetime import datetime

# --- CONFIGURATION ---
TWILIO_SID = 'AC14911ac5ee7380049fc38986c318f829'
TWILIO_TOKEN = 'ba415a1d96f3140cd7dea2b22623ab75'
TWILIO_FROM = 'whatsapp:+14155238886'

SHEET_NAME = "Teachers Attendance"
CREDENTIALS_FILE = "credentials.json"

st.set_page_config(page_title="Event Scanner", page_icon="📷")
st.title("📷 Live Event Scanner")

# --- HYBRID CONNECTION SETUP (MODERN) ---
# --- HYBRID CONNECTION SETUP ---
@st.cache_resource
def init_services():
    """
    Establish connections. 
    Returns (sheet_object, twilio_object).
    If a connection fails, returns None for that object.
    Strictly NO UI code (st.write, st.error) allowed here.
    """
    sheet_obj = None
    twilio_obj = None

    # 1. CONNECT GOOGLE SHEETS
    try:
        scope = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = None
        
        # Strategy A: Local file
        if os.path.exists(CREDENTIALS_FILE):
            creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scope)
        
        # Strategy B: Streamlit Cloud Secrets
        elif "GOOGLE_CREDENTIALS_BASE64" in st.secrets:
            b64_str = st.secrets["GOOGLE_CREDENTIALS_BASE64"]
            # Decode b64 -> bytes -> string -> json dict
            json_str = base64.b64decode(b64_str).decode("utf-8")
            key_dict = json.loads(json_str)
            creds = Credentials.from_service_account_info(key_dict, scopes=scope)
            
        if creds:
            g_client = gspread.authorize(creds)
            sheet_obj = g_client.open(SHEET_NAME).sheet1
            
    except Exception as e:
        # Do not st.error(e) here! It causes CacheReplayClosureError.
        print(f"Google Sheet Error: {e}") # print to console is safe
        sheet_obj = None

    # 2. CONNECT TWILIO
    try:
        # Use .get() to avoid KeyErrors if secrets are missing
        sid = st.secrets.get("TWILIO_SID")
        token = st.secrets.get("TWILIO_TOKEN")
        
        if sid and token:
            twilio_obj = Client(sid, token)
            
    except Exception as e:
        print(f"Twilio Error: {e}")
        twilio_obj = None
    
    return sheet_obj, twilio_obj

# --- MAIN EXECUTION ---
# 1. Run the cached function
sheet, twilio_client = init_services()

# 2. Handle UI / Errors based on the result (Safe to do here)
if sheet:
    st.toast("✅ Google Connected")
else:
    st.error("❌ Google Connection Failed. Check server logs or secrets.")

if twilio_client:
    st.toast("✅ Twilio Connected")
else:
    st.warning("⚠️ Twilio not connected (SMS/WhatsApp disabled)")
    
    return sheet, twilio

# Initialize Connections
sheet, twilio_client = setup_connections()

# --- THE SCANNER LOGIC ---
result_queue = queue.Queue()

class QRProcessor(VideoTransformerBase):
    def __init__(self):
        self.qr_detector = cv2.QRCodeDetector()
        self.scanned_codes = set()
        self.last_scan_time = 0

    def recv(self, frame):
        img = frame.to_ndarray(format="bgr24")
        data, points, _ = self.qr_detector.detectAndDecode(img)
        
        if data:
            if points is not None:
                pts = np.array(points, np.int32).reshape((-1, 1, 2))
                cv2.polylines(img, [pts], True, (0, 255, 0), 4)
            
            current_time = time.time()
            if data not in self.scanned_codes or (current_time - self.last_scan_time > 10):
                self.scanned_codes.add(data)
                self.last_scan_time = current_time
                result_queue.put(data)
                cv2.putText(img, "SCANNED!", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 3)
            else:
                 cv2.putText(img, "Already Scanned", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

        return av.VideoFrame.from_ndarray(img, format="bgr24")

# --- UI & WEBRTC ---
rtc_configuration = RTCConfiguration(
    {"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]}
)

webrtc_ctx = webrtc_streamer(
    key="scanner",
    video_transformer_factory=QRProcessor,
    rtc_configuration=rtc_configuration,
    media_stream_constraints={"video": {"facingMode": "environment"}},
)

# --- PROCESS RESULTS ---
if webrtc_ctx.state.playing:
    try:
        scanned_data = result_queue.get(timeout=0.1)
        if scanned_data:
            st.success(f"Processing: {scanned_data}")
            
            # Logic
            raw_text = scanned_data
            phone = re.sub(r'\D', '', raw_text)
            name = re.sub(r'[0-9,.-]', '', raw_text).strip()
            if not name: name = "Unknown Guest"
            
            if len(phone) <= 11:
                if phone.startswith("0"): phone = "+964" + phone[1:]
                else: phone = "+964" + phone
            else: phone = "+" + phone

            if sheet:
                try:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    sheet.append_row([name, phone, timestamp, "ARRIVED"])
                    st.toast(f"✅ Saved: {name}")
                except Exception as e:
                    st.error(f"Sheet Error: {e}")

            if twilio_client:
                try:
                    msg = f"Welcome {name}! You are checked in."
                    twilio_client.messages.create(body=msg, from_=TWILIO_FROM, to=f"whatsapp:{phone}")
                    st.toast(f"📨 WhatsApp Sent!")
                except Exception as e:
                    st.warning(f"Twilio Error: {e}")

    except queue.Empty:
        pass
