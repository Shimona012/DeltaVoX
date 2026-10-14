/**
 * app.js — Frontend Controller
 * Minimalist, resilient vanilla JavaScript application.
 */

// ==================================================================================
// 🌟 PROJECT & TEAM CONFIGURATION (CENTRALIZED BRANDING)
// ==================================================================================
window.APP_CONFIG = {
  PROJECT_NAME: "DELTAVOX",                         // Project Name
  PROJECT_SUBTITLE: "The sound of change",          // Tagline / Subtitle
  TEAM_NAME: "@echo off",                          // Team Name
  DEMO_VIDEO_URL: "https://youtu.be/your-demo-video", // Replace with your YouTube / Loom link
};

function readStoredValue(key, fallback = null) {
  try { return window.localStorage.getItem(key) ?? fallback; }
  catch (_err) { return fallback; }
}

function writeStoredValue(key, value) {
  try { window.localStorage.setItem(key, value); return true; }
  catch (_err) { return false; }
}

function removeStoredValue(key) {
  try { window.localStorage.removeItem(key); return true; }
  catch (_err) { return false; }
}

async function fetchPublicConfig() {
  const controller = typeof AbortController === 'undefined' ? null : new AbortController();
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => {
      if (controller) controller.abort();
      reject(new Error('Site configuration request timed out.'));
    }, 5000);
  });
  try {
    const res = await Promise.race([
      fetch('/api/config/public', {
        credentials: 'same-origin',
        ...(controller ? { signal: controller.signal } : {})
      }),
      timeout
    ]);
    if (!res.ok) throw new Error('Could not read site configuration.');
    return await res.json();
  } finally {
    clearTimeout(timer);
  }
}

// Application State
const state = {
  theme: readStoredValue('sce_theme', 'dark-peach'),
  activePage: 'studio',
  currentUser: null,
  refMode: 'preset',
  evalMode: 'spectrum',
  recordedBlob: null,
  isRecording: false,
  mediaRecorder: null,
  audioChunks: [],
  recInterval: null,
  recStartTime: 0,
  audioCtx: null,
  analyser: null,
  dataArr: null,
  animId: null,
  authModalMode: 'login', // 'login' | 'signup'
  lastAnalysisData: null,
  datasets: [],
  historyItems: []
};

let authStateRevision = 0;
function setCurrentUser(user) {
  const previousUserId = state.currentUser?.id || null;
  const nextUserId = user?.id || null;
  authStateRevision += 1;
  state.currentUser = user;
  if (previousUserId !== nextUserId) {
    state.lastAnalysisData = null;
    state.historyItems = [];
    document.getElementById('resultsSection')?.classList.add('hidden');
  }
  updateAuthUI(user);
}

function applyAppConfig() {
  if (!window.APP_CONFIG) return;
  const brandTitle = document.querySelector('.brand-title');
  const brandSub = document.querySelector('.brand-sub');
  if (brandTitle && window.APP_CONFIG.PROJECT_NAME) {
    brandTitle.textContent = window.APP_CONFIG.PROJECT_NAME;
  }
  if (brandSub && window.APP_CONFIG.PROJECT_SUBTITLE) {
    brandSub.innerHTML = `<em>${escapeHtml(window.APP_CONFIG.PROJECT_SUBTITLE)}</em>`;
  }
  const footerNote = document.getElementById('footerBrandNote');
  if (footerNote && window.APP_CONFIG.PROJECT_NAME) {
    footerNote.innerHTML = `<strong>${escapeHtml(window.APP_CONFIG.PROJECT_NAME)}</strong> &bull; <em>${escapeHtml(window.APP_CONFIG.PROJECT_SUBTITLE)}</em> &bull; Team: <code>${escapeHtml(window.APP_CONFIG.TEAM_NAME)}</code>`;
  }
  document.title = `${window.APP_CONFIG.PROJECT_NAME} — ${window.APP_CONFIG.PROJECT_SUBTITLE}`;
}

// --- INITIALIZATION ---
document.addEventListener('DOMContentLoaded', () => {
  applyAppConfig();
  initTheme();
  initNavigation();
  initAuth();
  initEmailFlows();
  initHistoryDetails();
  initStudioDock();
  initRecorder();
  initExecution();
  initScriptPresets();
  loadPresets();
  checkAuthSession();
});

// --- THEME MANAGEMENT ---
function initTheme() {
  const root = document.documentElement;
  const select = document.getElementById('themeSelect');
  const label = document.getElementById('currentThemeLabel');

  function applyTheme(themeKey) {
    if (themeKey === 'cyan-neon') {
      themeKey = 'electric-cyan';
    }
    state.theme = themeKey;
    root.setAttribute('data-theme', themeKey);
    writeStoredValue('sce_theme', themeKey);
    if (select) select.value = themeKey;
    const nameMap = {
      'dark-peach': 'Peach & Charcoal',
      'light-peach': 'Peach & Light Grey',
      'monochrome': 'High-Contrast Monochrome',
      'cyber-slate': 'Cyber Slate',
      'electric-cyan': 'Electric Cyan',
      'neon-green': 'Neon Green',
      'cyan-neon': 'Electric Cyan'
    };
    if (label) label.textContent = nameMap[themeKey] || themeKey;
    // Redraw pitch contour canvas if chart exists
    if (state.lastAnalysisData) {
      renderPitchCanvas(state.lastAnalysisData.time_series);
    }
  }

  applyTheme(state.theme);

  if (select) {
    select.addEventListener('change', (e) => applyTheme(e.target.value));
  }
}

// --- NAVIGATION & DRAWER MENU ---
function initNavigation() {
  const navItems = document.querySelectorAll('.nav-item');
  const drawerLinks = document.querySelectorAll('.drawer-link');
  const drawer = document.getElementById('sideDrawer');
  const overlay = document.getElementById('drawerOverlay');
  const menuBtn = document.getElementById('menuToggleBtn');
  const closeBtn = document.getElementById('drawerCloseBtn');

  function switchPage(pageId) {
    state.activePage = pageId;
    // Update pages
    document.querySelectorAll('.page-view').forEach(view => {
      view.classList.toggle('active', view.id === `view-${pageId}`);
    });
    // Update nav links
    navItems.forEach(btn => btn.classList.toggle('active', btn.dataset.page === pageId));
    drawerLinks.forEach(btn => btn.classList.toggle('active', btn.dataset.page === pageId));
    closeDrawer();

    if (pageId === 'history') {
      loadHistory();
    }
  }

  navItems.forEach(btn => btn.addEventListener('click', () => switchPage(btn.dataset.page)));
  drawerLinks.forEach(btn => btn.addEventListener('click', () => switchPage(btn.dataset.page)));

  function openDrawer() {
    drawer.classList.add('open');
    drawer.setAttribute('aria-hidden', 'false');
    overlay.classList.remove('hidden');
  }

  function closeDrawer() {
    drawer.classList.remove('open');
    drawer.setAttribute('aria-hidden', 'true');
    overlay.classList.add('hidden');
  }

  if (menuBtn) menuBtn.addEventListener('click', openDrawer);
  if (closeBtn) closeBtn.addEventListener('click', closeDrawer);
  if (overlay) overlay.addEventListener('click', closeDrawer);

  document.getElementById('clearHistoryBtn')?.addEventListener('click', () => {
    if (confirm('Clear all locally saved takes?')) {
      clearLocalHistory();
    }
  });

  // Quick 1-click demos
  document.getElementById('oneClickCivicDemo')?.addEventListener('click', () => {
    window.testDatasetPreset('dataset/speech_001_persuasive/level_0_ideal.wav', 'dataset/speech_001_persuasive/level_4_egregious.wav');
  });

  document.getElementById('oneClickCivicMinorDemo')?.addEventListener('click', () => {
    window.testDatasetPreset('dataset/speech_001_persuasive/level_0_ideal.wav', 'dataset/speech_001_persuasive/level_2_minor.wav');
  });
}


