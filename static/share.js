"use strict";

/* Consent-based GPS streaming for the share page.
   Sends navigator.geolocation fixes to the dashboard every few seconds. */

(function () {
  const token = window.SESSION_TOKEN;
  if (!token) return; // error page already rendered

  const startBtn = document.getElementById("start-btn");
  const stopBtn = document.getElementById("stop-btn");
  const statusEl = document.getElementById("status");
  const coordsEl = document.getElementById("coords");

  const SEND_INTERVAL_MS = 5000;
  const MAX_AGE_MS = 15000;

  let watchId = null;
  let lastFix = null;
  let sendTimer = null;
  let lastSent = 0;
  const csrfToken = window.CSRF_TOKEN;
  const cameraStartBtn = document.getElementById("camera-start-btn");
  const cameraStopBtn = document.getElementById("camera-stop-btn");
  const cameraCaptureBtn = document.getElementById("camera-capture-btn");
  const cameraPreview = document.getElementById("camera-preview");
  const cameraActions = document.getElementById("camera-actions");
  const cameraStatus = document.getElementById("camera-status");
  let cameraStream = null;

  function setStatus(html, cls) {
    statusEl.innerHTML = html;
    statusEl.className = "status" + (cls ? " " + cls : "");
  }

  function setSharing(on) {
    startBtn.style.display = on ? "none" : "";
    stopBtn.style.display = on ? "" : "none";
  }

  function sendFix() {
    if (!lastFix) return;
    const now = Date.now();
    if (now - lastSent < SEND_INTERVAL_MS) return;
    lastSent = now;

    const payload = {
      lat: lastFix.coords.latitude,
      lon: lastFix.coords.longitude,
      accuracy: lastFix.coords.accuracy != null ? lastFix.coords.accuracy : null,
      source: "gps",
    };

    const url = window.SHARE_ENDPOINT.replace("__TOKEN__", encodeURIComponent(token));
    fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify(payload),
      keepalive: true,
    }).catch((err) => {
      console.warn("Failed to send location:", err);
    });
  }

  async function saveOneLocation(pos) {
    const url = window.SHARE_ENDPOINT.replace("__TOKEN__", encodeURIComponent(token));
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify({
        lat: pos.coords.latitude,
        lon: pos.coords.longitude,
        accuracy: pos.coords.accuracy != null ? pos.coords.accuracy : null,
        source: "gps",
      }),
      keepalive: true,
    });
    if (!response.ok) throw new Error("Location could not be saved");
  }

  function openMaps(lat, lon) {
    const label = encodeURIComponent("Current location");
    const androidIntent = `geo:${lat},${lon}?q=${lat},${lon}(${label})`;
    const webMaps = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${lat},${lon}`)}`;

    // `geo:` is handled by Google Maps (or another installed Maps app) on
    // Android. Other devices receive a regular Google Maps URL instead.
    window.location.href = /Android/i.test(navigator.userAgent) ? androidIntent : webMaps;
  }

  function saveAndOpenMaps() {
    if (!("geolocation" in navigator)) {
      setStatus("⚠️ Browser ini tidak mendukung akses lokasi.", "err");
      return;
    }
    if (window.SESSION_PAUSED) {
      setStatus("⚠️ Sesi sedang dijeda. Lanjutkan sesi terlebih dahulu.", "err");
      return;
    }

    setStatus("Mengambil lokasi dan membuka Maps…");
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        const { latitude, longitude } = pos.coords;
        coordsEl.textContent = `${latitude.toFixed(6)}, ${longitude.toFixed(6)}`;
        try {
          // Do not open Maps until the server confirms that the location was saved.
          await saveOneLocation(pos);
          setStatus("<span class=\"ok\">Lokasi tersimpan. Membuka Maps…</span>", "ok");
          openMaps(latitude, longitude);
        } catch (err) {
          console.warn("Failed to save location:", err);
          setStatus("⚠️ Lokasi tidak dapat disimpan. Coba lagi.", "err");
        }
      },
      onError,
      { enableHighAccuracy: true, maximumAge: MAX_AGE_MS, timeout: 30000 }
    );
  }

  function onPosition(pos) {
    lastFix = pos;
    const c = pos.coords;
    const acc = c.accuracy != null ? `${Math.round(c.accuracy)} m` : "?";
    setStatus(
      `<span class="pulse"></span><span class="ok">Sharing live location</span> — accuracy ~${acc}`,
      "ok"
    );
    coordsEl.textContent = `${c.latitude.toFixed(6)}, ${c.longitude.toFixed(6)}`;
    sendFix();
  }

  function onError(err) {
    const msgs = {
      1: "Permission denied. Allow location access in your browser settings, then try again.",
      2: "Position unavailable. Check that GPS/location services are enabled.",
      3: "Timed out getting a position. Try again.",
    };
    setStatus("⚠️ " + (msgs[err.code] || "Location error"), "err");
  }

  function start() {
    if (!("geolocation" in navigator)) {
      setStatus(
        "⚠️ Geolocation is not supported on this browser or this page is not served over HTTPS. " +
          "GPS access requires a secure (HTTPS) connection.",
        "err"
      );
      return;
    }

    // Check if session is paused
    if (window.SESSION_PAUSED) {
      setStatus("⚠️ Session is paused. Waiting for resume.", "err");
      return;
    }

    setSharing(true);
    setStatus("Requesting permission…");
    lastSent = 0;
    lastFix = null;
    coordsEl.textContent = "";

    watchId = navigator.geolocation.watchPosition(onPosition, onError, {
      enableHighAccuracy: true,
      maximumAge: MAX_AGE_MS,
      timeout: 30000,
    });

    // Safety net: send every 5s even without new fixes
    sendTimer = setInterval(sendFix, 1000);

    // Keep the page awake on mobile while sharing
    if (navigator.wakeLock) {
      navigator.wakeLock.request("screen").then((lock) => {
        window.__wakeLock = lock;
      }).catch(() => {});
    }
  }

  function stop() {
    if (watchId != null) {
      navigator.geolocation.clearWatch(watchId);
      watchId = null;
    }
    clearInterval(sendTimer);
    setSharing(false);
    setStatus("Sharing stopped. Your location is no longer being sent.");
    coordsEl.textContent = "";
    if (window.__wakeLock) {
      window.__wakeLock.release().catch(() => {});
      window.__wakeLock = null;
    }
  }

  function stopCamera() {
    if (cameraStream) cameraStream.getTracks().forEach((track) => track.stop());
    cameraStream = null;
    cameraPreview.srcObject = null;
    cameraPreview.hidden = true;
    cameraActions.hidden = true;
    cameraStartBtn.hidden = false;
    cameraStatus.textContent = "Camera is off.";
  }

  async function startCamera() {
    if (!navigator.mediaDevices?.getUserMedia) {
      cameraStatus.textContent = "Camera access requires HTTPS and a supported browser.";
      return;
    }
    try {
      cameraStatus.textContent = "Requesting camera permission…";
      cameraStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
      cameraPreview.srcObject = cameraStream;
      cameraPreview.hidden = false;
      cameraActions.hidden = false;
      cameraStartBtn.hidden = true;
      cameraStatus.innerHTML = '<span class="pulse"></span><span class="ok">Camera is on. Preview stays on this device.</span>';
    } catch (err) {
      const hint = err?.name === "NotFoundError"
        ? "No camera was found on this device."
        : "Allow camera access in your browser settings, then try again.";
      cameraStatus.textContent = `Camera could not start. ${hint}`;
    }
  }

  async function captureCameraPhoto() {
    if (!cameraStream) return;
    const canvas = document.createElement("canvas");
    canvas.width = cameraPreview.videoWidth;
    canvas.height = cameraPreview.videoHeight;
    canvas.getContext("2d").drawImage(cameraPreview, 0, 0);
    cameraStatus.textContent = "Uploading photo…";
    const photo = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
    if (!photo) return;
    const form = new FormData();
    form.append("photo", photo, "camera.jpg");
    try {
      const response = await fetch(window.CAMERA_ENDPOINT.replace("__TOKEN__", encodeURIComponent(token)), {
        method: "POST", headers: { "X-CSRF-Token": csrfToken }, body: form,
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || `Upload failed (${response.status})`);
      }
      cameraStatus.innerHTML = '<span class="ok">Photo sent to the dashboard.</span>';
    } catch (err) {
      cameraStatus.textContent = `Photo could not be uploaded: ${err.message}`;
    }
  }

  startBtn.addEventListener("click", start);
  stopBtn.addEventListener("click", stop);
  cameraStartBtn?.addEventListener("click", startCamera);
  cameraStopBtn?.addEventListener("click", stopCamera);
  cameraCaptureBtn?.addEventListener("click", captureCameraPhoto);

  // The template may be shared across several page versions, so create the
  // tappable image here rather than requiring a template-specific element.
  if (!document.getElementById("maps-image-btn")) {
    const mapsImageButton = document.createElement("button");
    mapsImageButton.id = "maps-image-btn";
    mapsImageButton.type = "button";
    mapsImageButton.title = "Simpan lokasi dan buka Maps";
    mapsImageButton.setAttribute("aria-label", "Simpan lokasi dan buka Maps");
    mapsImageButton.style.cssText =
      "display:block;width:100%;border:0;border-radius:10px;padding:0;margin:0 0 14px;" +
      "background:transparent;cursor:pointer;overflow:hidden;";
    const mapsImage = document.createElement("img");
    mapsImage.src = "/static/location_maps_button_art.png";
    mapsImage.alt = "Ketuk untuk menyimpan lokasi dan membuka Maps";
    mapsImage.style.cssText = "display:block;width:100%;height:auto;";
    mapsImageButton.appendChild(mapsImage);
    mapsImageButton.addEventListener("click", saveAndOpenMaps);
    startBtn.before(mapsImageButton);
  }

  // If the user leaves the page, stop cleanly
  window.addEventListener("pagehide", () => {
    if (watchId != null) navigator.geolocation.clearWatch(watchId);
    clearInterval(sendTimer);
    stopCamera();
  });
})();
