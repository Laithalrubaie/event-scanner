import streamlit as st
from streamlit_webrtc import webrtc_streamer, VideoProcessorBase, RTCConfiguration
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
from google.oauth2.service_account import Credentials
from datetime import datetime

# --- CONFIGURATION ---
TWILIO_SID = 'AC14911ac5ee7380049fc38986c318f829'
TWILIO_TOKEN = 'ba415a1d96f3140cd7dea2b22623ab75'
TWILIO_FROM = 'whatsapp:+14155238886'

SHEET_NAME = "Teachers Attendance"
CREDENTIALS_FILE = "credentials.json"

st.set_page_config(page_title="Event Scanner", page_icon="📷")
st.title("📷 Live Event Scanner")

# --- HYBRID CONNECTION SETUP ---
@st.cache_resource
def init_services():
    sheet_obj = None
    twilio_obj = None

    # 1. CONNECT GOOGLE SHEETS
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
    except Exception as e:
        print(f"Google Error: {e}")

    # 2. CONNECT TWILIO
    try:
        sid = st.secrets.get("TWILIO_SID", TWILIO_SID)
        token = st.secrets.get("TWILIO_TOKEN", TWILIO_TOKEN)
        if sid and token:
            twilio_obj = Client(sid, token)
    except Exception as e:
        print(f"Twilio Error: {e}")
    
    return sheet_obj, twilio_obj

sheet, twilio_client = init_services()

# --- 🌐 NETWORK BOOSTER ---
@st.cache_data(ttl=3600)
def get_ice_servers():
    # Try Twilio first
    try:
        if twilio_client:
            token = twilio_client.tokens.create()
            return token.ice_servers
    except: pass
    
    # Fallback to Free Servers
    return [
        {"urls": ["stun:stun.l.google.com:19302"]},
        {"urls": ["stun:stun1.l.google.com:19302"]},
    ]

# --- UI STATUS ---
if sheet: st.toast("✅ Google Connected")
if twilio_client: st.toast("✅ Twilio Connected")

# --- SCANNER LOGIC ---
result_queue = queue.Queue()

# --- SCANNER LOGIC (Improved Visuals) ---
# --- SCANNER LOGIC (Fixed: Text disappears when QR is gone) ---
class QRProcessor(VideoProcessorBase):
    def __init__(self):
        self.qr_detector = cv2.QRCodeDetector()
        self.scanned_codes = set()
        self.last_scan_time = 0

    def recv(self, frame):
        img = frame.to_ndarray(format="bgr24")
        data, points, _ = self.qr_detector.detectAndDecode(img)
        
        current_time = time.time()
        
        # 1. Default: No message
        message = ""
        color = (0, 255, 0)

        if data:
            # We found a QR Code!
            if points is not None:
                pts = np.array(points, np.int32).reshape((-1, 1, 2))
                
                # Check if it's new or old
                # (Change '5' to '2' if you want to re-scan faster)
                if data not in self.scanned_codes or (current_time - self.last_scan_time > 5):
                    # NEW SCAN
                    self.scanned_codes.add(data)
                    self.last_scan_time = current_time
                    result_queue.put(data)
                    
                    message = "SUCCESS!"
                    color = (0, 255, 0) # Green
                    cv2.polylines(img, [pts], True, color, 4)

                else:
                    # DUPLICATE SCAN
                    message = "ALREADY SCANNED"
                    color = (0, 0, 255) # Red
                    cv2.polylines(img, [pts], True, color, 4)
            
            # 2. Draw the message ONLY if a QR code is currently visible
            cv2.putText(img, message, (50, 80), 
                        cv2.FONT_HERSHEY_SIMPLEX, 1.5, color, 4)

        return av.VideoFrame.from_ndarray(img, format="bgr24")
        
        
# --- WEBRTC COMPONENT ---
ice_servers = get_ice_servers()
rtc_configuration = RTCConfiguration({"iceServers": ice_servers})

# CAMERA SELECTOR
camera_mode = st.radio("Select Camera:", ["Back Camera (Mobile)", "Front/Laptop"], horizontal=True)

# THE FIX: Use "ideal" constraints + Limit Resolution
if camera_mode == "Back Camera (Mobile)":
    # Ask for environment, but limit width to 640px to prevent crashing
    video_constraints = {
        "facingMode": {"ideal": "environment"},
        "width": {"min": 480, "ideal": 640, "max": 1280},
        "height": {"min": 480, "ideal": 640, "max": 720}
    }
else:
    video_constraints = {
        "facingMode": "user",
        "width": {"min": 480, "ideal": 640, "max": 1280}
    }

webrtc_ctx = webrtc_streamer(
    key="scanner",
    video_processor_factory=QRProcessor,
    rtc_configuration=rtc_configuration,
    media_stream_constraints={
        "video": video_constraints, # <--- NEW OPTIMIZED SETTINGS
        "audio": False
    },
    async_processing=True,
)

# --- PROCESS RESULT ---
if webrtc_ctx.state.playing:
    try:
        scanned_data = result_queue.get(timeout=0.1)
        if scanned_data:
            st.success(f"Processing: {scanned_data}")
            
            raw_text = scanned_data
            phone = re.sub(r'\D', '', raw_text)
            name = re.sub(r'[0-9,.-]', '', raw_text).strip()
            if not name: name = "Unknown"
            
            if len(phone) <= 11:
                if phone.startswith("0"): phone = "+964" + phone[1:]
                else: phone = "+964" + phone
            else: phone = "+" + phone

            if sheet:
                try:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    sheet.append_row([name, phone, timestamp, "ARRIVED"])
                    st.toast(f"✅ Saved: {name}")
                except: st.error("Sheet Error")

            if twilio_client:
                try:
                    msg = f"Welcome {name}! You are checked in."
                    twilio_client.messages.create(body=msg, from_=TWILIO_FROM, to=f"whatsapp:{phone}")
                    st.toast(f"📨 Sent!")
                except: st.warning("Twilio Error")

    except queue.Empty:
        pass
