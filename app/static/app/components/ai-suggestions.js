/**
 * ai-suggestions.js — Renders unapproved AI clips with hook score and rationale.
 */

import * as api from '../api.js';

export function render() {
    return `
    <div style="padding:16px;border-bottom:1px solid var(--border);">
        <h3 style="margin-bottom:4px;font-size:14px;display:flex;align-items:center;gap:6px;">
            <span style="color:var(--accent);">✨</span> AI Suggestions
        </h3>
        <p style="font-size:12px;color:var(--text-muted);">Review and approve clips</p>
    </div>
    <div id="ai-list" style="flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:12px;">
        <div class="skeleton" style="height:120px;"></div>
        <div class="skeleton" style="height:120px;"></div>
    </div>
    `;
}

export function mount(clips, onPreview, onApprove, onReject) {
    const listEl = document.getElementById('ai-list');
    
    // Filter to only "suggested" status
    const pendingClips = clips.filter(c => c.status === 'suggested');

    if (pendingClips.length === 0) {
        listEl.innerHTML = `
            <div style="text-align:center;padding:24px 0;color:var(--text-dim);">
                <div style="font-size:24px;margin-bottom:8px;">✅</div>
                <div style="font-size:13px;">All caught up!</div>
            </div>
        `;
        return;
    }

    listEl.innerHTML = '';
    
    pendingClips.forEach((clip, idx) => {
        const hook = (clip.hook_line || 'Engaging moment').replace(/</g, '&lt;');
        const reason = (clip.reason || 'AI selected this clip for its high engagement potential.').replace(/</g, '&lt;');
        const duration = Math.round(clip.duration_sec);
        
        const card = document.createElement('div');
        card.className = 'ai-suggestion-card card';
        card.style.background = 'var(--bg-elev-1)';
        card.style.borderLeft = '3px solid var(--accent)';
        
        card.innerHTML = `
            <div class="card-body" style="padding:16px;">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px;">
                    <div>
                        <div style="display:flex;gap:8px;align-items:center;margin-bottom:4px;">
                            <span class="badge badge-ai">Clip ${idx + 1}</span>
                            <span style="font-size:12px;font-family:var(--font-mono);color:var(--text-muted);">${duration}s</span>
                        </div>
                        <div style="font-weight:600;font-size:13px;line-height:1.4;">"${hook}"</div>
                    </div>
                </div>
                
                <div style="font-size:12px;color:var(--text-dim);margin-bottom:16px;line-height:1.5;">
                    ${reason}
                </div>
                
                <div style="display:flex;gap:8px;">
                    <button class="btn btn-primary btn-sm ai-btn-approve" style="flex:1;padding:6px;font-size:12px;justify-content:center;">Approve</button>
                    <button class="btn btn-secondary btn-sm ai-btn-reject" style="padding:6px;font-size:12px;">✕</button>
                    <button class="btn btn-ghost btn-sm ai-btn-preview" style="padding:6px;font-size:12px;">▶ Preview</button>
                </div>
            </div>
        `;
        
        card.querySelector('.ai-btn-approve').addEventListener('click', () => {
            card.style.opacity = '0.5';
            card.style.pointerEvents = 'none';
            onApprove(clip);
        });
        
        card.querySelector('.ai-btn-reject').addEventListener('click', () => {
            card.style.transition = 'opacity 200ms, transform 200ms';
            card.style.opacity = '0';
            card.style.transform = 'scale(0.95)';
            setTimeout(() => card.remove(), 200);
            onReject(clip);
        });
        
        card.querySelector('.ai-btn-preview').addEventListener('click', () => {
            onPreview(clip);
        });
        
        listEl.appendChild(card);
    });
}
