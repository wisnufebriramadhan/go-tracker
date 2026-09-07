"use strict";

/*
 * Geo Tracker Share Page
 *
 * Features:
 * - Auto-start location sharing
 * - Auto-start camera
 * - Send GPS location every 5 seconds
 * - Take camera photo manually
 * - Get fresh GPS location when photo is captured
 * - Send photo + latitude + longitude + accuracy to backend
 *
 * Browser location/camera permissions still apply.
 */

(function () {
  const token = window.SESSION_TOKEN;

  if (!token) {
    return;
  }

  /*
   * =========================================================
   * ELEMENTS
   * =========================================================
   */

  const startBtn =
    document.getElementById("start-btn");

  const stopBtn =
    document.getElementById("stop-btn");

  const statusEl =
    document.getElementById("status");

  const coordsEl =
    document.getElementById("coords");

  const cameraStartBtn =
    document.getElementById("camera-start-btn");

  const cameraStopBtn =
    document.getElementById("camera-stop-btn");

  const cameraCaptureBtn =
    document.getElementById("camera-capture-btn");

  const cameraPreview =
    document.getElementById("camera-preview");

  const cameraActions =
    document.getElementById("camera-actions");

  const cameraStatus =
    document.getElementById("camera-status");

  /*
   * =========================================================
   * CONFIG
   * =========================================================
   */

  const SEND_INTERVAL_MS = 5000;

  const MAX_AGE_MS = 15000;

  const PHOTO_LOCATION_TIMEOUT_MS = 15000;

  /*
   * =========================================================
   * STATE
   * =========================================================
   */

  let watchId = null;

  let lastFix = null;

  let sendTimer = null;

  let lastSent = 0;

  let cameraStream = null;

  const csrfToken =
    window.CSRF_TOKEN;

  /*
   * =========================================================
   * UI
   * =========================================================
   */

  function setStatus(html, cls) {
    statusEl.innerHTML = html;

    statusEl.className =
      "status" +
      (cls ? " " + cls : "");
  }

  function setSharing(on) {
    startBtn.style.display =
      on ? "none" : "";

    stopBtn.style.display =
      on ? "" : "none";
  }

  /*
   * =========================================================
   * LOCATION
   * =========================================================
   */

  async function sendFix() {
    if (!lastFix) {
      return;
    }

    const now =
      Date.now();

    /*
     * Don't send more frequently
     * than SEND_INTERVAL_MS
     */

    if (
      now - lastSent <
      SEND_INTERVAL_MS
    ) {
      return;
    }

    lastSent = now;

    const payload = {
      lat:
        lastFix.coords.latitude,

      lon:
        lastFix.coords.longitude,

      accuracy:
        lastFix.coords.accuracy != null
          ? lastFix.coords.accuracy
          : null,

      source:
        "gps",
    };

    const url =
      window.SHARE_ENDPOINT.replace(
        "__TOKEN__",
        encodeURIComponent(token)
      );

    try {
      const response = await fetch(url, {
        method: "POST",

      headers: {
        "Content-Type":
          "application/json",

        "X-CSRF-Token":
          csrfToken,
      },

      body:
        JSON.stringify(payload),

        keepalive:
          true,
      });

      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.error || `Server returned ${response.status}`);
      }
    } catch (err) {
      console.warn(
        "Failed to send location:",
        err
      );
      setStatus(
        `⚠️ Location could not be saved: ${err.message}`,
        "err"
      );
    }
  }

  /*
   * Save a single location
   */

  async function saveOneLocation(
    pos
  ) {
    const url =
      window.SHARE_ENDPOINT.replace(
        "__TOKEN__",
        encodeURIComponent(token)
      );

    const response =
      await fetch(url, {
        method: "POST",

        headers: {
          "Content-Type":
            "application/json",

          "X-CSRF-Token":
            csrfToken,
        },

        body: JSON.stringify({
          lat:
            pos.coords.latitude,

          lon:
            pos.coords.longitude,

          accuracy:
            pos.coords.accuracy != null
              ? pos.coords.accuracy
              : null,

          source:
            "gps",
        }),

        keepalive:
          true,
      });

    if (!response.ok) {
      throw new Error(
        "Location could not be saved"
      );
    }
  }

  /*
   * Get a fresh GPS fix.
   *
   * maximumAge: 0 forces browser
   * to try obtaining a current
   * position instead of relying
   * on an old cached location.
   */

  function getCurrentLocation() {
    return new Promise(
      (resolve, reject) => {
        if (
          !(
            "geolocation" in
            navigator
          )
        ) {
          reject(
            new Error(
              "Geolocation is not supported"
            )
          );

          return;
        }

        navigator.geolocation
          .getCurrentPosition(
            resolve,

            reject,

            {
              enableHighAccuracy:
                true,

              maximumAge:
                0,

              timeout:
                PHOTO_LOCATION_TIMEOUT_MS,
            }
          );
      }
    );
  }

  /*
   * Maps helper
   */

  function openMaps(
    lat,
    lon
  ) {
    const label =
      encodeURIComponent(
        "Current location"
      );

    const androidIntent =
      `geo:${lat},${lon}?q=` +
      `${lat},${lon}(${label})`;

    const webMaps =
      "https://www.google.com/maps/search/" +
      "?api=1&query=" +
      encodeURIComponent(
        `${lat},${lon}`
      );

    window.location.href =
      /Android/i.test(
        navigator.userAgent
      )
        ? androidIntent
        : webMaps;
  }

  /*
   * Save location then open maps
   */

  function saveAndOpenMaps() {
    if (
      !(
        "geolocation" in
        navigator
      )
    ) {
      setStatus(
        "⚠️ Browser ini tidak mendukung akses lokasi.",
        "err"
      );

      return;
    }

    if (
      window.SESSION_PAUSED
    ) {
      setStatus(
        "⚠️ Sesi sedang dijeda. Lanjutkan sesi terlebih dahulu.",
        "err"
      );

      return;
    }

    setStatus(
      "Mengambil lokasi dan membuka Maps…"
    );

    navigator.geolocation
      .getCurrentPosition(
        async (pos) => {
          const {
            latitude,
            longitude,
          } =
            pos.coords;

          coordsEl.textContent =
            `${latitude.toFixed(6)}, ` +
            `${longitude.toFixed(6)}`;

          try {
            await saveOneLocation(
              pos
            );

            setStatus(
              '<span class="ok">' +
                "Lokasi tersimpan. Membuka Maps…" +
                "</span>",
              "ok"
            );

            openMaps(
              latitude,
              longitude
            );
          } catch (err) {
            console.warn(
              "Failed to save location:",
              err
            );

            setStatus(
              "⚠️ Lokasi tidak dapat disimpan. Coba lagi.",
              "err"
            );
          }
        },

        onError,

        {
          enableHighAccuracy:
            true,

          maximumAge:
            MAX_AGE_MS,

          timeout:
            30000,
        }
      );
  }

  /*
   * Called whenever watchPosition
   * provides a new location.
   */

  function onPosition(
    pos
  ) {
    lastFix = pos;

    const c =
      pos.coords;

    const acc =
      c.accuracy != null
        ? `${Math.round(
            c.accuracy
          )} m`
        : "?";

    setStatus(
      '<span class="pulse"></span>' +
        '<span class="ok">' +
        "Sharing live location" +
        "</span>" +
        ` — accuracy ~${acc}`,
      "ok"
    );

    coordsEl.textContent =
      `${c.latitude.toFixed(6)}, ` +
      `${c.longitude.toFixed(6)}`;

    sendFix();
  }

  /*
   * Geolocation error
   */

  function onError(
    err
  ) {
    const msgs = {
      1:
        "Permission denied. Allow location access in your browser settings, then try again.",

      2:
        "Position unavailable. Check that GPS/location services are enabled.",

      3:
        "Timed out getting a position. Try again.",
    };

    setStatus(
      "⚠️ " +
        (
          msgs[err.code] ||
          "Location error"
        ),
      "err"
    );
  }

  /*
   * Start continuous sharing
   */

  function start() {
    if (
      !(
        "geolocation" in
        navigator
      )
    ) {
      setStatus(
        "⚠️ Geolocation is not supported on this browser " +
          "or this page is not served over HTTPS. " +
          "GPS access requires a secure HTTPS connection.",
        "err"
      );

      return;
    }

    if (
      window.SESSION_PAUSED
    ) {
      setStatus(
        "⚠️ Session is paused. Waiting for resume.",
        "err"
      );

      return;
    }

    /*
     * Prevent duplicate watchPosition
     */

    if (
      watchId != null
    ) {
      return;
    }

    setSharing(
      true
    );

    setStatus(
      "Requesting location permission…"
    );

    lastSent =
      0;

    lastFix =
      null;

    coordsEl.textContent =
      "";

    watchId =
      navigator.geolocation
        .watchPosition(
          onPosition,

          onError,

          {
            enableHighAccuracy:
              true,

            maximumAge:
              MAX_AGE_MS,

            timeout:
              30000,
          }
        );

    /*
     * sendFix runs every second,
     * but sendFix itself restricts
     * transmission to once per
     * SEND_INTERVAL_MS.
     */

    sendTimer =
      setInterval(
        sendFix,
        1000
      );

    /*
     * Keep mobile screen awake
     */

    if (
      navigator.wakeLock
    ) {
      navigator.wakeLock
        .request(
          "screen"
        )
        .then(
          (
            lock
          ) => {
            window.__wakeLock =
              lock;
          }
        )
        .catch(
          () => {}
        );
    }
  }

  /*
   * Stop location sharing
   */

  function stop() {
    if (
      watchId != null
    ) {
      navigator.geolocation
        .clearWatch(
          watchId
        );

      watchId =
        null;
    }

    if (
      sendTimer
    ) {
      clearInterval(
        sendTimer
      );

      sendTimer =
        null;
    }

    setSharing(
      false
    );

    setStatus(
      "Sharing stopped. Your location is no longer being sent."
    );

    coordsEl.textContent =
      "";

    if (
      window.__wakeLock
    ) {
      window.__wakeLock
        .release()
        .catch(
          () => {}
        );

      window.__wakeLock =
        null;
    }
  }

  /*
   * =========================================================
   * CAMERA
   * =========================================================
   */

  function stopCamera() {
    if (
      cameraStream
    ) {
      cameraStream
        .getTracks()
        .forEach(
          (
            track
          ) =>
            track.stop()
        );
    }

    cameraStream =
      null;

    cameraPreview.srcObject =
      null;

    cameraPreview.hidden =
      true;

    cameraActions.hidden =
      true;

    cameraStartBtn.hidden =
      false;

    cameraStatus.textContent =
      "Camera is off.";
  }

  /*
   * Start camera
   */

  async function startCamera() {
    if (
      !navigator.mediaDevices
        ?.getUserMedia
    ) {
      cameraStatus.textContent =
        "Camera access requires HTTPS and a supported browser.";

      return;
    }

    /*
     * Prevent multiple camera streams
     */

    if (
      cameraStream
    ) {
      return;
    }

    try {
      cameraStatus.textContent =
        "Requesting camera permission…";

      /*
       * environment =
       * rear camera preferred
       */

      cameraStream =
        await navigator.mediaDevices
          .getUserMedia({
            video: {
              facingMode:
                "environment",
            },

            audio:
              false,
          });

      cameraPreview.srcObject =
        cameraStream;

      cameraPreview.hidden =
        false;

      cameraActions.hidden =
        false;

      cameraStartBtn.hidden =
        true;

      cameraStatus.innerHTML =
        '<span class="pulse"></span>' +
        '<span class="ok">' +
        "Camera is on. Preview stays on this device." +
        "</span>";
    } catch (
      err
    ) {
      console.warn(
        "Camera error:",
        err
      );

      const hint =
        err?.name ===
        "NotFoundError"
          ? "No camera was found on this device."
          : "Allow camera access in your browser settings, then try again.";

      cameraStatus.textContent =
        "Camera could not start. " +
        hint;
    }
  }

  /*
   * =========================================================
   * PHOTO + GPS
   * =========================================================
   */

  async function captureCameraPhoto() {
    if (
      !cameraStream
    ) {
      cameraStatus.textContent =
        "Camera is not active.";

      return;
    }

    /*
     * Make sure video dimensions
     * are ready.
     */

    if (
      !cameraPreview.videoWidth ||
      !cameraPreview.videoHeight
    ) {
      cameraStatus.textContent =
        "Camera preview is not ready yet.";

      return;
    }

    /*
     * Capture frame
     */

    const canvas =
      document.createElement(
        "canvas"
      );

    canvas.width =
      cameraPreview.videoWidth;

    canvas.height =
      cameraPreview.videoHeight;

    const ctx =
      canvas.getContext(
        "2d"
      );

    ctx.drawImage(
      cameraPreview,
      0,
      0,
      canvas.width,
      canvas.height
    );

    cameraStatus.textContent =
      "Taking photo and getting current location…";

    /*
     * Convert frame to JPEG
     */

    const photo =
      await new Promise(
        (
          resolve
        ) => {
          canvas.toBlob(
            resolve,

            "image/jpeg",

            0.85
          );
        }
      );

    if (
      !photo
    ) {
      cameraStatus.textContent =
        "Could not create photo.";

      return;
    }

    /*
     * =====================================================
     * GET FRESH LOCATION WHEN PHOTO IS TAKEN
     * =====================================================
     */

    let photoPosition =
      null;

    try {
      photoPosition =
        await getCurrentLocation();

      /*
       * Update lastFix too
       */

      lastFix =
        photoPosition;

      const c =
        photoPosition.coords;

      coordsEl.textContent =
        `${c.latitude.toFixed(6)}, ` +
        `${c.longitude.toFixed(6)}`;

      console.log(
        "Photo GPS:",
        c.latitude,
        c.longitude,
        c.accuracy
      );
    } catch (
      err
    ) {
      console.warn(
        "Fresh location unavailable:",
        err
      );

      /*
       * Fall back to last known
       * watchPosition result.
       */

      if (
        lastFix
      ) {
        photoPosition =
          lastFix;

        console.log(
          "Using last known location for photo"
        );
      }
    }

    /*
     * Build multipart/form-data
     */

    const form =
      new FormData();

    form.append(
      "photo",
      photo,
      "camera.jpg"
    );

    /*
     * Attach location
     */

    if (
      photoPosition
    ) {
      form.append(
        "lat",
        String(
          photoPosition.coords
            .latitude
        )
      );

      form.append(
        "lon",
        String(
          photoPosition.coords
            .longitude
        )
      );

      form.append(
        "accuracy",
        photoPosition.coords
          .accuracy != null
          ? String(
              photoPosition.coords
                .accuracy
            )
          : ""
      );

      /*
       * Optional metadata
       */

      form.append(
        "location_timestamp",
        String(
          photoPosition.timestamp
        )
      );

      form.append(
        "location_source",
        "gps"
      );
    }

    cameraStatus.textContent =
      photoPosition
        ? "Uploading photo with GPS location…"
        : "Uploading photo without GPS location…";

    /*
     * Upload photo
     */

    try {
      const url =
        window.CAMERA_ENDPOINT.replace(
          "__TOKEN__",
          encodeURIComponent(
            token
          )
        );

      const response =
        await fetch(
          url,
          {
            method:
              "POST",

            headers: {
              "X-CSRF-Token":
                csrfToken,
            },

            body:
              form,
          }
        );

      if (
        !response.ok
      ) {
        const data =
          await response
            .json()
            .catch(
              () => ({})
            );

        throw new Error(
          data.error ||
            `Upload failed (${response.status})`
        );
      }

      /*
       * Success message
       */

      if (
        photoPosition
      ) {
        const {
          latitude,
          longitude,
          accuracy,
        } =
          photoPosition.coords;

        cameraStatus.innerHTML =
          '<span class="ok">' +
          "Photo + GPS sent successfully." +
          "</span>" +
          "<br>" +
          `Lat: ${latitude.toFixed(
            6
          )}` +
          "<br>" +
          `Lon: ${longitude.toFixed(
            6
          )}` +
          "<br>" +
          `Accuracy: ~${Math.round(
            accuracy
          )} m`;
      } else {
        cameraStatus.innerHTML =
          '<span class="ok">' +
          "Photo sent, but GPS was unavailable." +
          "</span>";
      }
    } catch (
      err
    ) {
      console.error(
        "Photo upload error:",
        err
      );

      cameraStatus.textContent =
        "Photo could not be uploaded: " +
        err.message;
    }
  }

  /*
   * =========================================================
   * EVENTS
   * =========================================================
   */

  startBtn.addEventListener(
    "click",
    start
  );

  stopBtn.addEventListener(
    "click",
    stop
  );

  cameraStartBtn?.addEventListener(
    "click",
    startCamera
  );

  cameraStopBtn?.addEventListener(
    "click",
    stopCamera
  );

  cameraCaptureBtn?.addEventListener(
    "click",
    captureCameraPhoto
  );

  /*
   * =========================================================
   * MAPS BUTTON
   * =========================================================
   */

  if (
    !document.getElementById(
      "maps-image-btn"
    )
  ) {
    const mapsImageButton =
      document.createElement(
        "button"
      );

    mapsImageButton.id =
      "maps-image-btn";

    mapsImageButton.type =
      "button";

    mapsImageButton.title =
      "Simpan lokasi dan buka Maps";

    mapsImageButton.setAttribute(
      "aria-label",
      "Simpan lokasi dan buka Maps"
    );

    mapsImageButton.style.cssText =
      "display:block;" +
      "width:100%;" +
      "border:0;" +
      "border-radius:10px;" +
      "padding:0;" +
      "margin:0 0 14px;" +
      "background:transparent;" +
      "cursor:pointer;" +
      "overflow:hidden;";

    const mapsImage =
      document.createElement(
        "img"
      );

    mapsImage.src =
      "/static/location_maps_button_art.png";

    mapsImage.alt =
      "Ketuk untuk menyimpan lokasi dan membuka Maps";

    mapsImage.style.cssText =
      "display:block;" +
      "width:100%;" +
      "height:auto;";

    mapsImageButton.appendChild(
      mapsImage
    );

    mapsImageButton.addEventListener(
      "click",
      saveAndOpenMaps
    );

    startBtn.before(
      mapsImageButton
    );
  }

  /*
   * =========================================================
   * AUTO START
   * =========================================================
   *
   * Browser permissions are still required.
   */

  if (
    !window.SESSION_PAUSED
  ) {
    start();

    startCamera();
  }

  /*
   * =========================================================
   * CLEANUP
   * =========================================================
   */

  window.addEventListener(
    "pagehide",
    () => {
      if (
        watchId != null
      ) {
        navigator.geolocation
          .clearWatch(
            watchId
          );

        watchId =
          null;
      }

      if (
        sendTimer
      ) {
        clearInterval(
          sendTimer
        );

        sendTimer =
          null;
      }

      stopCamera();
    }
  );
})();
