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
st.title("📷 Live Scanner: Safe Mode")

# --- 1. CONNECT SERVICES ---
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

# --- 2. NETWORK BOOSTER (MEGA LIST) ---
@st.cache_data(ttl=3600)
def get_ice_servers():
    try:
        if twilio_client: return twilio_client.tokens.create().ice_servers
    except: pass
    return [
        {"urls": ["stun:stun.l.google.com:19302"]},
        {"urls": ["stun:stun1.l.google.com:19302"]},
        {"urls": ["stun:stun2.l.google.com:19302"]},
        {"urls": ["stun:stun3.l.google.com:19302"]},
        {"urls": ["stun:stun4.l.google.com:19302"]},
        {"urls": ["stun:stun.global.calls.net:3478"]},
        {"urls": ["stun:stun.ideasip.com"]},
        {"urls": ["stun:stun.voip.blackberry.com:3478"]},
        {"urls": ["stun:stun.server.com:3478"]},
        {"urls": ["stun:stun.schlund.de"]},
        {"urls": ["stun:stun.voiparound.com:3478"]},
        {"urls": ["stun:stun.voipbuster.com"]},
        {"urls": ["stun:stun.voipstunt.com"]},
    ]

# --- 3. CONNECTION STATUS ---
if sheet:
    st.success(f"✅ Sheet Connected: {SHEET_NAME}")
else:
    st.error("🚨 SHEET FAILED.")

# --- 4. SCANNER LOGIC ---
# We use a Queue to send data from the camera thread to the main thread
if 'result_queue' not in st.session_state:
    st.session_state.result_queue = queue.Queue()

class QRProcessor(VideoProcessorBase):
    def __init__(self):
        self.qr_detector = cv2.QRCodeDetector()
        self.last_scan = 0

    def recv(self, frame):
        img = frame.to_ndarray(format="bgr24")
        data, points, _ = self.qr_detector.detectAndDecode(img)
        
        if data:
            if points is not None:
                pts = np.array(points, np.int32).reshape((-1, 1, 2))
                cv2.polylines(img, [pts], True, (0, 255, 0), 4)
            
            # Spam Control (2 seconds)
            if (time.time() - self.last_scan) > 2.0:
                self.last_scan = time.time()
                # Send data to main app
                st.session_state.result_queue.put(data)
                
            cv2.putText(img, "PROCESSING...", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 3)

        return av.VideoFrame.from_ndarray(img, format="bgr24")

# --- 5. UI & COMPONENT ---
ice_servers = get_ice_servers()
rtc_config = RTCConfiguration({"iceServers": ice_servers})

camera_mode = st.radio("Camera:", ["Back (Mobile)", "Front/Laptop"], horizontal=True)

if camera_mode == "Back (Mobile)":
    v_constraints = {"facingMode": {"ideal": "environment"}, "width": {"ideal": 640}, "height": {"ideal": 640}}
else:
    v_constraints = {"facingMode": "user", "width": {"ideal": 640}}

# THE KEY FIX: Handle the component safely
try:
    webrtc_ctx = webrtc_streamer(
        key="scanner",
        video_processor_factory=QRProcessor,
        rtc_configuration=rtc_config,
        media_stream_constraints={"video": v_constraints, "audio": False},
        async_processing=True,
    )
except Exception as e:
    st.warning("Restarting Camera...")
    st.rerun()

# --- 6. CHECK FOR DATA & SAVE (SAFE METHOD) ---
# This runs every time the script refreshes
if webrtc_ctx.state.playing:
    try:
        # Check if the camera put anything in the mailbox
        scanned_data = st.session_state.result_queue.get(timeout=0.1)
        
        if scanned_data:
            st.info(f"⚡ CAMERA SAW: {scanned_data}")
            
            # PARSE
            raw_text = scanned_data
            phone = re.sub(r'\D', '', raw_text)
            name = re.sub(r'[0-9,.-]', '', raw_text).strip()
            if not name: name = "Unknown"

            # WRITE
            if sheet:
                try:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    sheet.append_row([name, phone, timestamp, "ARRIVED"])
                    st.success(f"✅ SAVED: {name}")
                    st.balloons()
                    
                    # ⚠️ AUTO-REFRESH TO KEEP IT STABLE
                    time.sleep(1) # Let user see the balloon
                    st.rerun() # Refresh the app for the next person
                    
                except Exception as e:
                    st.error(f"❌ WRITE ERROR: {e}")

    except queue.Empty:
        pass
