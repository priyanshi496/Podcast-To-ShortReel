/**
 * build-clips.js — The core editor workspace.
 * Integrates Player, Timeline, Transcript, AI Suggestions, and Selected Clips.
 */

import * as api from '../api.js';
import * as playerComp from '../components/video-player.js';
import * as timelineComp from '../components/timeline.js';
import * as transcriptComp from '../components/transcript.js';
import * as sidebarComp from '../components/clip-sidebar.js';
import * as aiComp from '../components/ai-suggestions.js';

export function render() {
    return `
    <div class="build-page fade-in" style="display:flex;height:calc(100vh - 64px);overflow:hidden;">
        
        <!-- Left: Transcript -->
        <div style="width:320px;flex-shrink:0;border-right:1px solid var(--border);display:flex;flex-direction:column;">
            <div id="editor-transcript" style="flex:1;overflow:hidden;"></div>
        </div>
        
        <!-- Center: Player & Timeline -->
        <div style="flex:1;display:flex;flex-direction:column;min-width:0;background:var(--bg-base);">
            <div style="flex:1;padding:24px;display:flex;align-items:center;justify-content:center;">
                <div id="editor-player" style="width:100%;max-width:800px;margin:0 auto;box-shadow:0 12px 32px rgba(0,0,0,0.4);border-radius:var(--r-lg);"></div>
            </div>
            
            <div style="height:120px;border-top:1px solid var(--border);background:var(--bg-elev-1);padding:16px 24px;display:flex;flex-direction:column;justify-content:center;">
                <div id="editor-timeline"></div>
                <div style="display:flex;justify-content:flex-end;margin-top:12px;">
                    <button id="editor-add-manual" class="btn btn-secondary" style="font-size:13px;padding:6px 12px;">+ Save Selection</button>
                </div>
            </div>
        </div>
        
        <!-- Right: AI & Selected Clips -->
        <div style="width:340px;flex-shrink:0;border-left:1px solid var(--border);display:flex;flex-direction:column;background:var(--bg-base);">
            <!-- AI Suggestions (hidden in manual mode) -->
            <div id="editor-ai" style="flex:1;overflow:hidden;display:flex;flex-direction:column;border-bottom:1px solid var(--border);"></div>
            
            <!-- Selected Clips -->
            <div id="editor-sidebar" style="flex:1;overflow:hidden;display:flex;flex-direction:column;"></div>
        </div>
    </div>
    `;
}

export async function mount(go) {
    const videoId = window.__app.APP.videoId;
    if (!videoId) {
        go('HOME');
        return;
    }

    const mode = window.__app.APP.mode || 'manual';
    const aiContainer = document.getElementById('editor-ai');
    if (mode === 'manual') aiContainer.style.display = 'none';

    try {
        // Fetch data
        const [video, transcript, clips] = await Promise.all([
            api.getVideo(videoId),
            api.getTranscript(videoId),
            api.getClips(videoId)
        ]);

        let allClips = clips || [];
        let player, timeline, transcriptView;

        // Initialize Player
        document.getElementById('editor-player').innerHTML = playerComp.render();
        player = playerComp.mount(video.storage_path, (currentTime) => {
            timeline?.updatePlayhead(currentTime);
            transcriptView?.updateTime(currentTime);
            window.__app.APP.editor.currentTime = currentTime;
        });

        // Initialize Timeline
        document.getElementById('editor-timeline').innerHTML = timelineComp.render();
        timeline = timelineComp.mount(
            video.duration_sec,
            window.__app.APP.editor.inPoint,
            window.__app.APP.editor.outPoint,
            (inP, outP) => {
                window.__app.APP.editor.inPoint = inP;
                window.__app.APP.editor.outPoint = outP;
            },
            (t) => {
                player.seek(t);
            }
        );

        // Initialize Transcript
        document.getElementById('editor-transcript').innerHTML = transcriptComp.render();
        transcriptView = transcriptComp.mount(transcript, (t) => {
            player.seek(t);
        });

        // Add Manual Clip
        document.getElementById('editor-add-manual').addEventListener('click', async () => {
            try {
                const inP = window.__app.APP.editor.inPoint;
                const outP = window.__app.APP.editor.outPoint;
                const newClip = await api.createManualClip(videoId, inP, outP);
                allClips.push(newClip);
                renderSidebars();
            } catch (err) {
                alert('Failed to save clip: ' + err.message);
            }
        });

        // Render AI and Selected Sidebar
        function renderSidebars() {
            if (mode === 'ai') {
                document.getElementById('editor-ai').innerHTML = aiComp.render();
                aiComp.mount(
                    allClips,
                    (clip) => {
                        // preview
                        timeline.setIn(clip.start_time);
                        timeline.setOut(clip.end_time);
                        player.seek(clip.start_time);
                        player.play();
                    },
                    async (clip) => {
                        // approve
                        try {
                            const updated = await api.approveClip(clip.id);
                            allClips = allClips.map(c => c.id === clip.id ? updated : c);
                            renderSidebars();
                        } catch (err) { alert(err.message); }
                    },
                    async (clip) => {
                        // reject
                        try {
                            const updated = await api.rejectClip(clip.id);
                            allClips = allClips.map(c => c.id === clip.id ? updated : c);
                            renderSidebars();
                        } catch (err) { alert(err.message); }
                    }
                );
            }

            document.getElementById('editor-sidebar').innerHTML = sidebarComp.render();
            sidebarComp.mount(
                allClips,
                (clip) => {
                    // preview
                    timeline.setIn(clip.start_time);
                    timeline.setOut(clip.end_time);
                    player.seek(clip.start_time);
                    player.play();
                },
                async (clip) => {
                    // delete / reject
                    try {
                        const updated = await api.rejectClip(clip.id);
                        allClips = allClips.map(c => c.id === clip.id ? updated : c);
                        renderSidebars();
                    } catch (err) { alert(err.message); }
                },
                () => {
                    // render all
                    go('EXPORT'); // next sprint
                }
            );
        }

        renderSidebars();

        // Bind global shortcut ⌘K for search
        const keyHandler = (e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
                e.preventDefault();
                transcriptView.focusSearch();
            }
        };
        document.addEventListener('keydown', keyHandler);
        
        // Cleanup on navigate
        const originalGo = window.__app.go;
        window.__app.go = (...args) => {
            document.removeEventListener('keydown', keyHandler);
            window.__app.go = originalGo;
            originalGo(...args);
        };

    } catch (err) {
        alert('Failed to load editor data: ' + err.message);
    }
}