// --- DOCK CONTROLS (TABS & PLAYERS) ---
function initStudioDock() {
  // Ref Tabs
  const tabRefPreset = document.getElementById('tabRefPreset');
  const tabRefUpload = document.getElementById('tabRefUpload');
  const boxRefPreset = document.getElementById('boxRefPreset');
  const boxRefUpload = document.getElementById('boxRefUpload');
  const refFileInput = document.getElementById('refFileInput');
  const refPresetSelect = document.getElementById('refPresetSelect');
  const refPlayer = document.getElementById('refPlayer');

  tabRefPreset.addEventListener('click', () => {
    state.refMode = 'preset';
    tabRefPreset.classList.add('active');
    tabRefUpload.classList.remove('active');
    boxRefPreset.classList.remove('hidden');
    boxRefUpload.classList.add('hidden');
    refPlayer.src = '/' + refPresetSelect.value;
  });

  tabRefUpload.addEventListener('click', () => {
    state.refMode = 'upload';
    tabRefUpload.classList.add('active');
    tabRefPreset.classList.remove('active');
    boxRefUpload.classList.remove('hidden');
    boxRefPreset.classList.add('hidden');
  });

  function updateBenchmarkCard(val) {
    const f0El = document.getElementById('dockABenchmarkF0');
    const descEl = document.getElementById('dockABenchmarkDesc');
    if (!f0El || !descEl) return;
    f0El.textContent = 'Mean Pitch: ~128 Hz • Pace: 3.4 syl/sec';
    descEl.textContent = 'Contemporary civic keynote delivery with dynamic modal projection, steady 3.4 syllables/second cadence, and clean declarative clause articulation.';
  }

  refPresetSelect.addEventListener('change', () => {
    refPlayer.src = '/' + refPresetSelect.value;
    updateBenchmarkCard(refPresetSelect.value);
  });

  refFileInput.addEventListener('change', (e) => {
    if (e.target.files[0]) {
      refPlayer.src = URL.createObjectURL(e.target.files[0]);
    }
  });

  // Eval Tabs
  const tabEvalMic = document.getElementById('tabEvalMic');
  const tabEvalSpectrum = document.getElementById('tabEvalSpectrum');
  const tabEvalUpload = document.getElementById('tabEvalUpload');
  const boxEvalMic = document.getElementById('boxEvalMic');
  const boxEvalSpectrum = document.getElementById('boxEvalSpectrum');
  const boxEvalUpload = document.getElementById('boxEvalUpload');
  const evalSpectrumSelect = document.getElementById('evalSpectrumSelect');
  const evalFileInput = document.getElementById('evalFileInput');
  const evalPlayer = document.getElementById('evalPlayer');

  window.switchEvalMode = function(mode) {
    state.evalMode = mode;
    tabEvalMic.classList.toggle('active', mode === 'mic');
    tabEvalSpectrum.classList.toggle('active', mode === 'spectrum');
    tabEvalUpload.classList.toggle('active', mode === 'upload');

    boxEvalMic.classList.toggle('hidden', mode !== 'mic');
    boxEvalSpectrum.classList.toggle('hidden', mode !== 'spectrum');
    boxEvalUpload.classList.toggle('hidden', mode !== 'upload');

    if (mode === 'spectrum') {
      evalPlayer.src = '/' + evalSpectrumSelect.value;
    } else if (mode === 'mic' && state.recordedBlob) {
      evalPlayer.src = URL.createObjectURL(state.recordedBlob);
    }
  };

  tabEvalMic.addEventListener('click', () => switchEvalMode('mic'));
  tabEvalSpectrum.addEventListener('click', () => switchEvalMode('spectrum'));
  tabEvalUpload.addEventListener('click', () => switchEvalMode('upload'));

  evalSpectrumSelect.addEventListener('change', () => {
    evalPlayer.src = '/' + evalSpectrumSelect.value;
  });

  // Ensure initial evalPlayer audio source matches spectrum preset
  if (evalSpectrumSelect && evalSpectrumSelect.value) {
    evalPlayer.src = '/' + evalSpectrumSelect.value;
  }

  evalFileInput.addEventListener('change', (e) => {
    if (e.target.files[0]) {
      switchEvalMode('upload');
      evalPlayer.src = URL.createObjectURL(e.target.files[0]);
    }
  });
}

