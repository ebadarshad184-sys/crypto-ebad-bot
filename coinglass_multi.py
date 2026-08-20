"""
SMTP Email Delivery Tester Script
=================================
Is script ka maqsad sirf yeh check karna hai ke Gmail SMTP aur App Password
sahih kaam kar rahe hain ya nahi.
"""


import smtplib
from email.mime.text import MIMEText
import datetime

# ==========================================
# CONFIGURATION
# ==========================================
GMAIL_ADDRESS = "arshadebad5@gmail.com"
GMAIL_APP_PASSWORD = "ondd zmuv exqj csrh"
TO_EMAIL = "arshadebad5@gmail.com"

def send_test_email():
    now_pkt = (datetime.datetime.utcnow() + datetime.timedelta(hours=5)).strftime("%Y-%m-%d %I:%M:%S %p")
    
    subject = "🧪 TEST EMAIL: Trading Bot SMTP Check"
    body = (
        f"Assalam-o-Alaikum Ebad!\n\n"
        f"Yeh ek test email hai.\n"
        f"Agar aapko yeh message mil gaya hai to aapka Gmail App Password aur SMTP setup 100% working condition mein hai.\n\n"
        f"Time: {now_pkt} (PKT)\n"
        f"Status: Success"
    )

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = TO_EMAIL

    print("Attempting to send test email...")
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            # Spaces remove kar ke login karega
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD.replace(" ", ""))
            server.sendmail(GMAIL_ADDRESS, TO_EMAIL, msg.as_string())
        print("✅ SUCCESS: Test Email successfully sent to", TO_EMAIL)
    except Exception as e:
        print("❌ ERROR: Email failed to send.")
        print(f"Details: {e}")

if __name__ == "__main__":
    send_test_email()
