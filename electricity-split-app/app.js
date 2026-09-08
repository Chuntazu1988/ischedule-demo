const HEBREW_LETTERS = ['א', 'ב', 'ג', 'ד', 'ה', 'ו', 'ז', 'ח', 'ט', 'י', 'כ', 'ל', 'מ', 'נ', 'ס', 'ע', 'פ', 'צ', 'ק', 'ר', 'ש', 'ת'];

const metersList = document.getElementById('metersList');
const rowTemplate = document.getElementById('meterRowTemplate');
const addMeterBtn = document.getElementById('addMeterBtn');
const calcBtn = document.getElementById('calcBtn');
const totalAmountInput = document.getElementById('totalAmount');
const resultsSection = document.getElementById('resultsSection');
const resultsBody = document.getElementById('resultsBody');
const resultsTotal = document.getElementById('resultsTotal');

function renumberBadges() {
  const rows = metersList.querySelectorAll('.meter-row');
  rows.forEach((row, i) => {
    row.querySelector('.meter-badge').textContent = HEBREW_LETTERS[i] || String(i + 1);
  });
}

function addMeterRow(name = '', reading = '') {
  const row = rowTemplate.content.firstElementChild.cloneNode(true);

  row.querySelector('.meter-name').value = name;
  row.querySelector('.meter-reading').value = reading;

  row.querySelector('.remove-btn').addEventListener('click', () => {
    row.remove();
    renumberBadges();
  });

  const photoInput = row.querySelector('.meter-photo');
  const preview = row.querySelector('.preview');
  const status = row.querySelector('.ocr-status');
  const readingInput = row.querySelector('.meter-reading');

  photoInput.addEventListener('change', async () => {
    const file = photoInput.files[0];
    if (!file) return;

    preview.src = URL.createObjectURL(file);
    preview.hidden = false;

    status.textContent = 'מזהה מספרים בתמונה...';
    try {
      const digits = await recognizeReading(file);
      if (digits) {
        readingInput.value = digits;
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
    const reading = parseFloat(row.querySelector('.meter-reading').value);
    return { name, reading };
  }).filter(m => !isNaN(m.reading) && m.reading >= 0);

  if (meters.length === 0) {
    alert('נא להוסיף לפחות דירה אחת עם קריאת מונה.');
    return;
  }

  const sumReadings = meters.reduce((sum, m) => sum + m.reading, 0);
  if (sumReadings === 0) {
    alert('סכום קריאות המונים הוא 0 - לא ניתן לחשב חלוקה.');
    return;
  }

  resultsBody.innerHTML = '';
  let paidSoFar = 0;

  meters.forEach((m, i) => {
    const share = m.reading / sumReadings;
    const isLast = i === meters.length - 1;
    const amount = isLast ? totalAmount - paidSoFar : Math.round(share * totalAmount * 100) / 100;
    paidSoFar += amount;

    const row = document.createElement('div');
    row.className = 'results-row';
    row.setAttribute('role', 'row');
    row.innerHTML = `
      <span role="cell">${m.name}</span>
      <span role="cell" class="num">${m.reading}</span>
      <span role="cell" class="num">${(share * 100).toFixed(1)}%</span>
      <span role="cell" class="pay">${amount.toFixed(2)} ₪</span>
    `;
    resultsBody.appendChild(row);
  });

  resultsTotal.textContent = `${paidSoFar.toFixed(2)} ₪`;
  resultsSection.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

addMeterBtn.addEventListener('click', () => addMeterRow());
calcBtn.addEventListener('click', calculateSplit);

addMeterRow('דירה 1 (לדוגמה)', 300);
addMeterRow('דירה 2 (לדוגמה)', 600);
calculateSplit();
