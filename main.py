"""
Camera Relay Server — main.py
─────────────────────────────
FastAPI + aiortc WebRTC SFU.

Architecture:
  [Publisher (laptop)] ──WebRTC──► [This Server] ──WebRTC──► [Viewer (browser/app)]

Endpoints:
  GET  /api/status          — health check, is camera live?
  POST /api/publish/offer   — publisher sends WebRTC offer (requires API key)
  POST /api/view/offer      — viewer requests the stream
  GET  /                    — serves the web viewer UI
"""

import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    RTCIceServer,
    RTCConfiguration,
    MediaStreamTrack,
)
from aiortc.contrib.media import MediaRelay

from config import (
    HOST, PORT, API_KEY,
    STUN_SERVERS,
    TURN_URL, TURN_USERNAME, TURN_CREDENTIAL,
)

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("relay-server")

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")


# ─── Global State ─────────────────────────────────────────────────────────────
# MediaRelay allows a single incoming track to be forwarded to N viewers
relay = MediaRelay()

publisher_pc:          Optional[RTCPeerConnection] = None
publisher_video_track: Optional[MediaStreamTrack]  = None   # Incoming track from publisher
viewer_pcs:            dict[str, RTCPeerConnection] = {}


# ─── App Lifecycle ────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Camera Relay Server started")
    yield
    logger.info("🛑 Shutting down — closing all peer connections...")
    all_pcs = list(viewer_pcs.values())
    if publisher_pc:
        all_pcs.append(publisher_pc)
    await asyncio.gather(*[pc.close() for pc in all_pcs], return_exceptions=True)
    logger.info("✅ All connections closed")


