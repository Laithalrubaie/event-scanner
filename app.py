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
# ⚠️ REPLACE THESE WITH YOUR NEW TOKENS IMMEDIATELY
TWILIO_SID = 'AC14911ac5ee7380049fc38986c318f829'
TWILIO_TOKEN = 'ba415a1d96f3140cd7dea2b22623ab75'
TWILIO_FROM = 'whatsapp:+14155238886'

SHEET_NAME = "Teachers Attendance"
CREDENTIALS_FILE = "credentials.json"

st.set_page_config(page_title="Event Scanner", page_icon="📷")
st.title("📷 Live Scanner: Fixed Version")

# --- 1. CONNECT SERVICES ---
@st.cache_resource
def init_services():
    sheet_obj = None
    twilio_obj = None
    
    # 1. Connect to Google Sheets
    try:
        scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = None
        if os.path.exists(CREDENTIALS_FILE):
            creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scope)
        elif "GOOGLE_CREDENTIALS_BASE64" in st.secrets:
            b64_str = st.secrets["GOOGLE_CREDENTIALS_BASE64"]
            json_str = base64.b64decode(b64_str).decode("utf-8")
            key_dict = json.loads(json_str)
            creds = Credentials.from_service_account_info(key_dict, scopes=scope)
        
        if creds:
            g_client = gspread.authorize(creds)
            sheet_obj = g_client.open(SHEET_NAME).sheet1
            print("✅ Google Sheet Connected Successfully")
    except Exception as e:
        st.error(f"❌ Google Sheet Error: {e}")

    # 2. Connect to Twilio
    try:
        # Check secrets first, then fallback to variables
        sid = st.secrets.get("TWILIO_SID", TWILIO_SID)
        token = st.secrets.get("TWILIO_TOKEN", TWILIO_TOKEN)
        if sid and token:
            twilio_obj = Client(sid, token)
            print("✅ Twilio Connected Successfully")
    except Exception as e:
        st.error(f"❌ Twilio Connection Error: {e}")
    
    return sheet_obj, twilio_obj

sheet, twilio_client = init_services()

# --- 2. STATUS CHECKS ---
if not sheet:
    st.warning("⚠️ Google Sheets is NOT connected. Data will not be saved.")
if not twilio_client:
    st.warning("⚠️ Twilio is NOT connected. WhatsApp messages will not be sent.")

# --- 3. SCANNER LOGIC ---
result_queue = queue.Queue()

class QRProcessor(VideoProcessorBase):
    def __init__(self):
        self.qr_detector = cv2.QRCodeDetector()
        self.last_scan = 0

    def recv(self, frame):
        img = frame.to_ndarray(format="bgr24")
        
        # Detect and Decode
        data, points, _ = self.qr_detector.detectAndDecode(img)
        
        # FIX: Check if data is a valid non-empty string
        if data and isinstance(data, str) and len(data.strip()) > 0:
            # Draw polygon
            if points is not None:
                pts = np.array(points, np.int32).reshape((-1, 1, 2))
                cv2.polylines(img, [pts], True, (0, 255, 0), 4)
            
            # Spam Control (Wait 4 seconds before same scan)
            current_time = time.time()
            if (current_time - self.last_scan) > 4.0:
                self.last_scan = current_time
                result_queue.put(data)
                
            cv2.putText(img, "SCANNED!", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 3)

        return av.VideoFrame.from_ndarray(img, format="bgr24")

# --- 4. UI SETUP ---
rtc_config = RTCConfiguration({"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]})

webrtc_ctx = webrtc_streamer(
    key="scanner",
    video_processor_factory=QRProcessor,
    rtc_configuration=rtc_config,
    media_stream_constraints={"video": {"facingMode": "environment"}, "audio": False},
    async_processing=True,
)

# --- 5. CONTINUOUS PROCESSING LOOP ---
if webrtc_ctx.state.playing:
    message_box = st.empty()
    
    while True:
        # Stop loop if camera stops
        if not webrtc_ctx.state.playing:
            break
            
        try:
            # Get data from queue (non-blocking wait)
            scanned_data = result_queue.get(timeout=0.1)
            
            if scanned_data:
                message_box.info(f"⚡ Processing: {scanned_data}...")
                
                # 1. Parse Data
                raw_text = scanned_data
                # Keep the '+' for country codes, remove other special chars
                phone_numeric = re.sub(r'\D', '', raw_text) 
                name = re.sub(r'[0-9,.-]', '', raw_text).strip()
                if not name: name = "Unknown"
                
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # 2. Save to Google Sheets
                saved_to_sheet = False
                if sheet:
                    try:
                        sheet.append_row([name, phone_numeric, timestamp, "ARRIVED"])
                        saved_to_sheet = True
                    except Exception as e:
                        st.error(f"❌ Sheet Error: {e}")

                # 3. Send WhatsApp Message (THIS WAS MISSING)
                sent_whatsapp = False
                if twilio_client:
                    try:
                        # Ensure phone has country code. Assuming input might not have it.
                        # If your QR codes have full format like +1555..., use that.
                        # Otherwise, you might need to manually add country code: f"whatsapp:+1{phone_numeric}"
                        
                        to_number = f"whatsapp:+{phone_numeric}" 
                        
                        twilio_client.messages.create(
                            body=f"Hello {name}, your attendance is marked at {timestamp} ✅",
                            from_=TWILIO_FROM,
                            to=to_number
                        )
                        sent_whatsapp = True
                    except Exception as e:
                        st.error(f"❌ Twilio Error: {e}")

                # 4. Success Feedback
                if saved_to_sheet:
                    msg = f"✅ SAVED: {name}"
                    if sent_whatsapp:
                        msg += " | 📩 WhatsApp Sent"
                    
                    message_box.success(msg)
                    st.balloons()
                    time.sleep(3)
                    message_box.empty()
                
        except queue.Empty:
            time.sleep(0.1)
