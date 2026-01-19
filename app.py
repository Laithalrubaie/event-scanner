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
import base64 # Added this
from twilio.rest import Client
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

# --- CONFIGURATION ---
DEFAULT_TWILIO_SID = 'YOUR_TWILIO_SID'
DEFAULT_TWILIO_TOKEN = 'YOUR_TWILIO_TOKEN'
TWILIO_FROM = 'whatsapp:+14155238886'

SHEET_NAME = "Teachers Attendance"
CREDENTIALS_FILE = "credentials.json"

st.set_page_config(page_title="Event Scanner", page_icon="📷")
st.title("📷 Live Event Scanner")

# --- HYBRID CONNECTION SETUP (Base64 Edition) ---
@st.cache_resource
def setup_connections():
    sheet = None
    twilio = None

    # 1. CONNECT GOOGLE SHEETS
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = None
        
        # Strategy A: Check for local file (Laptop)
        if os.path.exists(CREDENTIALS_FILE):
            creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
        
        # Strategy B: Check for Base64 Secret (Streamlit Cloud - The Robust Way)
        elif "GOOGLE_CREDENTIALS_BASE64" in st.secrets:
            try:
                # Decode the Base64 string back into JSON
                b64_str = st.secrets["GOOGLE_CREDENTIALS_BASE64"]
                json_str = base64.b64decode(b64_str).decode("utf-8")
                key_dict = json.loads(json_str)
                
                creds = ServiceAccountCredentials.from_json_keyfile_dict(key_dict, scope)
            except Exception as e:
                st.error(f"❌ Failed to decode Base64 credentials: {e}")
                return None, None
            
        else:
            st.error("❌ Key File Missing! Put 'credentials.json' locally OR set 'GOOGLE_CREDENTIALS_BASE64' in Secrets.")
            return None, None

        g_client = gspread.authorize(creds)
        sheet = g_client.open(SHEET_NAME).sheet1
        st.toast("✅ Google Connected")
        
    except Exception as e:
        st.cache_resource.clear() # Clear cache on error
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

            # Sheet
            if sheet:
                try:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    sheet.append_row([name, phone, timestamp, "ARRIVED"])
                    st.toast(f"✅ Saved: {name}")
                except Exception as e:
                    st.error(f"Sheet Error: {e}")

            # WhatsApp
            if twilio_client:
                try:
                    msg = f"Welcome {name}! You are checked in."
                    twilio_client.messages.create(body=msg, from_=TWILIO_FROM, to=f"whatsapp:{phone}")
                    st.toast(f"📨 WhatsApp Sent!")
                except Exception as e:
                    st.warning(f"Twilio Error: {e}")

    except queue.Empty:
        pass
