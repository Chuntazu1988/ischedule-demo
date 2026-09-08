const HEBREW_LETTERS = ['א', 'ב', 'ג', 'ד', 'ה', 'ו', 'ז', 'ח', 'ט', 'י', 'כ', 'ל', 'מ', 'נ', 'ס', 'ע', 'פ', 'צ', 'ק', 'ר', 'ש', 'ת'];
const STORAGE_KEY = 'electricitySplitApp.previousReadings';

const metersList = document.getElementById('metersList');
const rowTemplate = document.getElementById('meterRowTemplate');
const addMeterBtn = document.getElementById('addMeterBtn');
const calcBtn = document.getElementById('calcBtn');
const totalAmountInput = document.getElementById('totalAmount');
const resultsSection = document.getElementById('resultsSection');
const resultsBody = document.getElementById('resultsBody');
const resultsTotal = document.getElementById('resultsTotal');

function loadSavedReadings() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {};
  } catch (err) {
    return {};
  }
}

function saveReadings(map) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  } catch (err) {
    // ignore - persistence is a convenience, not a requirement
  }
}

function renumberBadges() {
  const rows = metersList.querySelectorAll('.meter-row');
  rows.forEach((row, i) => {
    row.querySelector('.meter-badge').textContent = HEBREW_LETTERS[i] || String(i + 1);
  });
}

function updateConsumptionDisplay(row) {
  const prev = parseFloat(row.querySelector('.meter-prev').value);
  const curr = parseFloat(row.querySelector('.meter-curr').value);
  const display = row.querySelector('.consumption-display');
  const valueEl = display.querySelector('.consumption-value');

  if (isNaN(prev) || isNaN(curr)) {
    valueEl.textContent = '0';
    display.classList.remove('invalid');
    return;
  }

  const consumption = curr - prev;
  if (consumption < 0) {
    valueEl.textContent = 'שגיאה - בדקו קריאות';
    display.classList.add('invalid');
  } else {
    valueEl.textContent = consumption.toFixed(2).replace(/\.00$/, '');
    display.classList.remove('invalid');
  }
}

// --- Crop tool -------------------------------------------------------

const MIN_CROP_SIZE = 24;

function setupCropper(row) {
  const frame = row.querySelector('.cropper-frame');
  const box = row.querySelector('.crop-box');
  let drag = null;

  function frameSize() {
    const rect = frame.getBoundingClientRect();
    return { width: rect.width, height: rect.height };
  }

  function setBox(left, top, width, height) {
    const { width: fw, height: fh } = frameSize();
    width = Math.max(MIN_CROP_SIZE, Math.min(width, fw));
    height = Math.max(MIN_CROP_SIZE, Math.min(height, fh));
    left = Math.max(0, Math.min(left, fw - width));
    top = Math.max(0, Math.min(top, fh - height));
    box.style.left = left + 'px';
    box.style.top = top + 'px';
    box.style.width = width + 'px';
    box.style.height = height + 'px';
  }

  function currentBox() {
    return {
      left: parseFloat(box.style.left) || 0,
      top: parseFloat(box.style.top) || 0,
      width: parseFloat(box.style.width) || 0,
      height: parseFloat(box.style.height) || 0,
    };
  }

  function resetBox() {
    const { width: fw, height: fh } = frameSize();
    setBox(fw * 0.15, fh * 0.38, fw * 0.7, fh * 0.24);
  }

  function pointerPos(e) {
    const rect = frame.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  function onPointerDown(handle, e) {
    e.preventDefault();
    e.stopPropagation();
    frame.setPointerCapture && frame.setPointerCapture(e.pointerId);
    drag = { handle, start: pointerPos(e), box: currentBox() };
  }

  box.addEventListener('pointerdown', (e) => onPointerDown('move', e));
  row.querySelectorAll('.crop-handle').forEach(h => {
    h.addEventListener('pointerdown', (e) => onPointerDown(h.dataset.handle, e));
  });

  frame.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const pos = pointerPos(e);
    const dx = pos.x - drag.start.x;
    const dy = pos.y - drag.start.y;
    const b = drag.box;

    if (drag.handle === 'move') {
      setBox(b.left + dx, b.top + dy, b.width, b.height);
    } else {
      let { left, top, width, height } = b;
      if (drag.handle.includes('l')) { left = b.left + dx; width = b.width - dx; }
      if (drag.handle.includes('r')) { width = b.width + dx; }
      if (drag.handle.includes('t')) { top = b.top + dy; height = b.height - dy; }
      if (drag.handle.includes('b')) { height = b.height + dy; }
      setBox(left, top, width, height);
    }
  });

  ['pointerup', 'pointercancel'].forEach(evt => {
    frame.addEventListener(evt, () => { drag = null; });
  });

  row._cropApi = { resetBox, currentBox, frameSize };
}