// --- TARGET SCRIPT PRESETS & AI SCRIPT GENERATION ---
function initScriptPresets() {
  const scriptInput = document.getElementById('targetScriptInput');
  const civicBtn = document.getElementById('loadScriptCivicBtn');
  const clearBtn = document.getElementById('clearScriptBtn');
  const badge = document.getElementById('scriptBadge');
  const genBtn = document.getElementById('generateScriptBtn');
  const verifyBtn = document.getElementById('verifyScriptBtn');
  const topicInput = document.getElementById('scriptTopicInput');
  const durationSelect = document.getElementById('scriptDurationSelect');
  const paceSelect = document.getElementById('scriptPaceSelect');
  const scriptGeneratorNote = document.getElementById('scriptGeneratorNote');

  function updateScriptTarget() {
    const seconds = Number(durationSelect?.value || 60);
    const pace = Number(paceSelect?.value || 130);
    if (scriptGeneratorNote) scriptGeneratorNote.textContent =
      `English by default. Script language follows Feedback Language. Target: about ${Math.round(seconds * pace / 60)} words.`;
  }
  durationSelect?.addEventListener('change', updateScriptTarget);
  paceSelect?.addEventListener('change', updateScriptTarget);
  document.getElementById('languageSelect')?.addEventListener('change', updateScriptTarget);
  updateScriptTarget();

  const civicScript = `We meet here today to reaffirm our shared commitment to community dialogue and civic action. When we listen with genuine empathy and speak with unyielding conviction, we bridge divides that once seemed impossible to cross. Let our voices carry not division, but clarity, purpose, and lasting resolve.`;

  function countWords(txt) {
    if (!txt) return 0;
    const matches = txt.trim().match(/\S+/g);
    return matches ? matches.length : 0;
  }

  function updateBadge(msg, isLoaded) {
    if (!badge) return;
    badge.textContent = msg;
    badge.classList.toggle('loaded', Boolean(isLoaded));
  }

  scriptInput?.addEventListener('input', () => {
    const words = countWords(scriptInput.value);
    if (words === 0) {
      updateBadge('No script loaded', false);
    } else {
      updateBadge(`✓ ${words} words in script`, true);
    }
  });

  civicBtn?.addEventListener('click', () => {
    if (scriptInput) {
      scriptInput.value = civicScript;
      updateBadge(`✓ Civic Keynote (${countWords(civicScript)} words)`, true);
      showToast('Loaded Civic keynote speech script.');
    }
  });

  clearBtn?.addEventListener('click', () => {
    if (scriptInput) {
      scriptInput.value = '';
      updateBadge('Script cleared', false);
      showToast('Practice script cleared.');
    }
  });

  // AI Speech Script Generator from any topic
  genBtn?.addEventListener('click', async () => {
    let topic = topicInput?.value.trim();
    if (!topic) {
      topic = 'Dinosaurs & Prehistoric Evolution';
      if (topicInput) topicInput.value = topic;
    }

    try {
      genBtn.disabled = true;
      genBtn.innerHTML = '<span>⏳ Writing speech script...</span>';

      const intent = document.getElementById('intentSelect')?.value || 'persuasive_oratory';
      const lang = document.getElementById('languageSelect')?.value || 'English';

      const res = await fetch('/api/script/generate', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-SCE-Request': '1'
        },
        credentials: 'same-origin',
        body: JSON.stringify({
          topic,
          intent,
          language: lang,
          duration_seconds: Number(durationSelect?.value || 60),
          pace_wpm: Number(paceSelect?.value || 130)
        })
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to generate script.');

      if (scriptInput) {
        let prettyScript = String(data.script || '').replace(/\r\n?/g, '\n').trim();
        prettyScript = prettyScript.replace(/^\s*(?:script|speech)\s*:\s*/i, '').replace(/^['"]|['"]$/g, '');
        if (intent === 'storytelling' && !/\n\s*\n/.test(prettyScript)) {
          const sentences = prettyScript.match(/[^.!?]+[.!?]+|[^.!?]+$/g) || [prettyScript];
          const groups = [[], [], []];
          sentences.forEach((sentence, index) => groups[Math.min(2, Math.floor(index * 3 / sentences.length))].push(sentence.trim()));
          prettyScript = groups.filter(group => group.length).map(group => group.join(' ')).join('\n\n');
        }
        scriptInput.value = prettyScript;
        const words = countWords(prettyScript);
        const seconds = data.estimated_seconds || Math.round(words / Number(paceSelect?.value || 130) * 60);
        updateBadge(`Script: ${topic} (${words} words · ~${seconds}s)`, true);
        scriptInput.scrollIntoView({ behavior: 'smooth' });
        if (scriptGeneratorNote && data.note) scriptGeneratorNote.textContent = data.note;
        showToast(`Generated practice script on "${topic}".`);
      }
    } catch (err) {
      showToast('Script generation notice: ' + err.message);
    } finally {
      genBtn.disabled = false;
      genBtn.innerHTML = '<span>✨ Generate Practice Script</span>';
    }
  });

  // Verify Delivery with Script Action
  verifyBtn?.addEventListener('click', () => {
    if (scriptInput && !scriptInput.value.trim()) {
      scriptInput.value = mlkScript;
      updateBadge(`✓ Auto-loaded MLK script (${countWords(mlkScript)} words)`, true);
      showToast('Auto-loaded MLK script for verification test.');
    }

    // Ensure evaluation take exists
    if (state.evalMode === 'mic' && !state.recordedBlob) {
      if (typeof window.switchEvalMode === 'function') {
        window.switchEvalMode('spectrum');
      }
    }

    showToast('Evaluating speech take against practice script...');
    const execBtn = document.getElementById('executeEvalBtn');
    if (execBtn) execBtn.click();
  });
}

// --- DIRECT PCM WAV ENCODER (100% UNCOMPRESSED RIFF WAV) ---
function exportPcmChunksToWav(chunks, sampleRate) {
  let totalLength = 0;
  for (let i = 0; i < chunks.length; i++) {
    totalLength += chunks[i].length;
  }
  const merged = new Float32Array(totalLength);
  let offset = 0;
  for (let i = 0; i < chunks.length; i++) {
    merged.set(chunks[i], offset);
    offset += chunks[i].length;
  }

  // Encode as standard 16-bit mono PCM RIFF WAV
  const dataSize = merged.length * 2;
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);

  function writeString(view, offset, string) {
    for (let i = 0; i < string.length; i++) {
      view.setUint8(offset + i, string.charCodeAt(i));
    }
  }

  /* RIFF chunk descriptor */
  writeString(view, 0, 'RIFF');
  view.setUint32(4, 36 + dataSize, true);
  writeString(view, 8, 'WAVE');

  /* fmt sub-chunk */
  writeString(view, 12, 'fmt ');
  view.setUint32(16, 16, true); // Subchunk1Size (16 for PCM)
  view.setUint16(20, 1, true); // AudioFormat (1 = PCM)
  view.setUint16(22, 1, true); // NumChannels (1 = mono)
  view.setUint32(24, sampleRate, true); // SampleRate
  view.setUint32(28, sampleRate * 2, true); // ByteRate (SampleRate * 1 channel * 2 bytes)
  view.setUint16(32, 2, true); // BlockAlign (1 channel * 2 bytes)
  view.setUint16(34, 16, true); // BitsPerSample (16 bits)

  /* data sub-chunk */
  writeString(view, 36, 'data');
  view.setUint32(40, dataSize, true);

  // Write 16-bit linear PCM samples
  let sampleOffset = 44;
  for (let i = 0; i < merged.length; i++) {
    let s = Math.max(-1, Math.min(1, merged[i]));
    view.setInt16(sampleOffset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    sampleOffset += 2;
  }

  return new Blob([view], { type: 'audio/wav' });
}

// --- DIRECT WEBAUDIO LIVE RECORDER ---
function initRecorder() {
  const micBtn = document.getElementById('micRecordBtn');
  const micBtnText = document.getElementById('micBtnText');
  const recTimer = document.getElementById('recTimer');
  const meterBar = document.getElementById('micMeterBar');
  const evalPlayer = document.getElementById('evalPlayer');

  state.pcmChunks = [];
  state.micStream = null;
  state.pcmProcessor = null;

  micBtn.addEventListener('click', async () => {
    if (!state.isRecording) {
      // A new recording attempt replaces the previous take; never submit stale audio.
      state.recordedBlob = null;
      evalPlayer.removeAttribute('src');
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        state.micStream = stream;
        state.audioCtx = new (window.AudioContext || window.webkitAudioContext)();

        // Connect Analyser for real-time VU audio meter
        const src = state.audioCtx.createMediaStreamSource(stream);
        state.analyser = state.audioCtx.createAnalyser();
        state.analyser.fftSize = 256;
        src.connect(state.analyser);
        state.dataArr = new Uint8Array(state.analyser.frequencyBinCount);

        function updateVUMeter() {
          if (!state.isRecording) return;
          state.analyser.getByteFrequencyData(state.dataArr);
          let sum = 0;
          for (let i = 0; i < state.dataArr.length; i++) sum += state.dataArr[i];
          const avg = sum / state.dataArr.length;
          meterBar.style.width = Math.min(100, (avg / 128) * 100) + '%';
          state.animId = requestAnimationFrame(updateVUMeter);
        }

        // Direct PCM audio capture via ScriptProcessorNode (bypasses MediaRecorder container bugs)
        const bufferSize = 4096;
        state.pcmProcessor = state.audioCtx.createScriptProcessor(bufferSize, 1, 1);
        state.pcmChunks = [];

        state.pcmProcessor.onaudioprocess = (e) => {
          if (!state.isRecording) return;
          const inputChannel = e.inputBuffer.getChannelData(0);
          state.pcmChunks.push(new Float32Array(inputChannel));
        };

        src.connect(state.pcmProcessor);
        state.pcmProcessor.connect(state.audioCtx.destination);

        state.isRecording = true;
        state.recStartTime = Date.now();
        micBtn.classList.add('recording');
        micBtnText.textContent = 'Halt Recording';
        updateVUMeter();

        state.recInterval = setInterval(() => {
          const sec = (Date.now() - state.recStartTime) / 1000;
          const m = Math.floor(sec / 60).toString().padStart(2, '0');
          const s = (sec % 60).toFixed(1).padStart(4, '0');
          recTimer.textContent = `${m}:${s}`;
        }, 100);

      } catch (err) {
        alert('Microphone access denied or audio device not found: ' + err.message);
      }
    } else {
      // Halt recording
      state.isRecording = false;
      clearInterval(state.recInterval);
      cancelAnimationFrame(state.animId);
      micBtn.classList.remove('recording');
      micBtnText.textContent = 'Record Audio';
      meterBar.style.width = '0%';

      // Synchronously generate genuine 16-bit PCM WAV blob from collected raw floats
      const sampleRate = state.audioCtx ? state.audioCtx.sampleRate : 44100;
      if (state.pcmChunks && state.pcmChunks.length > 0) {
        state.recordedBlob = exportPcmChunksToWav(state.pcmChunks, sampleRate);
        evalPlayer.src = URL.createObjectURL(state.recordedBlob);
      }

      // Cleanup stream and processor
      if (state.pcmProcessor) {
        state.pcmProcessor.disconnect();
        state.pcmProcessor = null;
      }
      if (state.micStream) {
        state.micStream.getTracks().forEach(t => t.stop());
        state.micStream = null;
      }
      if (state.audioCtx) {
        state.audioCtx.close();
        state.audioCtx = null;
      }
    }
  });
}

// --- EXECUTE CONTRASTIVE EVALUATION ---
function initExecution() {
  const execBtn = document.getElementById('executeEvalBtn');
  const spinner = document.getElementById('execSpinner');
  const results = document.getElementById('resultsSection');

  execBtn.addEventListener('click', async () => {
    const fd = new FormData();

    // 1. Reference Audio Source
    let refLabel = 'Reference Speech';
    const refUploadFile = document.getElementById('refFileInput')?.files[0];
    if (state.refMode === 'upload' || (refUploadFile && document.getElementById('tabRefUpload')?.classList.contains('active'))) {
      if (!refUploadFile) { alert('Please choose an audio file for the reference target.'); return; }
      fd.append('reference_audio', refUploadFile);
      refLabel = refUploadFile.name;
    } else {
      const refPresetVal = document.getElementById('refPresetSelect')?.value;
      if (!refPresetVal) { alert('Please select a reference preset speech.'); return; }
      fd.append('reference_preset', refPresetVal);
      refLabel = document.getElementById('refPresetSelect')?.selectedOptions[0]?.text || refPresetVal;
    }

    // 2. Evaluated Audio Source
    let evalLabel = 'Your Voice Take';
    const evalUploadFile = document.getElementById('evalFileInput')?.files[0];
    const isUploadActive = document.getElementById('tabEvalUpload')?.classList.contains('active');
    const isMicActive = document.getElementById('tabEvalMic')?.classList.contains('active');
    const evalPresetVal = document.getElementById('evalSpectrumSelect')?.value;

    if (isUploadActive) {
      if (!evalUploadFile) {
        alert('Please choose an audio file to upload, or switch to "Benchmark Takes" to practice with samples.');
        return;
      }
      fd.append('evaluated_audio', evalUploadFile);
      evalLabel = evalUploadFile.name;
    } else if (isMicActive) {
      if (state.isRecording) {
        document.getElementById('micRecordBtn')?.click();
      }
      if (!state.recordedBlob || !state.recordedBlob.size) {
        alert('Record a take first, or choose Benchmark Takes or Upload Audio.');
        return;
      }
      fd.append('evaluated_audio', state.recordedBlob, 'mic_take.wav');
      evalLabel = 'Live Microphone Take';
    } else if (evalPresetVal) {
      // The active tab determines the take source; do not silently switch modes.
      fd.append('evaluated_preset', evalPresetVal);
      evalLabel = document.getElementById('evalSpectrumSelect')?.selectedOptions[0]?.text || evalPresetVal;
    } else {
      alert('Please select a practice take or record your audio to evaluate.');
      return;
    }

    const intentVal = document.getElementById('intentSelect')?.value || 'persuasive_oratory';
    fd.append('rhetorical_intent', intentVal);
    fd.append('preferred_language', document.getElementById('languageSelect')?.value || 'English');
    fd.append('speaker_accent', document.getElementById('accentSelect')?.value || 'general_american');

    const scriptVal = document.getElementById('targetScriptInput')?.value?.trim();
    if (scriptVal) {
      fd.append('target_script', scriptVal);
    }
    fd.append('save', 'true');
    const evaluationUserId = state.currentUser?.id || null;

    // UI Feedback: smooth progress animation
    results.classList.add('hidden');
    spinner.classList.remove('hidden');
    execBtn.disabled = true;

    const bar = document.getElementById('execProgressBar');
    const title = document.getElementById('execStatusTitle');
    const step = document.getElementById('execStatusStep');
    if (bar) bar.style.width = '15%';
    if (title) title.textContent = 'Analyzing Speech Delivery...';
    if (step) step.textContent = 'Measuring vocal pitch & loudness across speech...';

    let progress = 15;
    const progressInterval = setInterval(() => {
      progress += Math.floor(Math.random() * 8) + 6;
      if (progress > 88) progress = 88;
      if (bar) bar.style.width = progress + '%';

      if (progress > 30 && progress <= 60) {
        if (title) title.textContent = 'Aligning Speech Rhythm & Cadence...';
        if (step) step.textContent = 'Comparing speech timing against benchmark baseline...';
      } else if (progress > 60) {
        if (title) title.textContent = 'Synthesizing Coaching Guidance...';
        if (step) step.textContent = 'Identifying key moments and personalized coaching tips...';
      }
    }, 500);

    try {
      const res = await fetch('/api/analyze', {
        method: 'POST',
        headers: {
          'X-SCE-Request': '1' // Anti-CSRF header
        },
        credentials: 'same-origin',
        body: fd
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Analysis failed.' }));
        throw new Error(err.detail || 'Evaluation failed.');
      }

      const data = await res.json();
      if ((state.currentUser?.id || null) !== evaluationUserId) return;
      state.lastAnalysisData = data;

      // Derive a user-friendly title from topic or script
      const topicInputVal = document.getElementById('scriptTopicInput')?.value?.trim();
      const takeTitle = topicInputVal || (scriptVal ? scriptVal.slice(0, 36) + '...' : `${evalLabel}`);

      // Save to local takes immediately for guest session persistence
      saveLocalTake({
        id: data.analysis_id || ('take_' + Date.now()),
        title: takeTitle,
        created_at: Date.now() / 1000,
        reference: refLabel,
        evaluated: evalLabel,
        intent: intentVal,
        flaw_count: data.flaw_count || 0,
        overall: data.overall_score || 0,
        data: data
      }, evaluationUserId);

      renderResults(data);
      results.classList.remove('hidden');
      results.scrollIntoView({ behavior: 'smooth' });

      loadHistory();

    } catch (err) {
      alert('Analysis Error: ' + err.message);
    } finally {
      clearInterval(progressInterval);
      if (bar) bar.style.width = '100%';
      setTimeout(() => {
        spinner.classList.add('hidden');
        execBtn.disabled = false;
      }, 200);
    }
  });
}

// --- HELPER TRANSLATORS (ZERO CODE VARIABLE JARGON) ---
function humanizeDimension(key) {
  const map = {
    'melodic_inflection': 'Pitch Expressiveness',
    'dynamic_projection': 'Vocal Projection & Energy',
    'cadence_stability': 'Speaking Rhythm & Tempo',
    'pause_architecture': 'Pauses & Breathing'
  };
  return map[key] || key.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
}

function humanizeFlawName(code, name) {
  const map = {
    'F1_MONOTONE_COLLAPSE': 'Flat Monotone Pitch',
    'F5_ENERGY_DROP': 'Volume Drop & Fading Energy',
    'F2_RUSHED_PACING': 'Rushed Speaking Tempo',
    'F3_DEAD_PAUSE': 'Hesitation / Stalled Pause'
  };
  if (code && map[code]) return map[code];
  if (name && !name.includes('_')) return name;
  return (name || code || 'Delivery Note')
    .replace(/^F\d+_/i, '')
    .replace(/_/g, ' ')
    .toLowerCase()
    .replace(/\b\w/g, c => c.toUpperCase());
}

function cleanAdvice(str) {
  if (!str) return '';
  return str.replace(/\([+-]?\d+(\.\d+)?\s*(sigma|dB|cents|st)?\)/gi, '')
            .replace(/\(\{delta\}\)/g, '')
            .replace(/\s{2,}/g, ' ')
            .trim();
}

// --- RENDER EVALUATION RESULTS ---
function renderResults(data) {
  // 1. Score & Verdict
  const sc = data.overall_score;
  const scoreEl = document.getElementById('scoreOverall');
  const verdictEl = document.getElementById('scoreVerdict');
  scoreEl.textContent = sc.toFixed(1);

  if (sc >= 90) {
    verdictEl.textContent = 'Excellent // Benchmark Aligned';
    verdictEl.style.color = 'var(--accent-green)';
  } else if (sc >= 75) {
    verdictEl.textContent = 'Good Delivery // Minor Adjustments';
    verdictEl.style.color = 'var(--accent-peach)';
  } else {
    verdictEl.textContent = 'Needs Practice // Noticeable Vocal Drift';
    verdictEl.style.color = 'var(--accent-red)';
  }

  // 2. Rubric meters
  const rubricContainer = document.getElementById('rubricMeters');
  rubricContainer.innerHTML = '';
  Object.entries(data.rubric_scores).forEach(([key, val]) => {
    const item = document.createElement('div');
    item.className = 'rubric-item';
    const cleanName = humanizeDimension(key);
    item.innerHTML = `
      <div class="rubric-item-top">
        <span>${escapeHtml(cleanName)}</span>
        <strong>${val.toFixed(1)} / 100</strong>
      </div>
      <div class="meter-shell">
        <div class="meter-fill" style="width: ${Math.min(100, Math.max(0, val))}%"></div>
      </div>
    `;
    rubricContainer.appendChild(item);
  });

  // 3. Provenance Checksum
  document.getElementById('provenanceSha').textContent = data.provenance.sha256;
  document.getElementById('provenanceMeaning').textContent = 'Authentic digital verification fingerprint for this evaluation.';

  // 4. Canvas Pitch Curve
  renderPitchCanvas(data.time_series);

  // 5. Repair Cards (Top Fixes)
  const repairContainer = document.getElementById('repairCardsContainer');
  repairContainer.innerHTML = '';
  document.getElementById('flawCountBadge').textContent = `${data.flaw_count} Detected`;

  if (data.top_fixes && data.top_fixes.length > 0) {
    data.top_fixes.forEach((fix, idx) => {
      const card = document.createElement('div');
      card.className = 'repair-card';
      const cleanTitle = humanizeFlawName('', fix.flaw_name);
      card.innerHTML = `
        <div class="repair-top">
          <strong>Tip #${idx + 1}: ${escapeHtml(cleanTitle)}</strong>
          <span class="recovery-badge">+${fix.recovery.toFixed(1)} Pts Potential</span>
        </div>
        <div class="repair-advice">${escapeHtml(cleanAdvice(fix.advice))}</div>
      `;
      repairContainer.appendChild(card);
    });
  } else {
    repairContainer.innerHTML = '<p class="hint-caption">Great delivery! Your voice closely matches the reference benchmark.</p>';
  }

  // 6. Grounded Flaws List (Interactive click to play)
  const flawsContainer = document.getElementById('flawsListContainer');
  flawsContainer.innerHTML = '';

  if (data.flaw_regions && data.flaw_regions.length > 0) {
    data.flaw_regions.forEach((f, idx) => {
      const row = document.createElement('div');
      row.className = 'flaw-row';
      row.title = 'Click to jump player to this moment';
      const cleanTitle = humanizeFlawName(f.flaw_code, f.flaw_name);
      row.innerHTML = `
        <div class="flaw-row-top">
          <span class="flaw-name-code">Moment #${idx + 1}: ${escapeHtml(cleanTitle)}</span>
          <span class="flaw-time-jump">${f.t_start.toFixed(1)}s &ndash; ${f.t_end.toFixed(1)}s &bull; Listen ▶</span>
        </div>
        <div class="flaw-row-text">${escapeHtml(cleanAdvice(f.causal_explanation))}</div>
      `;
      row.addEventListener('click', () => {
        const player = document.getElementById('evalPlayer');
        player.currentTime = Math.max(0, f.t_start - 0.2);
        player.play();
      });
      flawsContainer.appendChild(row);
    });
  } else {
    flawsContainer.innerHTML = '<p class="hint-caption">No critical delivery flaws detected across the speech.</p>';
  }

  // 7. Script vs Spoken Comparison (if target script provided)
  const scriptPanel = document.getElementById('scriptCompPanel');
  if (scriptPanel) {
    const scComp = data.script_comparison;
    if (scComp && scComp.enabled) {
      scriptPanel.classList.remove('hidden');

      // Relative Verdict Badge
      const badge = document.getElementById('scriptVerdictBadge');
      if (badge) {
        badge.textContent = scComp.verdict_relative || 'Comparable to Baseline';
        badge.className = 'delivery-verdict-pill';
        const vLower = (scComp.verdict_relative || '').toLowerCase();
        if (vLower.includes('better')) {
          badge.classList.add('better');
        } else if (vLower.includes('worse')) {
          badge.classList.add('worse');
        }
      }

      // Adherence and Fumble Statistics
      const adhEl = document.getElementById('scriptAdherenceVal');
      if (adhEl) adhEl.textContent = `${(scComp.adherence_pct || 100).toFixed(1)}%`;

      const fumEl = document.getElementById('scriptFumbleVal');
      if (fumEl) fumEl.textContent = scComp.fumble_count ?? 0;

      const headEl = document.getElementById('scriptHeadlineVal');
      if (headEl) headEl.textContent = scComp.verdict_headline || 'Delivery evaluated against target script.';

      // Target Script vs Transcribed Heard Speech
      const targetBox = document.getElementById('scriptTargetDisplay');
      if (targetBox) targetBox.textContent = scComp.target_script || '';

      const heardBox = document.getElementById('scriptHeardDisplay');
      if (heardBox) heardBox.textContent = scComp.transcription_heard || '';

      // Fumble Cards
      const fumblesWrap = document.getElementById('scriptFumblesContainer');
      const fumblesList = document.getElementById('fumbleCardsList');
      if (fumblesWrap && fumblesList) {
        if (scComp.fumbles && scComp.fumbles.length > 0) {
          fumblesWrap.classList.remove('hidden');
          fumblesList.innerHTML = scComp.fumbles.map(f => `
            <div class="fumble-card-item">
              <div class="fumble-top">
                <span>"${escapeHtml(f.phrase || '')}"</span>
                <span>${escapeHtml(f.issue || 'Stumble / hesitation')}</span>
              </div>
              <div class="fumble-advice">${escapeHtml(cleanAdvice(f.advice || ''))}</div>
            </div>
          `).join('');
        } else {
          fumblesWrap.classList.add('hidden');
          fumblesList.innerHTML = '';
        }
      }

      // Delivery Suggestions
      const suggList = document.getElementById('scriptSuggestionsList');
      if (suggList) {
        const items = scComp.suggestions || [];
        if (items.length > 0) {
          suggList.innerHTML = items.map(s => `<li>${escapeHtml(cleanAdvice(s))}</li>`).join('');
        } else {
          suggList.innerHTML = '<li>Smooth cadence maintained throughout the script delivery.</li>';
        }
      }
    } else {
      scriptPanel.classList.add('hidden');
    }
  }
}

// --- HTML5 CANVAS PITCH & MELODY CHART ---
function renderPitchCanvas(ts) {
  const canvas = document.getElementById('pitchCanvas');
  if (!canvas || !ts) return;

  requestAnimationFrame(() => {
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const parent = canvas.parentElement;
    const rect = canvas.getBoundingClientRect();
    const w = rect.width || (parent ? parent.clientWidth : 0) || 800;
    const h = 240;

    canvas.width = w * dpr;
    canvas.height = h * dpr;
    ctx.scale(dpr, dpr);

    // Background clear
    ctx.clearRect(0, 0, w, h);

  const refTimes = ts.ref_times || [];
  const refCents = ts.ref_f0_cents || [];
  const evalTimes = ts.eval_times || [];
  const evalCents = ts.eval_f0_cents || [];

  const maxTime = Math.max(
    refTimes[refTimes.length - 1] || 1,
    evalTimes[evalTimes.length - 1] || 1
  );

  let minC = Infinity, maxC = -Infinity;
  [...refCents, ...evalCents].forEach(v => {
    if (v !== null && !isNaN(v)) {
      if (v < minC) minC = v;
      if (v > maxC) maxC = v;
    }
  });

  if (minC === Infinity) { minC = 1000; maxC = 3000; }
  const cPadding = (maxC - minC) * 0.1 || 200;
  minC -= cPadding;
  maxC += cPadding;

  function timeToX(t) { return (t / maxTime) * (w - 70) + 50; }
  function centsToY(c) { return h - 30 - ((c - minC) / (maxC - minC)) * (h - 60); }

  // Draw grid lines and time markers
  ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--border-subtle').trim() || '#252c38';
  ctx.lineWidth = 1;
  ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--text-dim').trim() || '#5c6675';
  ctx.font = '10px Inter, sans-serif';

  const timeStep = Math.max(1, Math.round(maxTime / 6));
  for (let s = 0; s <= maxTime; s += timeStep) {
    const gx = timeToX(s);
    ctx.beginPath();
    ctx.moveTo(gx, 20);
    ctx.lineTo(gx, h - 30);
    ctx.stroke();
    ctx.fillText(`${s}s`, gx - 6, h - 14);
  }

  // Draw axis guidance labels
  ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--text-muted').trim() || '#8b949e';
  ctx.fillText('Higher Pitch ↑', 10, 26);
  ctx.fillText('Deeper Voice ↓', 10, h - 35);

  // Draw Reference curve (Dotted muted line)
  ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--text-dim').trim() || '#5c6675';
  ctx.lineWidth = 1.8;
  ctx.setLineDash([4, 4]);
  ctx.beginPath();
  let first = true;
  for (let i = 0; i < refTimes.length; i++) {
    const c = refCents[i];
    if (c === null || isNaN(c)) { first = true; continue; }
    const x = timeToX(refTimes[i]);
    const y = centsToY(c);
    if (first) { ctx.moveTo(x, y); first = false; } else { ctx.lineTo(x, y); }
  }
  ctx.stroke();

  // Draw Evaluation take curve (Solid theme accent)
  const accentPeach = getComputedStyle(document.documentElement).getPropertyValue('--accent-peach').trim() || '#ff9e7d';
  ctx.strokeStyle = accentPeach;
  ctx.lineWidth = 2.5;
  ctx.setLineDash([]);
  ctx.beginPath();
  first = true;
  for (let i = 0; i < evalTimes.length; i++) {
    const c = evalCents[i];
    if (c === null || isNaN(c)) { first = true; continue; }
    const x = timeToX(evalTimes[i]);
    const y = centsToY(c);
    if (first) { ctx.moveTo(x, y); first = false; } else { ctx.lineTo(x, y); }
  }
  ctx.stroke();

  // Interactive Click to Seek on Canvas
  canvas.onclick = (e) => {
    const clickX = e.offsetX;
    const timeClick = Math.max(0, Math.min(maxTime, ((clickX - 50) / (w - 70)) * maxTime));
    const player = document.getElementById('evalPlayer');
    if (player) {
      player.currentTime = timeClick;
      player.play();
    }
  };
  });
}

// --- DATASET BROWSER (PRESETS) ---
async function loadPresets() {
  try {
    const res = await fetch('/api/presets');
    if (!res.ok) return;
    const data = await res.json();
    state.datasets = data.datasets || [];

    // 1. Populate Dataset Explorer Cards
    const container = document.getElementById('datasetCardsContainer');
    if (container && state.datasets.length > 0) {
      container.innerHTML = '';
      state.datasets.forEach(ds => {
        const card = document.createElement('div');
        card.className = 'ds-card';
        card.innerHTML = `
          <div class="ds-card-title">${escapeHtml(ds.title)}</div>
          <div class="ds-card-desc">${escapeHtml(ds.source_note || ds.category)}</div>
          <ul class="ds-levels-list">
            ${ds.levels.map(lv => `
              <li class="ds-level-item">
                <span><strong>Level ${lv.level}:</strong> ${escapeHtml(lv.label)}</span>
              <button type="button" class="ds-level-btn" data-reference-path="${escapeHtml(ds.levels[0].path)}" data-evaluation-path="${escapeHtml(lv.path)}">Evaluate Take</button>
              </li>
            `).join('')}
          </ul>
        `;
        card.addEventListener('click', event => {
          const button = event.target.closest('.ds-level-btn');
          if (!button || !card.contains(button)) return;
          window.testDatasetPreset(button.dataset.referencePath, button.dataset.evaluationPath);
        });
        container.appendChild(card);
      });
    }

    // 2. Dynamically Populate Studio Dropdown Selects (Dock A and Dock B)
    const refSelect = document.getElementById('refPresetSelect');
    const evalSelect = document.getElementById('evalSpectrumSelect');
    if (refSelect && evalSelect && state.datasets.length > 0) {
      const curRefVal = refSelect.value;
      const curEvalVal = evalSelect.value;
      refSelect.innerHTML = '';
      evalSelect.innerHTML = '';

      state.datasets.forEach(ds => {
        // Reference gold option
        const goldOpt = document.createElement('option');
        goldOpt.value = ds.levels[0].path;
        goldOpt.textContent = `${ds.title} (Level 0 Gold Benchmark)`;
        refSelect.appendChild(goldOpt);

        // Spectrum takes (All levels 0 to 4)
        ds.levels.forEach(lv => {
          const evalOpt = document.createElement('option');
          evalOpt.value = lv.path;
          evalOpt.textContent = `${ds.title || 'Civic'} — Level ${lv.level}: ${lv.label}`;
          evalSelect.appendChild(evalOpt);
        });
      });

      if (curRefVal) refSelect.value = curRefVal;
      if (curEvalVal) evalSelect.value = curEvalVal;
    }

  } catch (err) {
    console.warn('Failed loading dataset presets:', err);
  }
}

// --- EVALUATE TAKE ACTION ---
window.testDatasetPreset = function(refPath, evalPath) {
  if (!refPath || !evalPath) {
    window.showToast('This library take is missing its audio selection. Refresh the Speech Library and try again.');
    return;
  }
  // 1. Switch to Studio view
  const studioNav = document.querySelector('.nav-item[data-page="studio"]') || document.querySelector('.drawer-link[data-page="studio"]');
  if (studioNav) studioNav.click();

  // 2. Configure Reference (Dock A)
  const tabRefPreset = document.getElementById('tabRefPreset');
  if (tabRefPreset) tabRefPreset.click();
  const refSelect = document.getElementById('refPresetSelect');
  if (refSelect) {
    let opt = Array.from(refSelect.options).find(o => o.value === refPath);
    if (!opt) {
      opt = document.createElement('option');
      opt.value = refPath;
      opt.textContent = refPath;
      refSelect.appendChild(opt);
    }
    refSelect.value = refPath;
  } else {
    window.showToast('The Speech Studio reference selector is unavailable. Reload the page and try again.');
    return;
  }
  const refPlayer = document.getElementById('refPlayer');
  if (refPlayer) refPlayer.src = '/' + refPath;

  // 3. Configure Evaluation Take (Dock B)
  if (typeof switchEvalMode === 'function') {
    switchEvalMode('spectrum');
  }
  const evalSelect = document.getElementById('evalSpectrumSelect');
  if (evalSelect) {
    let opt = Array.from(evalSelect.options).find(o => o.value === evalPath);
    if (!opt) {
      opt = document.createElement('option');
      opt.value = evalPath;
      opt.textContent = evalPath;
      evalSelect.appendChild(opt);
    }
    evalSelect.value = evalPath;
  } else {
    window.showToast('The Speech Studio take selector is unavailable. Reload the page and try again.');
    return;
  }
  const evalPlayer = document.getElementById('evalPlayer');
  if (evalPlayer) evalPlayer.src = '/' + evalPath;

  // Auto-populate target script if currently empty so script vs spoken comparison is demonstrated
  const scriptInput = document.getElementById('targetScriptInput');
  if (scriptInput && !scriptInput.value.trim()) {
    document.getElementById('loadScriptCivicBtn')?.click();
  }

  // 4. Trigger evaluation with brief delay to let DOM synchronize
  setTimeout(() => {
    const execBtn = document.getElementById('executeEvalBtn');
    if (!execBtn) window.showToast('The Evaluate button is unavailable. Reload the page and try again.');
    else if (execBtn.disabled) window.showToast('An evaluation is already running. Wait for it to finish before starting another.');
    else execBtn.click();
  }, 80);
};

// --- AUTHENTICATION & MODAL ---
window.openAuthModal = function(mode) {
  const modal = document.getElementById('authModal');
  const errorMsg = document.getElementById('authErrorMsg');
  const tabLogin = document.getElementById('tabModeLogin');
  const tabSignup = document.getElementById('tabModeSignup');
  const fieldName = document.getElementById('fieldDisplayName');
  const fieldTerms = document.getElementById('fieldTerms');
  const title = document.getElementById('modalTitle');
  const submitBtn = document.getElementById('authSubmitBtn');

  state.authModalMode = mode || 'login';
  if (!modal) return;
  modal.classList.remove('hidden');
  if (errorMsg) errorMsg.classList.add('hidden');
  document.getElementById('resendVerificationBtn')?.classList.add('hidden');
  document.getElementById('boxResetPassword')?.classList.add('hidden');

  if (mode === 'signup') {
    tabSignup?.classList.add('active');
    tabLogin?.classList.remove('active');
    fieldName?.classList.remove('hidden');
    fieldTerms?.classList.remove('hidden');
    if (title) title.textContent = 'Create Account';
    if (submitBtn) submitBtn.textContent = 'Create Account';
  } else {
    tabLogin?.classList.add('active');
    tabSignup?.classList.remove('active');
    fieldName?.classList.add('hidden');
    fieldTerms?.classList.add('hidden');
    if (title) title.textContent = 'Sign In';
    if (submitBtn) submitBtn.textContent = 'Sign In';
  }
};

window.closeAuthModal = function() {
  const modal = document.getElementById('authModal');
  if (modal) modal.classList.add('hidden');
  const form = document.getElementById('authForm');
  if (form) form.reset();
};

function initAuth() {
  const modal = document.getElementById('authModal');
  const modalClose = document.getElementById('modalCloseBtn');
  const tabLogin = document.getElementById('tabModeLogin');
  const tabSignup = document.getElementById('tabModeSignup');
  const form = document.getElementById('authForm');
  const submitBtn = document.getElementById('authSubmitBtn');
  const errorMsg = document.getElementById('authErrorMsg');

  tabLogin?.addEventListener('click', () => window.openAuthModal('login'));
  tabSignup?.addEventListener('click', () => window.openAuthModal('signup'));
  modalClose?.addEventListener('click', window.closeAuthModal);
  modal?.addEventListener('click', (e) => { if (e.target === modal) window.closeAuthModal(); });

  // Event delegation on document so dynamic buttons always work
  document.addEventListener('click', (e) => {
    if (e.target.closest('#navLoginBtn') || e.target.closest('#drawerLoginBtn') || e.target.closest('#historyLoginBtn')) {
      window.openAuthModal('login');
    } else if (e.target.closest('#navSignupBtn') || e.target.closest('#drawerSignupBtn')) {
      window.openAuthModal('signup');
    } else if (e.target.closest('#navLogoutBtn') || e.target.closest('#drawerLogoutBtn')) {
      window.doLogout();
    }
  });

  form?.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (errorMsg) errorMsg.classList.add('hidden');

    const email = document.getElementById('authEmail')?.value.trim();
    const password = document.getElementById('authPassword')?.value;

    const endpoint = state.authModalMode === 'signup' ? '/api/auth/signup' : '/api/auth/login';
    const body = { email, password };

    if (state.authModalMode === 'signup') {
      body.display_name = document.getElementById('authDisplayName')?.value.trim() || 'Speaker';
      body.accept_terms = document.getElementById('authAcceptTerms')?.checked;
      if (!body.accept_terms) {
        if (errorMsg) {
          errorMsg.textContent = 'Please accept the Terms of Use to proceed.';
          errorMsg.classList.remove('hidden');
        }
        return;
      }
    }

    try {
      if (submitBtn) submitBtn.disabled = true;
      const res = await fetch(endpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-SCE-Request': '1' // Anti-CSRF
        },
        credentials: 'same-origin',
        body: JSON.stringify(body)
      });

      const resData = await res.json();
      if (!res.ok) {
        throw new Error(resData.detail || 'Authentication failed. Please verify your details.');
      }

      if (resData.email_verification_required) {
        window.openAuthModal('login');
        if (errorMsg) {
          errorMsg.textContent = resData.detail;
          errorMsg.className = 'form-error auth-success-message';
        }
        return;
      }

      setCurrentUser(resData.user);
      window.closeAuthModal();
      loadHistory();

    } catch (err) {
      if (errorMsg) {
        if (state.authModalMode === 'login' && /verify your email/i.test(err.message)) {
          document.getElementById('resendVerificationBtn')?.classList.remove('hidden');
        }
        // If account already exists during signup, provide 1-click instant switch to login!
        if (state.authModalMode === 'signup' && (err.message.includes('already have one') || err.message.includes('log in instead'))) {
          errorMsg.innerHTML = `
            <div>An account with this email already exists.</div>
            <button type="button" class="switch-login-btn" id="switchToLoginBtn">Switch to Sign In &rarr;</button>
          `;
          errorMsg.classList.remove('hidden');
          document.getElementById('switchToLoginBtn')?.addEventListener('click', () => {
            const currentEmail = document.getElementById('authEmail')?.value;
            window.openAuthModal('login');
            const emailInput = document.getElementById('authEmail');
            if (emailInput && currentEmail) {
              emailInput.value = currentEmail;
              document.getElementById('authPassword')?.focus();
            }
          });
        } else {
          errorMsg.textContent = err.message;
          errorMsg.classList.remove('hidden');
        }
      }
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  });

  // --- EMAIL-BASED PASSWORD RESET ---
  const forgotBtn = document.getElementById('authForgotBtn');
  const resetBox = document.getElementById('boxResetPassword');
  const sendResetLinkBtn = document.getElementById('sendResetLinkBtn');
  const confirmResetBtn = document.getElementById('confirmResetBtn');

  forgotBtn?.addEventListener('click', (e) => {
    e.preventDefault();
    if (resetBox) resetBox.classList.toggle('hidden');
  });

  sendResetLinkBtn?.addEventListener('click', async () => {
    const email = document.getElementById('authEmail')?.value.trim();
    if (!email) {
      if (errorMsg) {
        errorMsg.textContent = 'Enter your email address first.';
        errorMsg.classList.remove('hidden');
      }
      document.getElementById('authEmail')?.focus();
      return;
    }
    try {
      sendResetLinkBtn.disabled = true;
      const res = await fetch('/api/auth/password-reset/request', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-SCE-Request': '1'
        },
        credentials: 'same-origin',
        body: JSON.stringify({ email })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Could not request a reset link.');
      const help = document.getElementById('resetPasswordHelp');
      if (help) help.textContent = data.detail;
    } catch (err) {
      if (errorMsg) {
        errorMsg.textContent = err.message;
        errorMsg.classList.remove('hidden');
      }
    } finally {
      sendResetLinkBtn.disabled = false;
    }
  });

  document.getElementById('resendVerificationBtn')?.addEventListener('click', async () => {
    const email = document.getElementById('authEmail')?.value.trim();
    if (!email) {
      if (errorMsg) { errorMsg.textContent = 'Enter your email address first.'; errorMsg.classList.remove('hidden'); }
      document.getElementById('authEmail')?.focus();
      return;
    }
    try {
      const res = await fetch('/api/auth/verification/resend', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-SCE-Request': '1' },
        credentials: 'same-origin', body: JSON.stringify({ email })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Could not resend the verification link.');
      if (errorMsg) { errorMsg.textContent = data.detail; errorMsg.className = 'form-error auth-success-message'; }
    } catch (err) {
      if (errorMsg) { errorMsg.textContent = err.message; errorMsg.classList.remove('hidden'); }
    }
  });

  // --- SEPARATE MODAL HANDLERS: TERMS, PRIVACY, ABOUT & DIRECT CONTACT ---
  const termsModal = document.getElementById('termsModal');
  const termsClose = document.getElementById('termsModalCloseBtn');
  const termsClose2 = document.getElementById('termsModalCloseBtn2');
  const termsLink = document.getElementById('authTermsLink');
  const footerTerms = document.getElementById('footerTermsBtn');

  const privacyModal = document.getElementById('privacyModal');
  const privacyClose = document.getElementById('privacyModalCloseBtn');
  const privacyDone = document.getElementById('privacyModalDoneBtn');
  const privacyLink = document.getElementById('authPrivacyLink');
  const footerPrivacy = document.getElementById('footerPrivacyBtn');

  const aboutModal = document.getElementById('aboutModal');
  const aboutClose = document.getElementById('aboutModalCloseBtn');
  const aboutDone = document.getElementById('aboutModalDoneBtn');
  const footerAbout = document.getElementById('footerAboutBtn');

  const contactModal = document.getElementById('contactModal');
  const contactClose = document.getElementById('contactModalCloseBtn');
  const contactDone = document.getElementById('contactModalDoneBtn');
  const contactSubmitBtn = document.getElementById('contactSubmitBtn');
  const contactDirectMessage = document.getElementById('contactDirectMessage');
  const contactStatusBox = document.getElementById('contactStatusBox');
  const footerContact = document.getElementById('footerContactBtn');

  function openTerms() { if (termsModal) termsModal.classList.remove('hidden'); }
  function closeTerms() { if (termsModal) termsModal.classList.add('hidden'); }

  function openPrivacy() { if (privacyModal) privacyModal.classList.remove('hidden'); }
  function closePrivacy() { if (privacyModal) privacyModal.classList.add('hidden'); }

  function openAbout() { if (aboutModal) aboutModal.classList.remove('hidden'); }
  function closeAbout() { if (aboutModal) aboutModal.classList.add('hidden'); }

  function openContact() {
    if (contactModal) {
      contactModal.classList.remove('hidden');
      const senderName = document.getElementById('contactSenderName');
      const senderEmail = document.getElementById('contactSenderEmail');
      if (state.currentUser) {
        if (senderName && !senderName.value) senderName.value = state.currentUser.display_name || '';
        if (senderEmail && !senderEmail.value) senderEmail.value = state.currentUser.email || '';
      }
      if (contactStatusBox) {
        contactStatusBox.classList.remove('contact-status-error', 'contact-status-success');
        contactStatusBox.textContent = 'Checking message delivery…';
        contactStatusBox.classList.remove('hidden');
      }
      if (contactSubmitBtn) {
        contactSubmitBtn.disabled = true;
        contactSubmitBtn.textContent = 'Checking…';
      }
      fetchPublicConfig()
        .then(configData => {
          if (!configData.contact_enabled) {
            if (contactStatusBox) {
              contactStatusBox.textContent = 'Contact messages are not available until Firestore is configured.';
              contactStatusBox.classList.remove('hidden');
            }
            if (contactSubmitBtn) contactSubmitBtn.textContent = 'Unavailable';
            return;
          }
          if (contactStatusBox) contactStatusBox.classList.add('hidden');
          if (contactSubmitBtn) {
            contactSubmitBtn.disabled = false;
            contactSubmitBtn.textContent = 'Send Message';
          }
        })
        .catch(() => {
          if (contactStatusBox) {
            contactStatusBox.textContent = 'Message delivery status could not be checked. Please try again later.';
            contactStatusBox.classList.remove('hidden');
          }
          if (contactSubmitBtn) contactSubmitBtn.textContent = 'Unavailable';
        });
    }
  }
  function closeContact() { if (contactModal) contactModal.classList.add('hidden'); }

  // Terms Modal Listeners
  termsLink?.addEventListener('click', (e) => { e.preventDefault(); openTerms(); });
  footerTerms?.addEventListener('click', openTerms);
  termsClose?.addEventListener('click', closeTerms);
  termsClose2?.addEventListener('click', () => {
    const termsCheck = document.getElementById('authAcceptTerms');
    if (termsCheck) termsCheck.checked = true;
    closeTerms();
  });
  termsModal?.addEventListener('click', (e) => { if (e.target === termsModal) closeTerms(); });

  // Privacy Modal Listeners
  privacyLink?.addEventListener('click', (e) => { e.preventDefault(); openPrivacy(); });
  footerPrivacy?.addEventListener('click', openPrivacy);
  privacyClose?.addEventListener('click', closePrivacy);
  privacyDone?.addEventListener('click', closePrivacy);
  privacyModal?.addEventListener('click', (e) => { if (e.target === privacyModal) closePrivacy(); });

  // About Modal Listeners
  footerAbout?.addEventListener('click', openAbout);
  aboutClose?.addEventListener('click', closeAbout);
  aboutDone?.addEventListener('click', closeAbout);
  aboutModal?.addEventListener('click', (e) => { if (e.target === aboutModal) closeAbout(); });

  // Contact Modal Listeners (Direct Message — No public email disclosed)
  footerContact?.addEventListener('click', openContact);
  contactClose?.addEventListener('click', closeContact);
  contactDone?.addEventListener('click', closeContact);
  contactModal?.addEventListener('click', (e) => { if (e.target === contactModal) closeContact(); });

  const contactReplyFields = document.getElementById('contactReplyFields');
  const contactReplyEmail = document.getElementById('contactSenderEmail');
  const contactReplyName = document.getElementById('contactSenderName');
  const replyChoices = [...document.querySelectorAll('input[name="contactReplyMode"]')];
  function updateContactReplyMode() {
    const wantsReply = document.querySelector('input[name="contactReplyMode"]:checked')?.value === 'reply';
    if (contactReplyFields) {
      contactReplyFields.hidden = !wantsReply;
      contactReplyFields.classList.toggle('hidden', !wantsReply);
    }
    if (contactReplyEmail) contactReplyEmail.required = wantsReply;
    if (wantsReply && state.currentUser) {
      if (contactReplyEmail && !contactReplyEmail.value) contactReplyEmail.value = state.currentUser.email || '';
      if (contactReplyName && !contactReplyName.value) contactReplyName.value = state.currentUser.display_name || '';
    }
  }
  replyChoices.forEach(choice => choice.addEventListener('change', updateContactReplyMode));
  updateContactReplyMode();

  contactSubmitBtn?.addEventListener('click', async () => {
    const msg = contactDirectMessage?.value.trim();
    const wantsReply = document.querySelector('input[name="contactReplyMode"]:checked')?.value === 'reply';
    if (wantsReply && contactReplyEmail && !contactReplyEmail.reportValidity()) return;
    if (!msg) {
      if (contactStatusBox) {
        contactStatusBox.classList.remove('contact-status-success');
        contactStatusBox.classList.add('contact-status-error');
        contactStatusBox.textContent = 'Please enter your message before sending.';
        contactStatusBox.classList.remove('hidden');
      }
      contactDirectMessage?.focus();
      return;
    }

    contactSubmitBtn.disabled = true;
    contactSubmitBtn.textContent = 'Sending…';
    try {
      const res = await fetch('/api/contact', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-SCE-Request': '1' },
        credentials: 'same-origin', body: JSON.stringify({
          name: wantsReply ? (contactReplyName?.value.trim() || '') : '',
          email: wantsReply ? (contactReplyEmail?.value.trim() || '') : '',
          category: document.getElementById('contactCategorySelect')?.value || 'general',
          message: msg
        })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'The message could not be sent.');
      if (contactStatusBox) {
        contactStatusBox.classList.remove('contact-status-error');
        contactStatusBox.classList.add('contact-status-success');
        contactStatusBox.textContent = data.detail;
        contactStatusBox.classList.remove('hidden');
      }
      if (contactDirectMessage) contactDirectMessage.value = '';
      showToast(data.detail);
    } catch (err) {
      if (contactStatusBox) {
        contactStatusBox.classList.remove('contact-status-success');
        contactStatusBox.classList.add('contact-status-error');
        contactStatusBox.textContent = err.message;
        contactStatusBox.classList.remove('hidden');
      }
    } finally {
      contactSubmitBtn.disabled = false;
      contactSubmitBtn.textContent = 'Send Message';
    }
  });
}

