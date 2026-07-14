/**
 * choose-mode.js — Let the user pick Manual vs AI mode. AI mode shows an inline overlay.
 */

import * as api from '../api.js';

export function render() {
    return `
    <div class="choose-page fade-in" style="display:flex;align-items:center;justify-content:center;height:100%;padding:24px;">
        <div style="width:100%;max-width:720px;">
            <h1 style="text-align:center;margin-bottom:8px;">Choose How to Create Clips</h1>
            <p style="text-align:center;color:var(--text-muted);margin-bottom:48px;">Select a workflow to continue.</p>

            <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(300px, 1fr));gap:24px;" id="choose-cards">
                
                <!-- Manual Card -->
                <div class="card choose-card" tabindex="0" role="button" id="choose-manual" style="cursor:pointer;transition:transform 150ms, border-color 150ms;">
                    <div class="card-body" style="padding:32px;display:flex;flex-direction:column;align-items:center;text-align:center;height:100%;">
                        <div style="font-size:48px;margin-bottom:16px;">✂️</div>
                        <h2 style="margin-bottom:12px;">Edit Manually</h2>
                        <p style="color:var(--text-muted);font-size:15px;margin-bottom:24px;flex:1;">
                            Watch the video, read the transcript, and choose your own moments.
                        </p>
                        <button class="btn btn-secondary" style="width:100%;justify-content:center;">Select Manual</button>
                    </div>
                </div>

                <!-- AI Card -->
                <div class="card choose-card" tabindex="0" role="button" id="choose-ai" style="cursor:pointer;transition:transform 150ms, border-color 150ms;border:1px solid var(--accent-glow);">
                    <div class="card-body" style="padding:32px;display:flex;flex-direction:column;align-items:center;text-align:center;height:100%;position:relative;">
                        <div style="position:absolute;top:16px;right:16px;" class="badge badge-ai">Recommended</div>
                        <div style="font-size:48px;margin-bottom:16px;">🤖</div>
                        <h2 style="margin-bottom:12px;">Let AI Find Clips</h2>
                        <p style="color:var(--text-muted);font-size:15px;margin-bottom:24px;flex:1;">
                            Our AI analyzes the transcript to find the most viral, engaging moments automatically.
                        </p>
                        <button class="btn btn-primary" style="width:100%;justify-content:center;">Use AI</button>
                    </div>
                </div>

            </div>

            <!-- AI Overlay (inline) -->
            <div id="ai-overlay" style="display:none;margin-top:32px;">
                <div class="card" style="border-color:var(--accent);background:var(--bg-elev-2);">
                    <div class="card-body" style="text-align:center;padding:32px;">
                        <h3 style="margin-bottom:8px;color:var(--accent);">Finding Viral Moments...</h3>
                        <p style="color:var(--text-muted);font-size:13px;margin-bottom:24px;">This usually takes 30–60 seconds.</p>
                        
                        <div class="progress-track" style="margin-bottom:24px;">
                            <div class="progress-fill" id="ai-progress-fill" style="width:5%;"></div>
                        </div>
                        
                        <button class="btn btn-secondary" id="ai-cancel-btn">Cancel</button>
                    </div>
                </div>
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

    const manualBtn = document.getElementById('choose-manual');
    const aiBtn = document.getElementById('choose-ai');
    const cardsContainer = document.getElementById('choose-cards');
    const aiOverlay = document.getElementById('ai-overlay');
    const aiProgressFill = document.getElementById('ai-progress-fill');
    const cancelBtn = document.getElementById('ai-cancel-btn');

    let pollInterval = null;

    // Hover effects via JS to keep CSS clean, or just rely on CSS
    const cards = [manualBtn, aiBtn];
    cards.forEach(c => {
        c.addEventListener('mouseenter', () => { c.style.transform = 'translateY(-2px)'; c.style.borderColor = c.id === 'choose-ai' ? 'var(--accent)' : 'var(--border-strong)'; });
        c.addEventListener('mouseleave', () => { c.style.transform = 'none'; c.style.borderColor = c.id === 'choose-ai' ? 'var(--accent-glow)' : 'var(--border)'; });
    });

    manualBtn.addEventListener('click', () => {
        go('BUILD_CLIPS', { mode: 'manual' });
    });

    aiBtn.addEventListener('click', async () => {
        cardsContainer.style.display = 'none';
        aiOverlay.style.display = 'block';

        try {
            // Trigger AI ranking
            await api.rankVideo(videoId);
            startPolling();
        } catch (err) {
            alert('Failed to start AI ranking: ' + err.message);
            cancelAI();
        }
    });

    cancelBtn.addEventListener('click', () => {
        cancelAI();
    });

    function cancelAI() {
        if (pollInterval) clearInterval(pollInterval);
        aiOverlay.style.display = 'none';
        cardsContainer.style.display = 'grid';
        aiProgressFill.style.width = '5%';
    }

    async function poll() {
        try {
            const jobs = await api.getJobs(videoId);
            const rankJob = jobs.find(j => j.job_type === 'rank');
            
            if (!rankJob) return;

            if (rankJob.status === 'failed') {
                clearInterval(pollInterval);
                alert('AI Ranking failed: ' + (rankJob.error_message || 'Unknown error'));
                cancelAI();
                return;
            }

            const p = Math.max(5, Math.min(100, Math.round(rankJob.progress)));
            aiProgressFill.style.width = p + '%';

            if (rankJob.status === 'completed') {
                clearInterval(pollInterval);
                aiProgressFill.style.width = '100%';
                
                // Done! Navigate to BUILD_CLIPS with mode='ai'
                setTimeout(() => {
                    go('BUILD_CLIPS', { mode: 'ai' });
                }, 600);
            }
        } catch (err) {
            console.error('Polling error:', err);
        }
    }

    function startPolling() {
        if (pollInterval) clearInterval(pollInterval);
        pollInterval = setInterval(poll, 2000);
        poll(); // immediate run
    }

    // Cleanup on unmount
    const originalGo = window.__app.go;
    window.__app.go = (...args) => {
        if (pollInterval) clearInterval(pollInterval);
        window.__app.go = originalGo;
        originalGo(...args);
    };
}
