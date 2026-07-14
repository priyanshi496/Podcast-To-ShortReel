/**
 * ready.js — Shown when transcription completes, before user chooses manual vs AI.
 */

import * as api from '../api.js';

export function render() {
    return `
    <div class="ready-page fade-in" style="display:flex;align-items:center;justify-content:center;height:100%;">
        <div class="card" style="width:100%;max-width:480px;">
            <div class="card-body" style="text-align:center;padding:40px 24px;">
                <div style="font-size:48px;margin-bottom:16px;">✅</div>
                <h2 style="margin-bottom:12px;">Transcript Ready</h2>
                
                <p style="margin-bottom:24px;color:var(--text-muted);">
                    Your podcast has been successfully transcribed.
                </p>
                
                <div id="ready-metadata" style="display:flex;justify-content:center;gap:16px;margin-bottom:32px;font-size:13px;color:var(--text-dim);">
                    <div class="skeleton" style="width:120px;height:18px;"></div>
                </div>
                
                <button class="btn btn-primary" id="ready-continue-btn" style="width:100%;justify-content:center;padding:12px;font-size:15px;">
                    Continue to Clip Creation →
                </button>
            </div>
        </div>
    </div>
    `;
}

export function mount(go) {
    const videoId = window.__app.APP.videoId;
    if (!videoId) {
        go('HOME');
        return;
    }

    const metaEl = document.getElementById('ready-metadata');
    const continueBtn = document.getElementById('ready-continue-btn');

    // Load video info to show duration
    api.getVideo(videoId).then(video => {
        if (!video) return;
        const mins = Math.round((video.duration_sec || 0) / 60);
        metaEl.innerHTML = `
            <span>⏱ ${mins} minutes</span>
            <span>🎙 Transcribed</span>
        `;
    }).catch(err => {
        metaEl.innerHTML = `<span>Ready</span>`;
    });

    continueBtn.addEventListener('click', () => {
        go('CHOOSE_MODE');
    });
}
