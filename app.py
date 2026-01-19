import streamlit as st
import cv2
import numpy as np
from PIL import Image
from twilio.rest import Client
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import re

# --- CONFIGURATION ---
# (Ideally, use st.secrets for these when deploying, but for now paste them here)
TWILIO_SID = 'AC14911ac5ee7380049fc38986c318f829'
TWILIO_TOKEN = 'ba415a1d96f3140cd7dea2b22623ab75'
TWILIO_FROM = 'whatsapp:+14155238886' 

SHEET_NAME = "Teachers Attendance"
CREDENTIALS_FILE = "credentials.json"

# --- PAGE SETUP ---
st.set_page_config(page_title="Event Check-In", page_icon="📷")

st.title("📷 Event Check-In")
st.write("Snap a photo of the ID Card to check in.")

# --- CONNECT TO DATABASE (Cached so it doesn't reconnect every time) ---
@st.cache_resource
def connect_db():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
        g_client = gspread.authorize(creds)
        sheet = g_client.open(SHEET_NAME).sheet1
        return sheet
    except Exception as e:
        return None

sheet = connect_db()
if not sheet:
    st.error("❌ Database Connection Failed. Check credentials.json")
    st.stop()

# --- THE CAMERA ---
img_file_buffer = st.camera_input("Take a Picture")

if img_file_buffer is not None:
    # 1. Convert Photo to format OpenCV understands
    bytes_data = img_file_buffer.getvalue()
    cv2_img = cv2.imdecode(np.frombuffer(bytes_data, np.uint8), cv2.IMREAD_COLOR)
    
    # 2. Detect QR Code
    detector = cv2.QRCodeDetector()
    data, bbox, _ = detector.detectAndDecode(cv2_img)
    
    if data:
        st.success(f"🔎 Scanned: {data}")
        
        # --- LOGIC START ---
        # A. Clean Data
        raw_text = data
        phone = re.sub(r'\D', '', raw_text) # Only digits
        name = re.sub(r'[0-9,.-]', '', raw_text).strip() # Only letters
        
        if not name: name = "Unknown Guest"
        
        # B. Fix Phone (+964)
        if len(phone) <= 11:
            if phone.startswith("0"): phone = "+964" + phone[1:]
            else: phone = "+964" + phone
        else:
            phone = "+" + phone

        # C. Update Sheet
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            sheet.append_row([name, phone, timestamp, "ARRIVED"])
            st.toast(f"✅ Registered: {name}")
        except Exception as e:
            st.error(f"Sheet Error: {e}")

        # D. Send WhatsApp
        try:
            client = Client(TWILIO_SID, TWILIO_TOKEN)
            msg = f"Welcome {name}! You are checked in."
            client.messages.create(body=msg, from_=TWILIO_FROM, to=f"whatsapp:{phone}")
            st.toast(f"📨 WhatsApp Sent!")
        except Exception as e:
            st.warning(f"Twilio Error: {e}")
            
    else:
        st.warning("⚠️ No QR Code found. Try moving closer.")