let pendingPasswordResetToken = null;

function setAuthMessage(message, success = false) {
  const box = document.getElementById('authErrorMsg');
  if (!box) return;
  box.textContent = message;
  box.classList.remove('hidden');
  box.classList.toggle('auth-success-message', success);
}

function initEmailFlows() {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ''));
  const verifyToken = params.get('verify_email');
  const resetToken = params.get('reset_password');
  if (!verifyToken && !resetToken) return;

  window.history.replaceState({}, document.title, `${window.location.pathname}${window.location.search}`);

  if (verifyToken) {
    fetch('/api/auth/verification/confirm', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-SCE-Request': '1' },
      credentials: 'same-origin', body: JSON.stringify({ token: verifyToken })
    }).then(async res => {
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Email verification failed.');
      window.openAuthModal('login');
      setAuthMessage(data.detail, true);
    }).catch(err => {
      window.openAuthModal('login');
      setAuthMessage(err.message);
    });
  }

  if (resetToken) {
    pendingPasswordResetToken = resetToken;
    window.openAuthModal('login');
    document.getElementById('boxResetPassword')?.classList.remove('hidden');
    document.getElementById('resetRequestFields')?.classList.add('hidden');
    document.getElementById('resetApplyFields')?.classList.remove('hidden');
    const help = document.getElementById('resetPasswordHelp');
    if (help) help.textContent = 'Choose a new password for your account.';
    document.getElementById('authNewPassword')?.focus();
  }

  document.getElementById('confirmResetBtn')?.addEventListener('click', async () => {
    const newPassword = document.getElementById('authNewPassword')?.value || '';
    if (!pendingPasswordResetToken) {
      setAuthMessage('Open the password reset link from your email to continue.');
      return;
    }
    if (newPassword.length < 8) {
      setAuthMessage('Use a password with at least 8 characters.');
      return;
    }
    const button = document.getElementById('confirmResetBtn');
    button.disabled = true;
    try {
      const res = await fetch('/api/auth/reset-password', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-SCE-Request': '1' },
        credentials: 'same-origin', body: JSON.stringify({ token: pendingPasswordResetToken, new_password: newPassword })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Password reset failed.');
      pendingPasswordResetToken = null;
      document.getElementById('boxResetPassword')?.classList.add('hidden');
      document.getElementById('resetRequestFields')?.classList.remove('hidden');
      document.getElementById('resetApplyFields')?.classList.add('hidden');
      window.openAuthModal('login');
      setAuthMessage(data.detail, true);
    } catch (err) {
      setAuthMessage(err.message);
    } finally {
      button.disabled = false;
    }
  });
}

