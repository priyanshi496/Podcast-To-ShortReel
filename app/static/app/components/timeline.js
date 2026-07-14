/**
 * timeline.js — Custom dual-handle timeline (no canvas) for setting IN and OUT points.
 * Also renders a fake waveform for visual structure.
 */

export function render() {
    return `
    <div class="timeline-container" style="user-select:none;margin-top:16px;">
        <!-- Fake waveform for visual context -->
        <div style="height:32px;display:flex;align-items:flex-end;gap:2px;opacity:0.3;margin-bottom:4px;padding:0 8px;">
            ${Array.from({length: 40}).map(() => {
                const h = Math.random() * 20 + 8;
                return `<div style="flex:1;background:var(--text-muted);height:${h}px;border-radius:2px;"></div>`;
            }).join('')}
        </div>

        <div style="position:relative;height:32px;background:var(--bg-elev-2);border-radius:var(--r);padding:0 8px;" id="tl-track">
            <!-- Selected Range Bar -->
            <div id="tl-range" style="position:absolute;top:8px;bottom:8px;background:linear-gradient(90deg, var(--accent), var(--accent-2));opacity:0.5;border-radius:4px;"></div>
            
            <!-- Playhead -->
            <div id="tl-playhead" style="position:absolute;top:0;bottom:0;width:2px;background:#fff;z-index:3;pointer-events:none;transform:translateX(-50%);"></div>

            <!-- IN Handle -->
            <div id="tl-handle-in" class="tl-handle" style="position:absolute;top:4px;bottom:4px;width:12px;background:var(--success);border-radius:6px;cursor:ew-resize;z-index:4;transform:translateX(-50%);box-shadow:0 2px 4px rgba(0,0,0,0.5);"></div>
            
            <!-- OUT Handle -->
            <div id="tl-handle-out" class="tl-handle" style="position:absolute;top:4px;bottom:4px;width:12px;background:var(--error);border-radius:6px;cursor:ew-resize;z-index:4;transform:translateX(-50%);box-shadow:0 2px 4px rgba(0,0,0,0.5);"></div>
        </div>
        
        <div style="display:flex;justify-content:space-between;margin-top:8px;color:var(--text-dim);font-size:12px;font-family:var(--font-mono);">
            <span id="tl-time-in">00:00</span>
            <span id="tl-time-out">00:30</span>
        </div>
    </div>
    `;
}

function formatTime(sec) {
    if (isNaN(sec)) return "00:00";
    const m = Math.floor(sec / 60).toString().padStart(2, '0');
    const s = Math.floor(sec % 60).toString().padStart(2, '0');
    return `${m}:${s}`;
}

export function mount(duration, initialIn, initialOut, onChange, onSeek) {
    const track = document.getElementById('tl-track');
    const range = document.getElementById('tl-range');
    const handleIn = document.getElementById('tl-handle-in');
    const handleOut = document.getElementById('tl-handle-out');
    const playhead = document.getElementById('tl-playhead');
    const labelIn = document.getElementById('tl-time-in');
    const labelOut = document.getElementById('tl-time-out');

    let inPoint = initialIn || 0;
    let outPoint = initialOut || Math.min(30, duration);
    if (outPoint <= inPoint) outPoint = inPoint + 1;

    let isDragging = null; // 'in', 'out', or 'playhead'

    function updateUI() {
        if (!duration) return;
        const inPct = (inPoint / duration) * 100;
        const outPct = (outPoint / duration) * 100;

        handleIn.style.left = `${inPct}%`;
        handleOut.style.left = `${outPct}%`;
        range.style.left = `${inPct}%`;
        range.style.width = `${outPct - inPct}%`;

        labelIn.textContent = formatTime(inPoint);
        labelOut.textContent = formatTime(outPoint);
    }

    function getTimeFromEvent(e) {
        const rect = track.getBoundingClientRect();
        let x = e.clientX - rect.left;
        x = Math.max(0, Math.min(x, rect.width));
        return (x / rect.width) * duration;
    }

    track.addEventListener('mousedown', (e) => {
        if (!duration) return;
        const target = e.target;
        if (target === handleIn) {
            isDragging = 'in';
        } else if (target === handleOut) {
            isDragging = 'out';
        } else {
            // Clicked on track: act as seek
            isDragging = 'playhead';
            const t = getTimeFromEvent(e);
            onSeek(t);
        }
        e.preventDefault();
    });

    document.addEventListener('mousemove', (e) => {
        if (!isDragging) return;
        const t = getTimeFromEvent(e);

        if (isDragging === 'in') {
            inPoint = Math.min(t, outPoint - 0.5); // 0.5s min gap
            updateUI();
            onChange(inPoint, outPoint);
            onSeek(inPoint);
        } else if (isDragging === 'out') {
            outPoint = Math.max(t, inPoint + 0.5);
            updateUI();
            onChange(inPoint, outPoint);
            onSeek(outPoint);
        } else if (isDragging === 'playhead') {
            onSeek(t);
        }
    });

    document.addEventListener('mouseup', () => {
        if (isDragging) {
            isDragging = null;
        }
    });

    updateUI();

    return {
        updatePlayhead: (currentTime) => {
            if (!duration) return;
            const pct = (currentTime / duration) * 100;
            playhead.style.left = `${pct}%`;
        },
        setIn: (t) => { inPoint = Math.min(t, outPoint - 0.5); updateUI(); onChange(inPoint, outPoint); },
        setOut: (t) => { outPoint = Math.max(t, inPoint + 0.5); updateUI(); onChange(inPoint, outPoint); },
        getIn: () => inPoint,
        getOut: () => outPoint
    };
}
