// A/B demo player: two tracks, one waveform, switching keeps the playback position
(function () {
  "use strict";

  var player = document.getElementById("abPlayer");
  if (!player) return;

  var tracks = {
    before: document.getElementById("audioBefore"),
    after: document.getElementById("audioAfter")
  };
  var raw = { before: null, after: null };
  var peaks = { before: null, after: null };
  var current = "before";

  var canvas = document.getElementById("waveCanvas");
  var ctx = canvas.getContext("2d");
  var wave = document.getElementById("playerWave");
  var playhead = document.getElementById("wavePlayhead");
  var playBtn = document.getElementById("playBtn");
  var timeCur = document.getElementById("timeCur");
  var timeTot = document.getElementById("timeTot");
  var tabs = player.querySelectorAll(".player-tab");

  function fmt(t) {
    if (!isFinite(t)) return "0:00";
    var m = Math.floor(t / 60), s = Math.floor(t % 60);
    return m + ":" + (s < 10 ? "0" : "") + s;
  }

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function duration() {
    var d = tracks[current].duration;
    return isFinite(d) && d > 0 ? d : 0;
  }

  function draw() {
    var dpr = window.devicePixelRatio || 1;
    var w = wave.clientWidth, h = wave.clientHeight;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    var data = peaks[current];
    var barW = 3, gap = 2;
    var n = Math.floor(w / (barW + gap));
    var d = duration();
    var progress = d ? tracks[current].currentTime / d : 0;
    var mid = h / 2;

    var played = ctx.createLinearGradient(0, 0, 0, h);
    played.addColorStop(0, cssVar("--amber-bright"));
    played.addColorStop(1, cssVar("--ember"));
    var unplayed = cssVar("--border");

    for (var i = 0; i < n; i++) {
      var amp = data
        ? data[Math.floor(i / n * data.length)]
        : 0.25 + 0.15 * Math.sin(i * 0.35) * Math.sin(i * 0.11); // placeholder until decoded
      var bh = Math.max(3, amp * (h - 8));
      ctx.fillStyle = (i / n) <= progress ? played : unplayed;
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(i * (barW + gap), mid - bh / 2, barW, bh, 1.5);
      else ctx.rect(i * (barW + gap), mid - bh / 2, barW, bh);
      ctx.fill();
    }
    playhead.style.transform = "translateX(" + (progress * w) + "px)";
  }

  function updateTime() {
    timeCur.textContent = fmt(tracks[current].currentTime);
    timeTot.textContent = fmt(duration());
  }

  // Real waveform from the audio files (needs http(s); falls back to the placeholder on file://)
  function normalizeAll() {
    var max = 0;
    ["before", "after"].forEach(function (k) {
      if (raw[k]) raw[k].forEach(function (v) { if (v > max) max = v; });
    });
    if (!max) return;
    ["before", "after"].forEach(function (k) {
      if (raw[k]) peaks[k] = raw[k].map(function (v) { return Math.min(1, Math.pow(v / max, 0.7)); });
    });
  }

  function loadPeaks(key) {
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC || !window.fetch) return;
    fetch(tracks[key].getAttribute("src"))
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.arrayBuffer(); })
      .then(function (buf) {
        var ac = new AC();
        return new Promise(function (res, rej) { ac.decodeAudioData(buf, res, rej); })
          .then(function (audio) { if (ac.close) ac.close(); return audio; });
      })
      .then(function (audio) {
        var ch = audio.getChannelData(0);
        var buckets = 400, size = Math.floor(ch.length / buckets), out = [];
        for (var b = 0; b < buckets; b++) {
          var sum = 0, count = 0;
          for (var j = 0; j < size; j += 8) { var v = ch[b * size + j]; sum += v * v; count++; }
          out.push(Math.sqrt(sum / (count || 1)));
        }
        raw[key] = out;
        normalizeAll();
        draw();
      })
      .catch(function () { /* keep placeholder waveform */ });
  }

  function setPlayingUI(isPlaying) {
    playBtn.classList.toggle("is-playing", isPlaying);
    playBtn.setAttribute("aria-label", isPlaying ? "Pause" : "Play");
  }

  function tick() {
    if (tracks[current].paused) return;
    updateTime();
    draw();
    requestAnimationFrame(tick);
  }

  function togglePlay() {
    var a = tracks[current];
    if (a.paused) a.play().catch(function () {}); else a.pause();
  }

  function switchTo(key) {
    if (key === current) return;
    var from = tracks[current], to = tracks[key];
    var wasPlaying = !from.paused;
    from.pause();
    from.currentTime = 0;
    current = key;
    player.setAttribute("data-state", key);
    tabs.forEach(function (tab) {
      var on = tab.getAttribute("data-track") === key;
      tab.classList.toggle("is-active", on);
      tab.setAttribute("aria-selected", on ? "true" : "false");
    });
    // The warped orchestra version isn't time-aligned with the raw recording, so each starts from the top
    to.currentTime = 0;
    if (wasPlaying) to.play().catch(function () {});
    setPlayingUI(wasPlaying);
    updateTime();
    draw();
  }

  function seek(e) {
    var d = duration();
    if (!d) return;
    var rect = wave.getBoundingClientRect();
    var x = Math.min(Math.max((e.clientX - rect.left) / rect.width, 0), 1);
    tracks[current].currentTime = x * d;
    updateTime();
    draw();
  }

  Object.keys(tracks).forEach(function (key) {
    var a = tracks[key];
    a.addEventListener("play", function () { if (key === current) { setPlayingUI(true); tick(); } });
    a.addEventListener("pause", function () { if (key === current && !a.ended) setPlayingUI(false); });
    a.addEventListener("ended", function () {
      if (key !== current) return;
      setPlayingUI(false);
      a.currentTime = 0;
      updateTime();
      draw();
    });
    a.addEventListener("loadedmetadata", function () { if (key === current) { updateTime(); draw(); } });
  });

  playBtn.addEventListener("click", togglePlay);
  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () { switchTo(tab.getAttribute("data-track")); });
  });
  wave.addEventListener("click", seek);
  window.addEventListener("resize", draw);

  draw();
  updateTime();
  loadPeaks("before");
  loadPeaks("after");
})();