// Global Toast Banner
window.showToast = function(msg, duration = 2800) {
  const toast = document.getElementById('appToast');
  if (!toast) return;
  toast.textContent = msg;
  toast.classList.remove('hidden');
  clearTimeout(window._toastTimer);
  window._toastTimer = setTimeout(() => {
    toast.classList.add('hidden');
  }, duration);
};

async function checkAuthSession() {
  const revision = authStateRevision;
  try {
    const res = await fetch('/api/auth/me', { credentials: 'same-origin' });
    if (revision !== authStateRevision || !res.ok) return;
    const data = await res.json();
    if (revision !== authStateRevision) return;
    setCurrentUser(data.user);
  } catch (err) {
    console.log('Session check in guest mode');
  }
}

function updateAuthUI(user) {
  const headerWidget = document.getElementById('authHeaderWidget');
  const drawerUserName = document.getElementById('drawerUserName');
  const drawerAuthActions = document.getElementById('drawerAuthActions');
  const historyNotice = document.getElementById('historyAuthNotice');

  if (user) {
    if (headerWidget) {
      headerWidget.innerHTML = `
        <span class="auth-user-name" title="${escapeHtml(user.display_name)}">${escapeHtml(user.display_name)}</span>
        <button type="button" class="btn-subtle" id="navLogoutBtn">Sign Out</button>
      `;
    }
    if (drawerUserName) drawerUserName.textContent = user.display_name || user.email;
    if (drawerAuthActions) {
      drawerAuthActions.innerHTML = `
        <button type="button" class="btn-clean" id="drawerLogoutBtn">Sign Out (${escapeHtml(user.email)})</button>
      `;
    }
    if (historyNotice) historyNotice.classList.add('hidden');
  } else {
    if (headerWidget) {
      headerWidget.innerHTML = `
        <button type="button" class="btn-subtle" id="navLoginBtn">Log In</button>
        <button type="button" class="btn-prime" id="navSignupBtn">Sign Up</button>
      `;
    }
    if (drawerUserName) drawerUserName.textContent = 'Guest Speaker';
    if (drawerAuthActions) {
      drawerAuthActions.innerHTML = `
        <button type="button" class="btn-clean" id="drawerLoginBtn">Sign In</button>
        <button type="button" class="btn-clean" id="drawerSignupBtn">Create Account</button>
      `;
    }
    if (historyNotice) historyNotice.classList.remove('hidden');
  }
}

