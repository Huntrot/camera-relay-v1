"""
Server Configuration
Load settings from environment variables (.env file).
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─── Server ───────────────────────────────────────────────────────────────────
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

# ─── Auth ─────────────────────────────────────────────────────────────────────
# Publisher must send this key in the x-api-key header
# Generate a strong key: python -c "import secrets; print(secrets.token_hex(32))"
API_KEY = os.getenv("API_KEY", "tandeptrai@2005")



# Optional TURN server (needed when clients are behind strict firewalls)
# Self-host with Coturn or use Metered.ca free tier

TURN_URL        = os.getenv("TURN_URL", "turns:openrelay.metered.ca:443?transport=tcp")
TURN_USERNAME   = os.getenv("TURN_USERNAME", "openrelayproject")
TURN_CREDENTIAL = os.getenv("TURN_CREDENTIAL", "openrelayproject")
# TURN_URL        = os.getenv("TURN_URL", "turns:standard.relay.metered.ca:443?transport=tcp")
# TURN_USERNAME   = os.getenv("TURN_USERNAME", "1e77b21dfa1e608e3c9dd266")
# TURN_CREDENTIAL = os.getenv("TURN_CREDENTIAL", "Lze1p9VNMAlioJcZ")

# ─── ICE / WebRTC ─────────────────────────────────────────────────────────────
STUN_SERVERS = [
    "stun:stun.l.google.com:19302",
    "stun:stun1.l.google.com:19302",
]