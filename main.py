import os
import re
import time
import json
import threading
from flask import Flask
import telebot
import yt_dlp
from google import genai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

# 1. Environment Variables
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
DRIVE_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")

bot = telebot.TeleBot(BOT_TOKEN)
client = genai.Client(api_key=GEMINI_API_KEY)

# Google Drive API Setup
creds_dict = json.loads(GOOGLE_CREDS_JSON)
drive_creds = service_account.Credentials.from_service_account_info(
    creds_dict, scopes=['https://www.googleapis.com/auth/drive']
)
drive_service = build('drive', 'v3', credentials=drive_creds)

# 2. Flask Keep-Alive Server (UptimeRobot के लिए)
app = Flask(__name__)
@app.route('/')
def home():
    return "Bot is Awake and Running 24/7!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# 3. Video Downloader
def download_video(url, out_path="video.mp4"):
    ydl_opts = {
        'format': 'best[height<=720]/best',
        'outtmpl': out_path,
        'quiet': True,
        'overwrites': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    return out_path

# 4. Gemini 3.1 Pro Analysis (Error Proof)
def analyze_video(file_path):
    video_file = client.files.upload(file=file_path)
    
    try:
        # Loop with Error Prevention
        while video_file.state.name == "PROCESSING":
            time.sleep(3)
            video_file = client.files.get(name=video_file.name)
            
        if video_file.state.name == "FAILED":
            raise Exception("Gemini failed to process this video file.")

        prompt = """
        You are an expert video analyst using deep reasoning.
        Watch and listen to this entire video thoroughly and generate comprehensive, high-quality detailed notes.

        CRITICAL LANGUAGE INSTRUCTION:
        - The entire output MUST be written strictly in full, fluent English.
        - If the video contains dialogue in Hindi or another language, accurately translate and synthesize everything into English. Do NOT use Hinglish or mixed language.

        Format the notes with the following structure:
        1. Title & Executive Summary.
        2. Chronological Breakdown / Timestamps (Visual actions + dialogue).
        3. Actionable Key Takeaways.
        4. Notable Quotes & On-Screen Text.
        """

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[video_file, prompt]
        )
        return response.text

    finally:
        # Storage Leak Prevention: File will ALWAYS be deleted from Gemini cloud
        client.files.delete(name=video_file.name)

# 5. Create Google Doc
def create_google_doc(title, content, folder_id):
    file_metadata = {
        'name': title,
        'mimeType': 'application/vnd.google-apps.document',
        'parents': [folder_id]
    }
    media = MediaInMemoryUpload(content.encode('utf-8'), mimetype='text/plain')
    doc = drive_service.files().create(
        body=file_metadata, media_body=media, fields='id, webViewLink'
    ).execute()
    return doc.get('webViewLink')

# 6. Telegram Listener (Batch Processing)
@bot.message_handler(func=lambda message: True)
def handle_incoming_links(message):
    urls = re.findall(r'https?://[^\s]+', message.text)
    
    if not urls:
        bot.reply_to(message, "⚠️ Please send at least one valid Reel link.")
        return

    bot.reply_to(message, f"✅ {len(urls)} Links received! Processing in background. You can close your phone now.")

    for idx, url in enumerate(urls, start=1):
        temp_video = f"video_{message.chat.id}_{idx}.mp4"
        try:
            download_video(url, temp_video)
            notes = analyze_video(temp_video)
            
            timestamp = time.strftime("%Y-%m-%d %H:%M")
            doc_title = f"Reel Notes #{idx} - {timestamp}"
            full_content = f"Source: {url}\nAnalyzed by: Gemini 3.1 Pro\n\n" + notes

            doc_url = create_google_doc(doc_title, full_content, DRIVE_FOLDER_ID)

            bot.send_message(message.chat.id, f"📄 **[{idx}/{len(urls)}] Google Doc Ready!**\n📂 Link: {doc_url}")

        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Error on link #{idx}: {e}")

        finally:
            if os.path.exists(temp_video):
                os.remove(temp_video)

    bot.send_message(message.chat.id, "🎉 All links have been successfully processed!")

if __name__ == "__main__":
    threading.Thread(target=run_web).start()
    bot.infinity_polling(skip_pending=True)
  