let isLoggingOut = false;
window.doLogout = async function() {
  if (isLoggingOut) return;
  isLoggingOut = true;

  try {
    const response = await fetch('/api/auth/logout', {
      method: 'POST',
      headers: { 'X-SCE-Request': '1' },
      credentials: 'same-origin'
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || 'The server could not end this session. Please retry.');
    }

    setCurrentUser(null);
    const drawer = document.getElementById('sideDrawer');
    const overlay = document.getElementById('drawerOverlay');
    if (drawer) {
      drawer.classList.remove('open');
      drawer.setAttribute('aria-hidden', 'true');
    }
    if (overlay) overlay.classList.add('hidden');
    await loadHistory();
    showToast('Signed out successfully. Switched to Guest mode.');
  } catch (err) {
    console.warn('Logout failed', err);
    showToast(err.message || 'Could not sign out. Please retry.');
  } finally {
    isLoggingOut = false;
  }
};

// --- ACCOUNT-SCOPED LOCAL & CLOUD HISTORY STORAGE ---
const volatileLocalHistory = new Map();
let historyLoadRevision = 0;

function localHistoryStorageKey(userId = state.currentUser?.id || null) {
  return userId
    ? `sce_local_takes_v2:user:${encodeURIComponent(userId)}`
    : 'sce_local_takes_v2:guest';
}

