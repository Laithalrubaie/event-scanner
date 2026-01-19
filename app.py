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

# --- 1. CONNECTION SETUP ---
@st.cache_resource
def init_services():
    sheet_obj = None
    twilio_obj = None

    # Connect Google
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

    # Connect Twilio
    try:
        sid = st.secrets.get("TWILIO_SID", TWILIO_SID)
        token = st.secrets.get("TWILIO_TOKEN", TWILIO_TOKEN)
        if sid and token:
            twilio_obj = Client(sid, token)
    except: pass
    
    return sheet_obj, twilio_obj

sheet, twilio_client = init_services()

# --- 2. LOAD DATABASE (The New Logic) ---
if 'db_numbers' not in st.session_state:
    st.session_state.db_numbers = set()

def load_existing_db():
    """Fetch all phone numbers from the Sheet (Column 2) so we don't scan them twice."""
    if sheet:
        try:
            # Get all values from Column 2 (Phone Number)
            # We assume Row 1 is the Header, so we skip it
            phone_column = sheet.col_values(2)[1:] 
            
            # Clean the numbers (remove spaces, symbols) just to be safe
            clean_set = set()
            for p in phone_column:
                clean_num = re.sub(r'\D', '', str(p)) # Keep only digits
                if clean_num:
                    clean_set.add(clean_num)
            
            st.session_state.db_numbers = clean_set
            st.toast(f"📚 Database Loaded: {len(clean_set)} guests found.")
        except Exception as e:
            st.error(f"Failed to load DB: {e}")

# Load DB once on startup
if not st.session_state.db_numbers:
    load_existing_db()

# --- 3. NETWORK BOOSTER (THE MEGA LIST) ---
@st.cache_data(ttl=3600)
def get_ice_servers():
    """
    If real Twilio keys are missing, we use a MASSIVE list of free public STUN servers.
    This increases the chance of punching through the 4G firewall.
    """
    # 1. Try Real Twilio (If you ever buy real keys)
    try:
        if twilio_client:
            token = twilio_client.tokens.create()
            return token.ice_servers
    except: pass
    
    # 2. THE MEGA LIST (Free Public Servers)
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
# --- 4. SCANNER LOGIC (With DB Check) ---
result_queue = queue.Queue()

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
                
                # EXTRACT PHONE NUMBER from QR Data
                # Assuming QR format is "Name, Phone" or similar
                raw_phone = re.sub(r'\D', '', data) 
                
                # LOGIC: Check against the downloaded Sheet Database
                if raw_phone in st.session_state.db_numbers:
                    # OLD GUEST (Red)
                    message = "ALREADY REGISTERED"
                    color = (0, 0, 255) # Red
                    cv2.polylines(img, [pts], True, color, 4)
                else:
                    # NEW GUEST (Green)
                    message = "NEW GUEST!"
                    color = (0, 255, 0) # Green
                    cv2.polylines(img, [pts], True, color, 4)
                    
                    # Send to Main Loop to be saved
                    result_queue.put(data)

            # Draw Message
            cv2.putText(img, message, (50, 80), 
                        cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 4)

        return av.VideoFrame.from_ndarray(img, format="bgr24")

# --- 5. UI & COMPONENT ---
if sheet: st.toast("✅ Google Ready")

# Camera Setup
ice_servers = get_ice_servers()
rtc_config = RTCConfiguration({"iceServers": ice_servers})

camera_mode = st.radio("Camera:", ["Back (Mobile)", "Front/Laptop"], horizontal=True)

if camera_mode == "Back (Mobile)":
    v_constraints = {
        "facingMode": {"ideal": "environment"},
        "width": {"ideal": 640}, 
        "height": {"ideal": 640}
    }
else:
    v_constraints = {"facingMode": "user", "width": {"ideal": 640}}

webrtc_ctx = webrtc_streamer(
    key="scanner",
    video_processor_factory=QRProcessor,
    rtc_configuration=rtc_config,
    media_stream_constraints={"video": v_constraints, "audio": False},
    async_processing=True,
)

# --- 6. PROCESS & SAVE ---
if webrtc_ctx.state.playing:
    try:
        # Get data from scanner
        scanned_data = result_queue.get(timeout=0.1)
        
        if scanned_data:
            # 1. Parse Data
            raw_text = scanned_data
            phone = re.sub(r'\D', '', raw_text)
            name = re.sub(r'[0-9,.-]', '', raw_text).strip()
            if not name: name = "Unknown"

            # 2. DOUBLE CHECK: Is it really new? (Avoid race conditions)
            if phone in st.session_state.db_numbers:
                pass # Ignore, just processed
            else:
                st.success(f"Processing New Guest: {name}")
                
                # 3. Add to Local Database IMMEDIATELY (so camera turns Red)
                st.session_state.db_numbers.add(phone)

                # 4. Format Phone for WhatsApp
                wa_phone = phone
                if len(wa_phone) <= 11:
                    if wa_phone.startswith("0"): wa_phone = "+964" + wa_phone[1:]
                    else: wa_phone = "+964" + wa_phone
                else: wa_phone = "+" + wa_phone

                # 5. Save to Google Sheet
                if sheet:
                    try:
                        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        sheet.append_row([name, phone, timestamp, "ARRIVED"])
                        st.toast(f"✅ Saved to Sheet: {name}")
                    except Exception as e:
                        st.error(f"Sheet Error: {e}")

                # 6. Send WhatsApp
                if twilio_client:
                    try:
                        msg = f"Welcome {name}! You are successfully checked in."
                        twilio_client.messages.create(body=msg, from_=TWILIO_FROM, to=f"whatsapp:{wa_phone}")
                        st.toast(f"📨 WhatsApp Sent!")
                    except: pass

    except queue.Empty:
        pass
