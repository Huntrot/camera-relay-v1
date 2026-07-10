/**
 * CamRelay Viewer — viewer.js
 * ─────────────────────────────
 * WebRTC client that receives the live camera stream from the relay server.
 *
 * Flow:
 *  1. Poll /api/status  → check if camera is live
 *  2. Create RTCPeerConnection with recvonly transceiver
 *  3. POST offer to /api/view/offer → receive answer
 *  4. Set remote description → stream arrives via ontrack
 *  5. Auto-reconnect on failure
 */

'use strict';

// ─── Config ──────────────────────────────────────────────────────────────────

const SERVER_URL = window.location.origin;

const ICE_CONFIG = {
    iceServers: [
        { urls: 'stun:stun.l.google.com:19302' },
        { urls: 'stun:stun1.l.google.com:19302' },
    ],
};

const RECONNECT_DELAY_MS = 5_000;   // retry after connection drop
const STATUS_POLL_MS     = 3_000;   // poll camera status while offline
const ICE_TIMEOUT_MS     = 8_000;   // max wait for ICE gathering

// ─── DOM References ───────────────────────────────────────────────────────────

const $ = id => document.getElementById(id);

const videoEl         = $('video-feed');
const statusBadge     = $('status-badge');
const statusText      = $('status-text');
const overlayLoading  = $('overlay-loading');
const overlayOffline  = $('overlay-offline');
const infoBar         = $('info-bar');
const infoTime        = $('info-time');
const infoResolution  = $('info-resolution');
const infoFps         = $('info-fps');
const btnReconnect    = $('btn-reconnect');
const btnFullscreen   = $('btn-fullscreen');
const videoWrap       = $('video-wrap');

// ─── State ────────────────────────────────────────────────────────────────────

let pc               = null;    // RTCPeerConnection
let statsTimer       = null;    // WebRTC stats interval
let clockTimer       = null;    // Clock interval
let reconnectTimer   = null;    // Reconnect setTimeout
let pollTimer        = null;    // Status polling interval
let isConnecting     = false;

// ─── Status UI ────────────────────────────────────────────────────────────────

/**
 * @param {'connecting'|'live'|'offline'|'error'} state
 */
function setStatus(state) {
    statusBadge.className = `status-badge ${state}`;
    statusText.textContent = {
        connecting: 'Đang kết nối…',
        live:       'TRỰC TIẾP',
        offline:    'Camera offline',
        error:      'Lỗi kết nối',
    }[state] ?? state;
}

/**
 * @param {'loading'|'offline'|null} which
 */
function showOverlay(which) {
    overlayLoading.classList.toggle('hidden', which !== 'loading');
    overlayOffline.classList.toggle('hidden', which !== 'offline');
    infoBar.classList.toggle('hidden', which !== null);
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function waitForIceGathering(peerConn) {
    return new Promise(resolve => {
        if (peerConn.iceGatheringState === 'complete') { resolve(); return; }
        const timer = setTimeout(resolve, ICE_TIMEOUT_MS);
        const handler = () => {
            if (peerConn.iceGatheringState === 'complete') {
                clearTimeout(timer);
                peerConn.removeEventListener('icegatheringstatechange', handler);
                resolve();
            }
        };
        peerConn.addEventListener('icegatheringstatechange', handler);
    });
}

function stopTimers() {
    [statsTimer, clockTimer, reconnectTimer, pollTimer].forEach(t => {
        if (t) clearInterval(t);
        if (t) clearTimeout(t);
    });
    statsTimer = clockTimer = reconnectTimer = pollTimer = null;
}

function cleanup() {
    stopTimers();
    if (pc) {
        pc.close();
        pc = null;
    }
    videoEl.srcObject = null;
    isConnecting = false;
}

function scheduleReconnect(delayMs = RECONNECT_DELAY_MS) {
    if (reconnectTimer) return;
    console.info(`[viewer] Reconnecting in ${delayMs / 1000}s…`);
    reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        connect();
    }, delayMs);
}

// ─── Stats & Clock ────────────────────────────────────────────────────────────

function startLiveStats() {
    // Clock
    const tick = () => {
        infoTime.textContent = new Date().toLocaleTimeString('vi-VN', {
            hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
        });
    };
    tick();
    clockTimer = setInterval(tick, 1000);

    // Resolution (available after metadata loads)
    videoEl.addEventListener('loadedmetadata', () => {
        infoResolution.textContent = `${videoEl.videoWidth}×${videoEl.videoHeight}`;
    }, { once: true });

    // WebRTC stats (FPS via inbound-rtp report)
    statsTimer = setInterval(async () => {
        if (!pc) return;
        try {
            const stats = await pc.getStats();
            stats.forEach(r => {
                if (r.type === 'inbound-rtp' && r.kind === 'video') {
                    const fps = Math.round(r.framesPerSecond ?? 0);
                    if (fps > 0) infoFps.textContent = `${fps} fps`;
                }
            });
        } catch (_) { /* stats not yet available */ }
    }, 2000);
}

