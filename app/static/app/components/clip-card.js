/**
 * clip-card.js — Stateless view component for a selected clip.
 * Renders the thumbnail, title, duration, and source badge.
 */

import * as api from '../api.js';

function formatTime(sec) {
    if (isNaN(sec)) return "00:00";
    const m = Math.floor(sec / 60).toString().padStart(2, '0');
    const s = Math.floor(sec % 60).toString().padStart(2, '0');
    return `${m}:${s}`;
}

export function render(clip, onPreview, onDelete) {
    // Escape titles just in case
    const title = (clip.title || clip.suggested_title || 'Untitled Clip').replace(/</g, '&lt;');
    const duration = Math.round(clip.duration_sec);
    const source = clip.source === 'manual' ? 'Manual' : 'AI';
    const badgeClass = clip.source === 'manual' ? 'badge-manual' : 'badge-ai';
    
    // Create card element
    const el = document.createElement('div');
    el.className = 'clip-card';
    el.style.display = 'flex';
    el.style.gap = '12px';
    el.style.padding = '12px';
    el.style.background = 'var(--bg-elev-2)';
    el.style.border = '1px solid var(--border)';
    el.style.borderRadius = 'var(--r)';
    el.style.alignItems = 'center';
    el.style.position = 'relative';

    // Thumbnail logic
    const thumbUrl = api.thumbnailUrl(clip.video_id, clip.start_time);

    el.innerHTML = `
        <div style="width:64px;height:64px;border-radius:var(--r-sm);overflow:hidden;flex-shrink:0;background:#000;position:relative;">
            <img src="${thumbUrl}" style="width:100%;height:100%;object-fit:cover;" alt="Clip thumbnail" loading="lazy" onerror="this.style.display='none'">
            <div style="position:absolute;bottom:4px;right:4px;background:rgba(0,0,0,0.8);padding:2px 4px;border-radius:4px;font-size:10px;font-family:var(--font-mono);">${duration}s</div>
        </div>
        
        <div style="flex:1;min-width:0;">
            <div style="font-weight:600;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:4px;" title="${title}">${title}</div>
            <div style="display:flex;gap:8px;align-items:center;">
                <span class="badge ${badgeClass}">${source}</span>
                <span style="font-size:11px;color:var(--text-dim);">${formatTime(clip.start_time)} - ${formatTime(clip.end_time)}</span>
            </div>
        </div>

        <div style="display:flex;flex-direction:column;gap:4px;flex-shrink:0;">
            <button class="btn btn-ghost clip-card-preview" style="padding:4px 8px;font-size:11px;">▶</button>
            <button class="btn btn-ghost clip-card-delete" style="padding:4px 8px;font-size:11px;color:var(--text-dim);">&times;</button>
        </div>
    `;

    el.querySelector('.clip-card-preview').addEventListener('click', () => onPreview(clip));
    el.querySelector('.clip-card-delete').addEventListener('click', () => onDelete(clip));

    return el;
}
