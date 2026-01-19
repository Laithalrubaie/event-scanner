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
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

# --- CONFIGURATION ---
SHEET_NAME = "Teachers Attendance" # CHECK SPELLING!
CREDENTIALS_FILE = "credentials.json"

st.set_page_config(page_title="Debug Scanner", page_icon="🐞")
st.title("🐞 Debug Mode: Force Write")

# --- 1. CONNECT GOOGLE (Simplified) ---
@st.cache_resource
def get_sheet():
    try:
        scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        if os.path.exists(CREDENTIALS_FILE):
            creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scope)
            return gspread.authorize(creds).open(SHEET_NAME).sheet1
        elif "GOOGLE_CREDENTIALS_BASE64" in st.secrets:
            b64_str = st.secrets["GOOGLE_CREDENTIALS_BASE64"]
            json_str = base64.b64decode(b64_str).decode("utf-8")
            creds = Credentials.from_service_account_info(json.loads(json_str), scopes=scope)
            return gspread.authorize(creds).open(SHEET_NAME).sheet1
    except Exception as e:
        return None

sheet = get_sheet()

if sheet:
    st.success(f"✅ Sheet Connected: {SHEET_NAME}")
else:
    st.error("❌ Sheet Connection FAILED. Check credentials.")

# --- 2. SCANNER LOGIC (No Logic, Just Pass Data) ---
result_queue = queue.Queue()

class QRProcessor(VideoProcessorBase):
    def __init__(self):
        self.qr_detector = cv2.QRCodeDetector()
        self.last_scan = 0

    def recv(self, frame):
        img = frame.to_ndarray(format="bgr24")
        data, points, _ = self.qr_detector.detectAndDecode(img)
        
        if data:
            # Draw Box
            if points is not None:
                pts = np.array(points, np.int32).reshape((-1, 1, 2))
                cv2.polylines(img, [pts], True, (0, 255, 0), 4)
            
            # Spam Control (2 seconds only)
            if (time.time() - self.last_scan) > 2.0:
                self.last_scan = time.time()
                result_queue.put(data)
                
            cv2.putText(img, "SAVING...", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 3)

        return av.VideoFrame.from_ndarray(img, format="bgr24")

# --- 3. UI ---
# Use Free Servers to avoid network issues
rtc_config = RTCConfiguration({"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]})

webrtc_ctx = webrtc_streamer(
    key="debug_scanner",
    video_processor_factory=QRProcessor,
    rtc_configuration=rtc_config,
    media_stream_constraints={"video": True, "audio": False},
    async_processing=True,
)

# --- 4. THE WRITE LOOP ---
import time
if webrtc_ctx.state.playing:
    try:
        data = result_queue.get(timeout=0.1)
        if data:
            st.info(f"⚡ CAMERA SAW: {data}")
            
            if sheet:
                st.write("Attempting to write to sheet...")
                try:
                    # Write RAW data first to test connection
                    sheet.append_row([str(data), "Debug Test", datetime.now().strftime("%H:%M:%S")])
                    st.success("✅ WROTE TO SHEET! Check it now.")
                    st.balloons()
                except Exception as e:
                    st.error(f"❌ WRITE FAILED: {e}")
            else:
                st.error("Cannot write: Sheet is disconnected.")
                
    except queue.Empty:
        pass
