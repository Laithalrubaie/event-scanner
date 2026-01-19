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
from twilio.rest import Client
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

# ==========================================
# ⚙️ CONFIGURATION
# ==========================================
# These are fallback keys for testing on your laptop. 
# For the Cloud, we will use the "Secrets" menu instead.
DEFAULT_TWILIO_SID = 'YOUR_TWILIO_SID_HERE'
DEFAULT_TWILIO_TOKEN = 'YOUR_TWILIO_TOKEN_HERE'
TWILIO_FROM = 'whatsapp:+14155238886'

SHEET_NAME = "Teachers Attendance"
CREDENTIALS_FILE = "credentials.json"

# ==========================================
# 🚀 PAGE SETUP
# ==========================================
st.set_page_config(page_title="Event Scanner", page_icon="📷")
st.title("📷 Live Event Scanner")

# ==========================================
# 🔌 CONNECTION SETUP (Hybrid: Local + Cloud)
# ==========================================
# --- HYBRID CONNECTION SETUP ---
@st.cache_resource
def setup_connections():
    sheet = None
    twilio = None

    # 1. CONNECT GOOGLE SHEETS
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        
        # Strategy A: Check for local file (Laptop)
        if os.path.exists(CREDENTIALS_FILE):
            creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
        
        # Strategy B: Check for Cloud Secrets (Streamlit Cloud)
        elif "gcp_service_account" in st.secrets:
            # --- THE FIX IS HERE ---
            # We convert the secrets to a normal dictionary
            creds_dict = dict(st.secrets["gcp_service_account"])
            
            # We manually fix the Private Key string by replacing \\n with real \n
            if "private_key" in creds_dict:
                creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
            
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            
        else:
            st.error("❌ Key File Missing! Put 'credentials.json' in the folder OR set up Secrets.")
            return None, None

        g_client = gspread.authorize(creds)
        sheet = g_client.open(SHEET_NAME).sheet1
        st.toast("✅ Google Connected")
        
    except Exception as e:
        # We clear the cache if there is an error so it tries again next time
        st.cache_resource.clear()
        st.error(f"❌ Google Connection Error: {e}")

    # 2. CONNECT TWILIO
    try:
        if "TWILIO_SID" in st.secrets:
            sid = st.secrets["TWILIO_SID"]
            token = st.secrets["TWILIO_TOKEN"]
        else:
            sid = DEFAULT_TWILIO_SID
            token = DEFAULT_TWILIO_TOKEN
            
        twilio = Client(sid, token)
        st.toast("✅ Twilio Connected")
    except Exception as e:
        st.warning(f"⚠️ Twilio Error: {e}")
    
    return sheet, twilio

# Initialize Connections
sheet, twilio_client = setup_connections()

# ==========================================
# 🧠 THE SCANNER LOGIC (The Brain)
# ==========================================
# This queue allows the video thread to send data to the main website
result_queue = queue.Queue()

class QRProcessor(VideoTransformerBase):
    def __init__(self):
        self.qr_detector = cv2.QRCodeDetector()
        self.scanned_codes = set()
        self.last_scan_time = 0

    def recv(self, frame):
        img = frame.to_ndarray(format="bgr24")
        
        # Detect QR Code
        data, points, _ = self.qr_detector.detectAndDecode(img)
        
        if data:
            # Draw Green Box
            if points is not None:
                pts = np.array(points, np.int32).reshape((-1, 1, 2))
                cv2.polylines(img, [pts], True, (0, 255, 0), 4)
            
            # Spam Prevention Logic (Wait 10 seconds before scanning same code)
            current_time = time.time()
            if data not in self.scanned_codes or (current_time - self.last_scan_time > 10):
                self.scanned_codes.add(data)
                self.last_scan_time = current_time
                
                # Send the result to the main app
                result_queue.put(data)
                
                # Draw "SCANNED!" text on video
                cv2.putText(img, "SCANNED!", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 3)
            else:
                 cv2.putText(img, "Already Scanned", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)

        return av.VideoFrame.from_ndarray(img, format="bgr24")

# ==========================================
# 📱 THE CAMERA UI
# ==========================================
# STUN servers help mobile phones connect over 4G/Data
rtc_configuration = RTCConfiguration(
    {"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]}
)

webrtc_ctx = webrtc_streamer(
    key="scanner",
    video_transformer_factory=QRProcessor,
    rtc_configuration=rtc_configuration,
    media_stream_constraints={"video": {"facingMode": "environment"}}, # Use Back Camera
)

# ==========================================
# 📝 RESULT PROCESSING
# ==========================================
if webrtc_ctx.state.playing:
    try:
        # Check if the video thread sent us any data
        scanned_data = result_queue.get(timeout=0.1)
        
        if scanned_data:
            st.success(f"Processing: {scanned_data}")
            
            # --- 1. CLEAN DATA ---
            raw_text = scanned_data
            phone = re.sub(r'\D', '', raw_text) # Extract only numbers
            name = re.sub(r'[0-9,.-]', '', raw_text).strip() # Extract only letters
            
            # Handle empty names (bad scan)
            if not name: name = "Unknown Guest"
            
            # --- 2. FIX PHONE NUMBER (+964) ---
            if len(phone) <= 11:
                if phone.startswith("0"): phone = "+964" + phone[1:]
                else: phone = "+964" + phone
            else:
                phone = "+" + phone

            # --- 3. SAVE TO GOOGLE SHEET ---
            if sheet:
                try:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    sheet.append_row([name, phone, timestamp, "ARRIVED"])
                    st.toast(f"✅ Saved: {name}")
                except Exception as e:
                    st.error(f"Sheet Error: {e}")

            # --- 4. SEND WHATSAPP ---
            if twilio_client:
                try:
                    msg = f"Welcome {name}! You are checked in."
                    twilio_client.messages.create(body=msg, from_=TWILIO_FROM, to=f"whatsapp:{phone}")
                    st.toast(f"📨 WhatsApp Sent!")
                except Exception as e:
                    st.warning(f"Twilio Error: {e}")

    except queue.Empty:
        pass
