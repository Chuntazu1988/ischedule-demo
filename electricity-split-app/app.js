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

  const photoInput = row.querySelector('.meter-photo');
  const preview = row.querySelector('.preview');
  const status = row.querySelector('.ocr-status');

  photoInput.addEventListener('change', async () => {
    const file = photoInput.files[0];
    if (!file) return;

    preview.src = URL.createObjectURL(file);
    preview.hidden = false;

    status.textContent = 'מזהה מספרים בתמונה...';
    try {
      const digits = await recognizeReading(file);
      if (digits) {
        currInput.value = digits;
        updateConsumptionDisplay(row);
        status.textContent = `זוהה: ${digits} - בדקו ותקנו אם צריך`;
      } else {
        status.textContent = 'לא הצלחתי לזהות מספר - נא להקליד ידנית.';
      }
    } catch (err) {
      status.textContent = 'זיהוי אוטומטי נכשל - נא להקליד ידנית.';
    }
  });

  metersList.appendChild(row);
  renumberBadges();
  updateConsumptionDisplay(row);
}

async function recognizeReading(file) {
  const { data } = await Tesseract.recognize(file, 'eng', {
    tessedit_char_whitelist: '0123456789',
  });
  const digits = (data.text.match(/\d+/g) || []).join('');
  return digits || null;
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