# ─── FastAPI App ──────────────────────────────────────────────────────────────
app = FastAPI(
    title="Camera Relay Server",
    description="WebRTC SFU relay — watch a workshop camera from anywhere",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_api_key_header = APIKeyHeader(name="x-api-key", auto_error=False)


def _require_api_key(key: str = Security(_api_key_header)):
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return key


# ─── Helpers ──────────────────────────────────────────────────────────────────
def _build_rtc_config() -> RTCConfiguration:
    servers = [RTCIceServer(urls=[url]) for url in STUN_SERVERS]
    if TURN_URL:
        servers.append(RTCIceServer(
            urls=[TURN_URL],
            username=TURN_USERNAME,
            credential=TURN_CREDENTIAL,
        ))
    return RTCConfiguration(iceServers=servers)


# ─── API Routes ───────────────────────────────────────────────────────────────

@app.get("/api/status", tags=["status"])
async def get_status():
    """Viewer polls this to know if the camera is currently live."""
    return {
        "online": True,
        "camera_live": publisher_video_track is not None,
        "publisher_state": (
            publisher_pc.connectionState if publisher_pc else "disconnected"
        ),
        "viewer_count": len(viewer_pcs),
    }


@app.post("/api/publish/offer", tags=["publisher"])
async def publish_offer(
    request: Request,
    _key: str = Security(_require_api_key),
):
    """
    Called by the laptop at the workshop.
    Receives a WebRTC offer and responds with an answer.
    The server then holds the incoming video track for relay to viewers.
    """
    global publisher_pc, publisher_video_track

    # Clean up any existing publisher connection
    if publisher_pc:
        logger.info("Closing existing publisher connection")
        try:
            await publisher_pc.close()
        except Exception as e:
            logger.warning(f"⚠️ Error closing old publisher: {e}")
    publisher_pc = None
    publisher_video_track = None

    data  = await request.json()
    offer = RTCSessionDescription(sdp=data["sdp"], type=data["type"])

    pc = RTCPeerConnection(configuration=_build_rtc_config())
    publisher_pc = pc

    @pc.on("track")
    def on_track(track: MediaStreamTrack):
        global publisher_video_track
        logger.info(f"📹 Publisher track received: kind={track.kind}")
        if track.kind == "video":
            publisher_video_track = track  # Store raw track — relay.subscribe() on demand

    @pc.on("connectionstatechange")
    async def on_conn_state():
        global publisher_video_track
        state = pc.connectionState
        logger.info(f"Publisher state → {state}")
        if state in ("failed", "closed", "disconnected"):
            publisher_video_track = None  # Camera is no longer live
            logger.warning("📵 Publisher disconnected — camera offline")
            
            # Close the publisher PC itself to release internal resources (with recursion guard)
            if state != "closed":
                await pc.close()
            
            # Close all active viewer connections to trigger auto-reconnect on FE
            all_viewers = list(viewer_pcs.values())
            viewer_pcs.clear()
            if all_viewers:
                await asyncio.gather(*[vpc.close() for vpc in all_viewers], return_exceptions=True)
                logger.info(f"Closed {len(all_viewers)} viewer connections due to publisher disconnect")

    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    logger.info("✅ Publisher connected — stream is now LIVE")
    return JSONResponse({
        "sdp":  pc.localDescription.sdp,
        "type": pc.localDescription.type,
    })


@app.post("/api/view/offer", tags=["viewer"])
async def view_offer(request: Request):
    """
    Called by a remote browser or mobile app.
    Subscribes this viewer to the publisher's relayed video track.
    """
    global viewer_pcs

    if publisher_video_track is None:
        raise HTTPException(
            status_code=503,
            detail="Camera is offline. No publisher connected.",
        )

    data  = await request.json()
    offer = RTCSessionDescription(sdp=data["sdp"], type=data["type"])

    viewer_id = str(uuid.uuid4())
    pc = RTCPeerConnection(configuration=_build_rtc_config())
    @pc.on("icegatheringstatechange")
    def on_ice_gathering():
        logger.info(f"🧊 Viewer ICE Gathering -> {pc.iceGatheringState}")


    @pc.on("iceconnectionstatechange")
    def on_ice_connection():
        logger.info(f"🧊 Viewer ICE Connection -> {pc.iceConnectionState}")


    @pc.on("icecandidate")
    def on_candidate(candidate):
        if candidate:
            logger.info(f"📍 Viewer Candidate: {candidate}")
        else:
            logger.info("📍 Viewer Candidate gathering complete")

    @pc.on("icegatheringstatechange")
    def on_ice_gathering():
        logger.info(f"🧊 Publisher ICE Gathering -> {pc.iceGatheringState}")


    @pc.on("iceconnectionstatechange")
    def on_ice_connection():
        logger.info(f"🧊 Publisher ICE Connection -> {pc.iceConnectionState}")


    @pc.on("icecandidate")
    def on_candidate(candidate):
        if candidate:
            logger.info(f"📍 Publisher Candidate: {candidate}")
        else:
            logger.info("📍 Publisher Candidate gathering complete")
    viewer_pcs[viewer_id] = pc

    # Subscribe this viewer to the publisher's video track via MediaRelay
    # buffered=False → minimal latency (frames are not queued)
    pc.addTrack(relay.subscribe(publisher_video_track, buffered=False))

    @pc.on("connectionstatechange")
    async def on_conn_state():
        state = pc.connectionState
        logger.info(f"Viewer [{viewer_id[:8]}] → {state}")
        if state in ("failed", "closed", "disconnected"):
            viewer_pcs.pop(viewer_id, None)
            await pc.close()
            logger.info(f"Viewer [{viewer_id[:8]}] removed — active: {len(viewer_pcs)}")

    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    logger.info(f"✅ Viewer [{viewer_id[:8]}] connected — total viewers: {len(viewer_pcs)}")
    return JSONResponse({
        "sdp":  pc.localDescription.sdp,
        "type": pc.localDescription.type,
    })


# ─── Serve Static Viewer Files ────────────────────────────────────────────────
# Mount AFTER all API routes so /api/... routes take priority
if os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
else:
    logger.warning(f"Static directory not found: {STATIC_DIR}")


# ─── Entry Point ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=HOST, port=PORT, reload=False, log_level="info")