function getLocalHistory(userId = state.currentUser?.id || null) {
  const key = localHistoryStorageKey(userId);
  try {
    const fallback = JSON.stringify(volatileLocalHistory.get(key) || []);
    const parsed = JSON.parse(readStoredValue(key, fallback) || '[]');
    const takes = Array.isArray(parsed) ? parsed : [];
    volatileLocalHistory.set(key, takes);
    return takes;
  } catch (e) {
    return volatileLocalHistory.get(key) || [];
  }
}

function saveLocalTake(take, userId = state.currentUser?.id || null) {
  const key = localHistoryStorageKey(userId);
  const list = [take, ...getLocalHistory(userId)].slice(0, 50);
  volatileLocalHistory.set(key, list);
  writeStoredValue(key, JSON.stringify(list));
}

function clearLocalHistory() {
  const key = localHistoryStorageKey();
  volatileLocalHistory.set(key, []);
  removeStoredValue(key);
  loadHistory();
}

async function loadHistory() {
  const tbody = document.getElementById('historyTableBody');
  if (!tbody) return;

  const revision = ++historyLoadRevision;
  const userId = state.currentUser?.id || null;
  const isCurrentLoad = () => revision === historyLoadRevision && (state.currentUser?.id || null) === userId;
  const localTakes = getLocalHistory();

  if (userId) {
    try {
      const res = await fetch('/api/history', { credentials: 'same-origin' });
      if (!isCurrentLoad()) return;
      if (res.ok) {
        const data = await res.json();
        if (!isCurrentLoad()) return;
        const serverTakes = data.items || [];
        const combined = [...serverTakes, ...localTakes.filter(lt => !serverTakes.some(st => st.id === lt.id))];
        state.historyItems = combined;

        if (combined.length === 0) {
          tbody.innerHTML = '<tr><td colspan="7" class="table-empty">No speech evaluations recorded yet. Run a take in the Studio to view your scores here!</td></tr>';
          return;
        }

        renderHistoryRows(tbody, combined);
        return;
      }
      if (res.status === 401) {
        setCurrentUser(null);
      }
    } catch (err) {
      if (!isCurrentLoad()) return;
      console.warn('Failed loading server history, falling back to local', err);
    }
  }

  if (revision !== historyLoadRevision) return;
  const guestTakes = state.currentUser ? localTakes : getLocalHistory();

  // Guest mode or offline fallback: display local takes
  if (guestTakes.length === 0) {
    state.historyItems = [];
    tbody.innerHTML = '<tr><td colspan="7" class="table-empty">No speech evaluations recorded yet in this session. Try a demo or record your take in the Studio!</td></tr>';
    return;
  }

  state.historyItems = guestTakes;
  renderHistoryRows(tbody, guestTakes);
}

