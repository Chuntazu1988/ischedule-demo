const metersList = document.getElementById('metersList');
const rowTemplate = document.getElementById('meterRowTemplate');
const addMeterBtn = document.getElementById('addMeterBtn');
const calcBtn = document.getElementById('calcBtn');
const totalAmountInput = document.getElementById('totalAmount');
const resultsSection = document.getElementById('resultsSection');
const resultsTableBody = document.querySelector('#resultsTable tbody');
const resultsNote = document.getElementById('resultsNote');

function addMeterRow() {
  const row = rowTemplate.content.firstElementChild.cloneNode(true);

  row.querySelector('.remove-btn').addEventListener('click', () => {
    row.remove();
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
        status.textContent = `זוהה: ${digits} (בדקו ותקנו אם צריך)`;
      } else {
        status.textContent = 'לא הצלחתי לזהות מספר - נא להקליד ידנית.';
      }
    } catch (err) {
      status.textContent = 'זיהוי אוטומטי נכשל - נא להקליד ידנית.';
    }
  });

  metersList.appendChild(row);
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

  resultsTableBody.innerHTML = '';
  let paidSoFar = 0;

  meters.forEach((m, i) => {
    const share = m.reading / sumReadings;
    const isLast = i === meters.length - 1;
    const amount = isLast ? totalAmount - paidSoFar : Math.round(share * totalAmount * 100) / 100;
    paidSoFar += amount;

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${m.name}</td>
      <td>${m.reading}</td>
      <td>${(share * 100).toFixed(1)}%</td>
      <td>${amount.toFixed(2)} ₪</td>
    `;
    resultsTableBody.appendChild(tr);
  });

  resultsNote.textContent = `סה"כ חולק: ${paidSoFar.toFixed(2)} ₪ מתוך ${totalAmount.toFixed(2)} ₪`;
  resultsSection.hidden = false;
  resultsSection.scrollIntoView({ behavior: 'smooth' });
}

addMeterBtn.addEventListener('click', addMeterRow);
calcBtn.addEventListener('click', calculateSplit);

addMeterRow();
addMeterRow();
