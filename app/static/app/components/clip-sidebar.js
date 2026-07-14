/**
 * clip-sidebar.js — Renders the list of approved/selected clips.
 */

import * as clipCard from './clip-card.js';

export function render() {
    return `
    <div style="padding:16px;border-bottom:1px solid var(--border);">
        <h3 style="margin-bottom:4px;font-size:14px;display:flex;align-items:center;gap:6px;">
            <span style="color:var(--success);">✅</span> Selected Clips
        </h3>
        <p style="font-size:12px;color:var(--text-muted);">Clips ready for rendering</p>
    </div>
    <div id="selected-list" style="flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:12px;">
        <!-- Selected clips injected here -->
    </div>
    <div style="padding:16px;border-top:1px solid var(--border);background:var(--bg-elev-1);">
        <button class="btn btn-primary" id="btn-render-all" style="width:100%;justify-content:center;padding:12px;font-size:14px;" disabled>
            Render All Clips
        </button>
    </div>
    `;
}

export function mount(clips, onPreview, onDelete, onRenderAll) {
    const listEl = document.getElementById('selected-list');
    const renderBtn = document.getElementById('btn-render-all');
    
    // Filter to only "approved" status
    const approvedClips = clips.filter(c => c.status === 'approved' || c.status === 'trimmed');

    if (approvedClips.length === 0) {
        listEl.innerHTML = `
            <div style="text-align:center;padding:24px 0;color:var(--text-dim);">
                <div style="font-size:24px;margin-bottom:8px;">📁</div>
                <div style="font-size:13px;">No clips selected yet.</div>
            </div>
        `;
        renderBtn.disabled = true;
        return;
    }

    listEl.innerHTML = '';
    
    approvedClips.forEach(clip => {
        const card = clipCard.render(clip, onPreview, onDelete);
        listEl.appendChild(card);
    });

    renderBtn.disabled = false;
    renderBtn.addEventListener('click', onRenderAll);
}
