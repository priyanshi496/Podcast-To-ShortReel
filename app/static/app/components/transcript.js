/**
 * transcript.js — Renders the interactive, synchronized transcript.
 */

export function render() {
    return `
    <div class="transcript-container" style="display:flex;flex-direction:column;height:100%;border-left:1px solid var(--border);background:var(--bg-base);">
        <div style="padding:16px;border-bottom:1px solid var(--border);">
            <input type="text" id="transcript-search" class="input" placeholder="Search transcript (⌘K)..." style="background:var(--bg-elev-1);">
        </div>
        <div id="transcript-list" style="flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:8px;">
            <div class="skeleton" style="height:48px;"></div>
            <div class="skeleton" style="height:72px;"></div>
            <div class="skeleton" style="height:32px;"></div>
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

export function mount(segments, onSeek) {
    const listEl = document.getElementById('transcript-list');
    const searchEl = document.getElementById('transcript-search');
    let activeSegmentId = null;

    if (!segments || segments.length === 0) {
        listEl.innerHTML = `<div style="text-align:center;color:var(--text-muted);margin-top:32px;">No transcript available.</div>`;
        return {};
    }

    function renderSegments(filter = '') {
        const query = filter.toLowerCase();
        let html = '';
        for (const seg of segments) {
            const matches = !query || seg.text.toLowerCase().includes(query) || (seg.speaker && seg.speaker.toLowerCase().includes(query));
            if (!matches) continue;

            html += `
                <div class="transcript-segment" id="seg-${seg.id}" data-id="${seg.id}" data-start="${seg.start_time}" style="padding:8px 12px;border-radius:var(--r-sm);cursor:pointer;border-left:3px solid transparent;transition:all 150ms;">
                    <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
                        <span style="font-size:12px;font-weight:600;color:var(--text-muted);">${seg.speaker || 'Speaker'}</span>
                        <span style="font-size:12px;font-family:var(--font-mono);color:var(--text-dim);">${formatTime(seg.start_time)}</span>
                    </div>
                    <div style="color:var(--text);font-size:14px;line-height:1.5;">
                        ${seg.text}
                    </div>
                </div>
            `;
        }
        listEl.innerHTML = html || `<div style="text-align:center;color:var(--text-dim);margin-top:24px;">No results found.</div>`;

        // Bind clicks
        const els = listEl.querySelectorAll('.transcript-segment');
        els.forEach(el => {
            el.addEventListener('click', () => {
                const start = parseFloat(el.getAttribute('data-start'));
                onSeek(start);
            });
            // Hover effect
            el.addEventListener('mouseenter', () => {
                if (el.getAttribute('data-id') !== activeSegmentId) el.style.background = 'var(--bg-elev-1)';
            });
            el.addEventListener('mouseleave', () => {
                if (el.getAttribute('data-id') !== activeSegmentId) el.style.background = 'transparent';
            });
        });
        
        // Restore active class if it matches
        if (activeSegmentId) updateActiveSegment(activeSegmentId);
    }

    renderSegments();

    searchEl.addEventListener('input', (e) => {
        renderSegments(e.target.value);
    });

    function updateActiveSegment(id) {
        if (activeSegmentId) {
            const old = document.getElementById(`seg-${activeSegmentId}`);
            if (old) {
                old.style.background = 'transparent';
                old.style.borderLeftColor = 'transparent';
            }
        }
        activeSegmentId = id;
        if (activeSegmentId) {
            const active = document.getElementById(`seg-${activeSegmentId}`);
            if (active) {
                active.style.background = 'var(--bg-elev-2)';
                active.style.borderLeftColor = 'var(--accent)';
                // Only scroll into view if user isn't searching
                if (!searchEl.value) {
                    active.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                }
            }
        }
    }

    return {
        updateTime: (currentTime) => {
            // Find active segment
            const seg = segments.find(s => currentTime >= s.start_time && currentTime < s.end_time);
            if (seg && seg.id !== activeSegmentId) {
                updateActiveSegment(seg.id);
            }
        },
        focusSearch: () => {
            searchEl.focus();
        }
    };
}