function preprocessCanvasForOcr(canvas) {
  const ctx = canvas.getContext('2d');
  const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
  const d = imageData.data;
  const pixelCount = d.length / 4;
  const gray = new Uint8ClampedArray(pixelCount);
  const histogram = new Array(256).fill(0);

  for (let i = 0; i < d.length; i += 4) {
    const v = Math.round(0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2]);
    gray[i / 4] = v;
    histogram[v]++;
  }

  // Stretch contrast using the 2nd/98th percentile so a few glare or
  // shadow pixels don't skew the whole range (plain min/max would).
  const lo = percentileValue(histogram, pixelCount, 0.02);
  const hi = percentileValue(histogram, pixelCount, 0.98);
  const range = Math.max(1, hi - lo);

  for (let i = 0; i < d.length; i += 4) {
    const v = Math.max(0, Math.min(255, Math.round(((gray[i / 4] - lo) / range) * 255)));
    d[i] = d[i + 1] = d[i + 2] = v;
  }

  ctx.putImageData(imageData, 0, 0);
}

function percentileValue(histogram, total, fraction) {
  const target = total * fraction;
  let cumulative = 0;
  for (let v = 0; v < 256; v++) {
    cumulative += histogram[v];
    if (cumulative >= target) return v;
  }
  return 255;
}