function renderHistoryRows(tbody, items) {
  tbody.innerHTML = items.map(it => {
    const d = new Date((it.created_at || Date.now() / 1000) * 1000).toLocaleString();
    const scoreColor = it.overall >= 90 ? 'var(--accent-green)' : (it.overall >= 75 ? 'var(--accent-peach)' : 'var(--accent-red)');
    const title = it.title || it.intent || 'Speech Practice Take';
    return `
      <tr>
        <td>${escapeHtml(d)}</td>
        <td><strong>${escapeHtml(title)}</strong></td>
        <td>${escapeHtml(it.reference || 'Benchmark')}</td>
        <td>${escapeHtml(it.evaluated || 'Voice Take')}</td>
        <td>${it.flaw_count || 0}</td>
        <td><strong style="color: ${scoreColor};">${(it.overall || 0).toFixed(1)}</strong></td>
        <td>
          <div style="display: flex; gap: 4px;">
            <button type="button" class="btn-subtle" data-history-action="view" data-history-id="${escapeHtml(it.id)}" style="padding: 2px 8px; color: var(--accent-peach);">View Score</button>
            <button type="button" class="btn-subtle" data-history-action="delete" data-history-id="${escapeHtml(it.id)}" style="padding: 2px 8px;" aria-label="Delete saved take">&times;</button>
          </div>
        </td>
      </tr>
    `;
  }).join('');
}

function showSavedScore(item) {
  const modal = document.getElementById('historyDetailModal');
  const title = document.getElementById('historyDetailTitle');
  const details = document.getElementById('historyDetailBody');
  if (!modal || !title || !details) return;
  const score = Number(item.overall);
  title.textContent = item.title || item.intent || 'Saved speech score';
  const rubric = item.rubric && typeof item.rubric === 'object' ? item.rubric : {};
  const rubricHtml = Object.entries(rubric).map(([key, value]) => {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return '';
    return `<div class="saved-rubric-row"><span>${escapeHtml(humanizeDimension(key))}</span><strong>${numeric.toFixed(1)} / 100</strong></div>`;
  }).join('');
  details.innerHTML = `
    <p class="saved-score-value">${Number.isFinite(score) ? score.toFixed(1) : '—'}<span> / 100</span></p>
    <p>${escapeHtml(item.reference || 'Benchmark')} · ${escapeHtml(item.evaluated || 'Voice take')}</p>
    <p>${Number(item.flaw_count) || 0} delivery points · ${escapeHtml(new Date((item.created_at || Date.now() / 1000) * 1000).toLocaleString())}</p>
    <div class="saved-rubric-list">${rubricHtml || '<p>Detailed rubric scores are not available for this take.</p>'}</div>
    <p class="hint-caption">This saved entry contains the score summary. Original audio is not retained.</p>
  `;
  modal.classList.remove('hidden');
}

window.reopenHistoryTake = function(id) {
  const localTakes = getLocalHistory();
  const found = localTakes.find(t => t.id === id);
  if (found && found.data) {
    state.lastAnalysisData = found.data;
    renderResults(found.data);
    const studioNav = document.querySelector('.nav-item[data-page="studio"]') || document.querySelector('.drawer-link[data-page="studio"]');
    if (studioNav) studioNav.click();
    const results = document.getElementById('resultsSection');
    if (results) {
      results.classList.remove('hidden');
      results.scrollIntoView({ behavior: 'smooth' });
    }
  } else {
    const savedItem = state.historyItems.find(t => t.id === id);
    if (savedItem) showSavedScore(savedItem);
  }
};

function initHistoryDetails() {
  const modal = document.getElementById('historyDetailModal');
  const close = document.getElementById('historyDetailCloseBtn');
  const tbody = document.getElementById('historyTableBody');
  close?.addEventListener('click', () => modal?.classList.add('hidden'));
  modal?.addEventListener('click', e => { if (e.target === modal) modal.classList.add('hidden'); });
  tbody?.addEventListener('click', e => {
    const button = e.target.closest('[data-history-action]');
    if (!button) return;
    const { historyAction, historyId } = button.dataset;
    if (historyAction === 'view') window.reopenHistoryTake(historyId);
    if (historyAction === 'delete') window.deleteHistoryItem(historyId);
  });
}

window.deleteHistoryItem = async function(id) {
  // Remove from local takes
  try {
    const list = getLocalHistory().filter(it => it.id !== id);
    volatileLocalHistory = list;
    writeStoredValue('sce_local_takes', JSON.stringify(list));
  } catch (e) {
    console.warn(e);
  }

  // If logged in, also delete from server
  if (state.currentUser && !id.startsWith('take_')) {
    try {
      await fetch(`/api/history/${id}`, {
        method: 'DELETE',
        headers: { 'X-SCE-Request': '1' },
        credentials: 'same-origin'
      });
    } catch (err) {
      console.warn(err);
    }
  }

  loadHistory();
};


// --- SECURITY UTILITIES ---
function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
