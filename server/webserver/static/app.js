function renderHeatmap(canvas, pixels) {
    if (!canvas || !Array.isArray(pixels) || pixels.length !== 64) return;
    const ctx  = canvas.getContext('2d');
    const size = canvas.width;
    const cell = size / 8;
    const min  = Math.min(...pixels);
    const max  = Math.max(...pixels);
    const range = max - min || 1;

    for (let y = 0; y < 8; y++) {
        for (let x = 0; x < 8; x++) {
            const normalized = (pixels[y * 8 + x] - min) / range;
            ctx.fillStyle = `hsl(${240 * (1 - normalized)}, 90%, 50%)`;
            ctx.fillRect(x * cell, y * cell, cell, cell);
        }
    }
}

function connectWebSocket() {
    const statusEl = document.getElementById('ws-status');
    const ws = new WebSocket(`ws://${location.host}/ws`);

    statusEl.textContent = 'Connecting...';

    ws.onopen  = () => { statusEl.textContent = 'Connected'; };
    ws.onclose = () => {
        statusEl.textContent = 'Disconnected';
        setTimeout(connectWebSocket, 3000);
    };
    ws.onerror = () => { statusEl.textContent = 'Error'; };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);

            renderHeatmap(document.getElementById('live-canvas'), data.pixels ?? []);

            const therm = data.thermistor ?? data.thermistor_temp;
            document.getElementById('thermistor-temp').textContent =
                therm != null ? `${parseFloat(therm).toFixed(1)}°C` : '--°C';

            const pred = (data.prediction ?? '--').toUpperCase();
            const predEl = document.getElementById('prediction-label');
            predEl.textContent = pred;
            predEl.className = pred === 'PRESENT' ? 'badge-present' : 'badge-empty';

            const conf = data.confidence;
            document.getElementById('confidence-val').textContent =
                conf != null ? `${(conf * 100).toFixed(1)}%` : '--%';

            fetchReadings();

        } catch (e) {
            console.error('WebSocket parse error:', e);
        }
    };
}

async function sendCommand(command) {
    const statusEl = document.getElementById('command-status');
    statusEl.textContent = `Sending "${command}"...`;
    try {
        const res = await fetch('/api/command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command })
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        statusEl.textContent = `✓ "${command}" sent`;
    } catch (e) {
        statusEl.textContent = `✗ Error: ${e.message}`;
    }
}

async function fetchDevices() {
    try {
        const res  = await fetch('/api/devices');
        const data = await res.json();
        const tbody = document.querySelector('#devices-table tbody');
        tbody.innerHTML = '';
        data.forEach((device, i) => {
            const tr = document.createElement('tr');
            tr.innerHTML = `<td>${i + 1}</td><td>${device.mac_address}</td>`;
            tbody.appendChild(tr);
        });
    } catch (e) {
        console.error('fetchDevices error:', e);
    }
}

async function fetchReadings(macFilter = null) {
    try {
        const url = macFilter
            ? `/api/readings?device_mac=${encodeURIComponent(macFilter)}`
            : '/api/readings';
        const res  = await fetch(url);
        const data = await res.json();

        const tbody = document.querySelector('#readings-table tbody');
        tbody.innerHTML = '';

        data.forEach(row => {
            const tr = document.createElement('tr');

            const thumbId = `thumb-${row.id}`;
            const pred    = (row.prediction ?? '').toUpperCase();
            const predClass = pred === 'PRESENT' ? 'badge-present' : 'badge-empty';
            const conf    = row.confidence != null ? `${(row.confidence * 100).toFixed(1)}%` : '--';
            const therm   = row.thermistor_temp != null
                ? `${parseFloat(row.thermistor_temp).toFixed(1)}°C` : '--';

            tr.innerHTML = `
                <td>${row.id}</td>
                <td>${row.mac_address}</td>
                <td>${therm}</td>
                <td class="${predClass}">${pred}</td>
                <td>${conf}</td>
                <td><canvas id="${thumbId}" class="thumb-canvas" width="64" height="64"></canvas></td>
                <td><button class="btn-delete" data-id="${row.id}">Delete</button></td>
            `;
            tbody.appendChild(tr);

            renderHeatmap(document.getElementById(thumbId), row.pixels ?? []);
        });

        tbody.querySelectorAll('.btn-delete').forEach(btn => {
            btn.addEventListener('click', () => deleteReading(parseInt(btn.dataset.id)));
        });

        fetchDevices();

    } catch (e) {
        console.error('fetchReadings error:', e);
    }
}

async function deleteReading(id) {
    try {
        const res = await fetch(`/api/readings/${id}`, { method: 'DELETE' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        fetchReadings();
    } catch (e) {
        console.error('deleteReading error:', e);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    connectWebSocket();
    fetchReadings();
    fetchDevices();

    document.getElementById('btn-get-one').addEventListener('click', () => sendCommand('get_one'));
    document.getElementById('btn-start').addEventListener('click',   () => sendCommand('start_continuous'));
    document.getElementById('btn-stop').addEventListener('click',    () => sendCommand('stop'));

    document.getElementById('btn-filter').addEventListener('click', () => {
        const val = document.getElementById('mac-filter').value.trim();
        if (val) fetchReadings(val);
    });

    document.getElementById('btn-clear-filter').addEventListener('click', () => {
        document.getElementById('mac-filter').value = '';
        fetchReadings();
    });
});