async function runCropOcr(row) {
  const img = row.querySelector('.crop-img');
  const canvas = row.querySelector('.crop-canvas');
  const status = row.querySelector('.ocr-status');
  const currInput = row.querySelector('.meter-curr');
  const { frameSize, currentBox } = row._cropApi;

  const rendered = frameSize();
  const scaleX = img.naturalWidth / rendered.width;
  const scaleY = img.naturalHeight / rendered.height;
  const b = currentBox();

  const sx = b.left * scaleX;
  const sy = b.top * scaleY;
  const sw = b.width * scaleX;
  const sh = b.height * scaleY;

  const upscale = Math.min(6, Math.max(1, 320 / sh));
  canvas.width = Math.round(sw * upscale);
  canvas.height = Math.round(sh * upscale);
  const ctx = canvas.getContext('2d');
  ctx.drawImage(img, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
  preprocessCanvasForOcr(canvas);

  row.querySelector('.cropper').hidden = true;
  status.textContent = 'מזהה מספרים בחיתוך...';

  try {
    const { data } = await Tesseract.recognize(canvas, 'eng', {
      tessedit_char_whitelist: '0123456789.',
    });
    const digits = (data.text.match(/[\d.]+/g) || []).join('');
    if (digits) {
      currInput.value = digits;
      updateConsumptionDisplay(row);
      status.textContent = `זוהה: ${digits} - בדקו ותקנו אם צריך`;
    } else {
      status.textContent = 'לא הצלחתי לזהות מספר בחיתוך - נא להקליד ידנית.';
    }
  } catch (err) {
    status.textContent = 'זיהוי אוטומטי נכשל - נא להקליד ידנית.';
  }
}

// --- Meter rows --------------------------------------------------------

function addMeterRow(name = '', prev = '', curr = '') {
  const row = rowTemplate.content.firstElementChild.cloneNode(true);

  const nameInput = row.querySelector('.meter-name');
  const prevInput = row.querySelector('.meter-prev');
  const currInput = row.querySelector('.meter-curr');

  nameInput.value = name;
  prevInput.value = prev;
  currInput.value = curr;

  nameInput.addEventListener('change', () => {
    if (prevInput.value) return;
    const saved = loadSavedReadings();
    const key = nameInput.value.trim();
    if (key && saved[key] !== undefined) {
      prevInput.value = saved[key];
      updateConsumptionDisplay(row);
    }
  });

  [prevInput, currInput].forEach(input => {
    input.addEventListener('input', () => updateConsumptionDisplay(row));
  });

  row.querySelector('.remove-btn').addEventListener('click', () => {
    row.remove();
    renumberBadges();
  });

  setupCropper(row);

  const photoInput = row.querySelector('.meter-photo');
  const cropperEl = row.querySelector('.cropper');
  const cropImg = row.querySelector('.crop-img');
  const status = row.querySelector('.ocr-status');

  photoInput.addEventListener('change', () => {
    const file = photoInput.files[0];
    if (!file) return;

    status.textContent = '';
    cropImg.onload = () => {
      cropperEl.hidden = false;
      row._cropApi.resetBox();
    };
    cropImg.src = URL.createObjectURL(file);
  });

  row.querySelector('.crop-confirm').addEventListener('click', () => runCropOcr(row));
  row.querySelector('.crop-cancel').addEventListener('click', () => {
    cropperEl.hidden = true;
  });

  metersList.appendChild(row);
  renumberBadges();
  updateConsumptionDisplay(row);
}

function calculateSplit() {
  const totalAmount = parseFloat(totalAmountInput.value);
  const rows = Array.from(metersList.querySelectorAll('.meter-row'));

  if (!totalAmount || totalAmount <= 0) {
    alert('נא להזין סכום חשבון תקין.');
    return;
  }

  const meters = rows.map((row, i) => {
    const name = row.querySelector('.meter-name').value.trim() || `דירה ${i + 1}`;
    const prev = parseFloat(row.querySelector('.meter-prev').value);
    const curr = parseFloat(row.querySelector('.meter-curr').value);
    return { name, prev, curr, consumption: curr - prev };
  }).filter(m => !isNaN(m.prev) && !isNaN(m.curr) && m.consumption >= 0);

  if (meters.length === 0) {
    alert('נא להוסיף לפחות דירה אחת עם קריאה קודמת ונוכחית תקינות (נוכחית >= קודמת).');
    return;
  }

  const sumConsumption = meters.reduce((sum, m) => sum + m.consumption, 0);
  if (sumConsumption === 0) {
    alert('סך הצריכה של כל הדירות הוא 0 - לא ניתן לחשב חלוקה.');
    return;
  }

  resultsBody.innerHTML = '';
  let paidSoFar = 0;

  meters.forEach((m, i) => {
    const share = m.consumption / sumConsumption;
    const isLast = i === meters.length - 1;
    const amount = isLast ? totalAmount - paidSoFar : Math.round(share * totalAmount * 100) / 100;
    paidSoFar += amount;

    const row = document.createElement('div');
    row.className = 'results-row';
    row.setAttribute('role', 'row');
    row.innerHTML = `
      <span role="cell">${m.name}</span>
      <span role="cell" class="num">${m.consumption.toFixed(2).replace(/\.00$/, '')}</span>
      <span role="cell" class="num">${(share * 100).toFixed(1)}%</span>
      <span role="cell" class="pay">${amount.toFixed(2)} ₪</span>
    `;
    resultsBody.appendChild(row);
  });

  resultsTotal.textContent = `${paidSoFar.toFixed(2)} ₪`;
  resultsSection.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

  const saved = loadSavedReadings();
  meters.forEach(m => { saved[m.name] = m.curr; });
  saveReadings(saved);
}

addMeterBtn.addEventListener('click', () => addMeterRow());
calcBtn.addEventListener('click', calculateSplit);

addMeterRow('דירה 1 (לדוגמה)', 27583.14, 27700.00);
addMeterRow('דירה 2 (לדוגמה)', 38509.30, 38564.16);
calculateSplit();
