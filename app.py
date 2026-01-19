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
import urllib.parse
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

# --- CONFIGURATION ---
SHEET_NAME = "Teachers Attendance"
CREDENTIALS_FILE = "credentials.json"

st.set_page_config(page_title="Free Event Scanner", page_icon="📷")
st.title("📷 Free Scanner: Click-to-Send")

# --- 1. CONNECT GOOGLE SHEETS ---
@st.cache_resource
def init_google_sheet():
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
            return g_client.open(SHEET_NAME).sheet1
    except Exception as e:
        st.error(f"❌ Google Sheet Error: {e}")
    return None

sheet = init_google_sheet()

if sheet:
    st.success(f"✅ Sheet Connected: {SHEET_NAME}")
else:
    st.warning("⚠️ Sheet not connected. Data will not be saved.")

# --- 2. SCANNER LOGIC ---
result_queue = queue.Queue()

class QRProcessor(VideoProcessorBase):
    def __init__(self):
        self.qr_detector = cv2.QRCodeDetector()
        self.last_scan = 0

    def recv(self, frame):
        img = frame.to_ndarray(format="bgr24")
        data, points, _ = self.qr_detector.detectAndDecode(img)
        
        # Check for valid data
        if data and isinstance(data, str) and len(data.strip()) > 0:
            if points is not None:
                pts = np.array(points, np.int32).reshape((-1, 1, 2))
                cv2.polylines(img, [pts], True, (0, 255, 0), 4)
            
            # 3-second delay to prevent double scanning
            if (time.time() - self.last_scan) > 3.0:
                self.last_scan = time.time()
                result_queue.put(data)
                
            cv2.putText(img, "SCANNED!", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 3)

        return av.VideoFrame.from_ndarray(img, format="bgr24")

# --- 3. UI & LOOP ---
rtc_config = RTCConfiguration({"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]})

webrtc_ctx = webrtc_streamer(
    key="scanner",
    video_processor_factory=QRProcessor,
    rtc_configuration=rtc_config,
    media_stream_constraints={"video": {"facingMode": "environment"}, "audio": False},
    async_processing=True,
)

# Placeholder for the "Result Card"
result_placeholder = st.empty()

if webrtc_ctx.state.playing:
    while True:
        if not webrtc_ctx.state.playing:
            break
        
        try:
            scanned_data = result_queue.get(timeout=0.1)
            
            if scanned_data:
                # 1. Parse Data
                raw_text = scanned_data
                phone_numeric = re.sub(r'\D', '', raw_text)
                name = re.sub(r'[0-9,.-]', '', raw_text).strip()
                if not name: name = "Guest"
                
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # 2. Save to Sheet
                if sheet:
                    try:
                        sheet.append_row([name, phone_numeric, timestamp, "ARRIVED"])
                    except:
                        pass # Ignore sheet errors to keep app running

                # 3. GENERATE WHATSAPP LINK
                # Create the message
                message_text = f"Hello {name}, Welcome to the event! 🎉"
                encoded_msg = urllib.parse.quote(message_text)
                
                # IMPORTANT: Ensure country code exists. Defaulting to assuming input has it, 
                # or you can force it like: wa_phone = "964" + phone_numeric
                wa_phone = phone_numeric 
                
                wa_link = f"https://wa.me/{wa_phone}?text={encoded_msg}"

                # 4. Display BIG Button
                with result_placeholder.container():
                    st.success(f"✅ **{name}** Saved!")
                    
                    # Markdown Link designed to look like a button
                    st.markdown(f"""
                    <a href="{wa_link}" target="_blank">
                        <button style="
                            width: 100%;
                            background-color: #25D366;
                            color: white;
                            padding: 15px;
                            border: none;
                            border-radius: 10px;
                            font-size: 20px;
                            font-weight: bold;
                            cursor: pointer;">
                            💬 Send WhatsApp to {name}
                        </button>
                    </a>
                    """, unsafe_allow_html=True)
                    
                    st.info("Tap the button above to send the message from your phone.")
                    st.balloons()
                
                # Clear after 5 seconds so you can scan the next person
                time.sleep(5)
                result_placeholder.empty()

        except queue.Empty:
            time.sleep(0.1)