function stopLiveStats() {
    if (clockTimer) { clearInterval(clockTimer);  clockTimer  = null; }
    if (statsTimer) { clearInterval(statsTimer);  statsTimer  = null; }
}

// ─── Core Connect ─────────────────────────────────────────────────────────────

async function connect() {
    if (isConnecting) return;
    isConnecting = true;

    cleanup();
    setStatus('connecting');
    showOverlay('loading');

    try {
        // ── 1. Check if camera is live ──────────────────────────────────────
        const statusRes  = await fetch(`${SERVER_URL}/api/status`);
        const statusData = await statusRes.json();

        if (!statusData.camera_live) {
            throw Object.assign(new Error('Camera offline'), { code: 'OFFLINE' });
        }

        // ── 2. Create peer connection ───────────────────────────────────────
        pc = new RTCPeerConnection(ICE_CONFIG);

        // ── 3. Handle incoming video track ──────────────────────────────────
        pc.ontrack = event => {
            if (event.track.kind === 'video') {
                console.info('[viewer] Video track received ✅');
                videoEl.srcObject = event.streams[0] ?? new MediaStream([event.track]);
            }
        };

        // ── 4. Connection state machine ─────────────────────────────────────
        pc.onconnectionstatechange = () => {
            const state = pc.connectionState;
            console.info('[viewer] WebRTC state →', state);

            if (state === 'connected') {
                setStatus('live');
                showOverlay(null);
                startLiveStats();
                isConnecting = false;
                stopPolling();
            } else if (['failed', 'disconnected', 'closed'].includes(state)) {
                setStatus('offline');
                showOverlay('offline');
                stopLiveStats();
                scheduleReconnect();
                isConnecting = false;
            }
        };

        pc.oniceconnectionstatechange = () => {
            console.debug('[viewer] ICE →', pc.iceConnectionState);
        };

        // ── 5. Add recvonly transceiver (we only receive, never send) ───────
        pc.addTransceiver('video', { direction: 'recvonly' });

        // ── 6. Create offer ─────────────────────────────────────────────────
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);

        // ── 7. Wait for ICE candidate gathering ─────────────────────────────
        await waitForIceGathering(pc);

        // ── 8. Send offer → get answer ──────────────────────────────────────
        const response = await fetch(`${SERVER_URL}/api/view/offer`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({
                sdp:  pc.localDescription.sdp,
                type: pc.localDescription.type,
            }),
        });

        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            if (response.status === 503) {
                throw Object.assign(new Error('Camera offline'), { code: 'OFFLINE' });
            }
            throw new Error(`Server error ${response.status}: ${err.detail ?? 'unknown'}`);
        }

        // ── 9. Apply answer ─────────────────────────────────────────────────
        const answer = await response.json();
        await pc.setRemoteDescription(new RTCSessionDescription(answer));

        console.info('[viewer] Offer/answer complete — waiting for stream…');
        // Connection state will fire 'connected' → showOverlay(null)

    } catch (err) {
        console.warn('[viewer] Connection error:', err.message);
        isConnecting = false;

        if (err.code === 'OFFLINE') {
            setStatus('offline');
            showOverlay('offline');
            startPolling();           // poll until camera comes back
        } else {
            setStatus('error');
            showOverlay('offline');
            scheduleReconnect(10_000);
        }
    }
}

// ─── Status Polling (while camera is offline) ─────────────────────────────────

function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(async () => {
        try {
            const r    = await fetch(`${SERVER_URL}/api/status`);
            const data = await r.json();
            if (data.camera_live) {
                stopPolling();
                if (!isConnecting) connect();
            }
        } catch (_) { /* network unavailable */ }
    }, STATUS_POLL_MS);
}

function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

// ─── Event Handlers ───────────────────────────────────────────────────────────

btnReconnect.addEventListener('click', () => {
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    stopPolling();
    connect();
});

btnFullscreen.addEventListener('click', () => {
    if (!document.fullscreenElement) {
        videoWrap.requestFullscreen?.() ??
        videoWrap.webkitRequestFullscreen?.();       // Safari
    } else {
        document.exitFullscreen?.();
    }
});

// Keyboard shortcut: F → fullscreen
document.addEventListener('keydown', e => {
    if (e.key === 'f' || e.key === 'F') btnFullscreen.click();
});

// Resume when tab becomes visible again
document.addEventListener('visibilitychange', () => {
    if (!document.hidden && pc?.connectionState !== 'connected') {
        if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
        connect();
    }
});

// ─── Boot ─────────────────────────────────────────────────────────────────────
connect();
