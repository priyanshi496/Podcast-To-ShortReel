/**
 * transcribing.js — Shows progress bar and polls for transcription completion.
 * Redesigned as a two-column workspace.
 */

import * as api from '../api.js';

export function render() {
    return `
    <div class="transcribing-workspace fade-in">
        <!-- Left Column: Progress -->
        <div class="transcribing-progress-pane">
            <div class="transcribing-icon">🎙</div>
            <h2 id="transcribing-status-text" style="font-size:24px; margin-bottom:8px;">Extracting audio...</h2>
            <div id="transcribing-filename" class="transcribing-filename">Loading...</div>
            
            <div class="progress-track" style="margin-top: 48px; height: 8px;">
                <div class="progress-fill" id="transcribing-progress-fill" style="width: 5%;"></div>
            </div>
            
            <div style="display:flex; justify-content:space-between; margin-top: 16px; align-items: flex-end;">
                <span id="transcribing-percent" style="font-size:36px; font-weight:700; line-height:1;">5%</span>
                <span id="transcribing-eta" style="color:var(--text-muted); font-size:13px;">Estimating time...</span>
            </div>
        </div>
        
        <!-- Right Column: Stages -->
        <div class="transcribing-stages-pane">
            <h3 style="margin-bottom: 24px; color: var(--text-dim); font-weight:500;">Current Stage</h3>
            <ul class="stages-list">
                <li id="stage-1" class="stage-item completed"><span class="stage-icon">✓</span> Upload completed</li>
                <li id="stage-2" class="stage-item active"><span class="stage-icon">●</span> Extracting audio</li>
                <li id="stage-3" class="stage-item"><span class="stage-icon">○</span> Speech recognition</li>
                <li id="stage-4" class="stage-item"><span class="stage-icon">○</span> Speaker separation</li>
                <li id="stage-5" class="stage-item"><span class="stage-icon">○</span> Finalizing transcript</li>
            </ul>
            <div id="transcribing-error" class="upload-status error" style="display:none;margin-top:24px;"></div>
        </div>
    </div>
    `;
}

function updateStages(activeStageIdx) {
    for (let i = 1; i <= 5; i++) {
        const li = document.getElementById(`stage-${i}`);
        if (!li) continue;
        const icon = li.querySelector('.stage-icon');
        
        li.className = 'stage-item'; // reset
        if (i < activeStageIdx) {
            li.classList.add('completed');
            icon.textContent = '✓';
        } else if (i === activeStageIdx) {
            li.classList.add('active');
            icon.textContent = '●';
        } else {
            icon.textContent = '○';
        }
    }
}

export async function mount(go) {
    const videoId = window.__app.APP.videoId;
    if (!videoId) {
        go('HOME');
        return;
    }

    const fillEl = document.getElementById('transcribing-progress-fill');
    const textEl = document.getElementById('transcribing-status-text');
    const pctEl = document.getElementById('transcribing-percent');
    const errEl = document.getElementById('transcribing-error');
    const fileEl = document.getElementById('transcribing-filename');
    const etaEl  = document.getElementById('transcribing-eta');

    let pollInterval = null;
    let startTime = Date.now();

    // Fetch video info to display filename
    try {
        const video = await api.getVideo(videoId);
        fileEl.textContent = video.original_filename || `Video #${videoId}`;
    } catch (e) {
        fileEl.textContent = `Video #${videoId}`;
    }

    async function poll() {
        try {
            const jobs = await api.getJobs(videoId);
            const transcribeJob = jobs.find(j => j.job_type === 'transcribe');
            
            if (!transcribeJob) return;

            if (transcribeJob.status === 'failed') {
                clearInterval(pollInterval);
                errEl.style.display = 'block';
                errEl.textContent = transcribeJob.error_message || 'Transcription failed';
                return;
            }

            const p = Math.max(5, Math.min(100, Math.round(transcribeJob.progress * 100)));
            fillEl.style.width = p + '%';
            pctEl.textContent = p + '%';
            
            // Fake ETA math for UI demo purposes
            if (p > 5 && p < 100) {
                const elapsed = (Date.now() - startTime) / 1000;
                const totalEstimated = elapsed / (p / 100);
                const remaining = Math.round(totalEstimated - elapsed);
                if (remaining > 60) {
                    etaEl.textContent = `Estimated remaining: ${Math.round(remaining/60)} min`;
                } else {
                    etaEl.textContent = `Estimated remaining: ${remaining} sec`;
                }
            } else if (p === 100) {
                etaEl.textContent = "Complete!";
            }
            
            // Logic for stages
            if (p < 30) {
                textEl.textContent = 'Extracting audio...';
                updateStages(2);
            } else if (p < 70) {
                textEl.textContent = 'Speech recognition...';
                updateStages(3);
            } else if (p < 95) {
                textEl.textContent = 'Detecting speakers...';
                updateStages(4);
            } else {
                textEl.textContent = 'Finalizing transcript...';
                updateStages(5);
            }

            if (transcribeJob.status === 'completed' || transcribeJob.status === 'done') {
                clearInterval(pollInterval);
                fillEl.style.width = '100%';
                pctEl.textContent = '100%';
                textEl.textContent = 'Transcription complete';
                updateStages(6); // all complete
                
                setTimeout(() => {
                    go('READY');
                }, 800);
            }
        } catch (err) {
            console.error('Polling error:', err);
        }
    }

    api.processVideo(videoId).catch(e => console.log('processVideo trigger:', e.message));

    pollInterval = setInterval(poll, 2000);
    poll();

    const originalGo = window.__app.go;
    window.__app.go = (...args) => {
        clearInterval(pollInterval);
        window.__app.go = originalGo;
        originalGo(...args);
    };
}
