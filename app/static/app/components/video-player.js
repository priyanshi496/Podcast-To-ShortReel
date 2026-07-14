/**
 * video-player.js — HTML5 video player wrapper with custom controls bridging to the APP state.
 */

export function render() {
    return `
    <div class="video-container" style="position:relative;background:#000;border-radius:var(--r-lg);overflow:hidden;aspect-ratio:16/9;display:flex;flex-direction:column;justify-content:center;">
        <video id="editor-video" style="width:100%;max-height:100%;outline:none;"></video>
        
        <!-- Custom Controls overlay (could be extracted, but keeping inline for simplicity) -->
        <div style="position:absolute;bottom:0;left:0;right:0;padding:24px 16px 12px;background:linear-gradient(0deg, rgba(0,0,0,0.8), transparent);display:flex;align-items:center;gap:16px;">
            <button id="editor-play-btn" style="background:transparent;border:none;color:white;cursor:pointer;font-size:24px;width:32px;">▶</button>
            <div id="editor-time-display" style="color:white;font-family:var(--font-mono);font-size:13px;font-variant-numeric:tabular-nums;">00:00 / 00:00</div>
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

export function mount(videoSrc, onTimeUpdate) {
    const video = document.getElementById('editor-video');
    const playBtn = document.getElementById('editor-play-btn');
    const timeDisplay = document.getElementById('editor-time-display');

    if (videoSrc) {
        // the backend serves uploads at /static/uploads/
        // BUT the API returns storage_path which is an absolute path. 
        // We need the filename to construct the URL.
        const filename = videoSrc.split(/[/\\]/).pop();
        video.src = `/static/uploads/${filename}`;
    }

    video.addEventListener('timeupdate', () => {
        timeDisplay.textContent = `${formatTime(video.currentTime)} / ${formatTime(video.duration || 0)}`;
        if (onTimeUpdate) onTimeUpdate(video.currentTime);
    });

    video.addEventListener('loadedmetadata', () => {
        timeDisplay.textContent = `00:00 / ${formatTime(video.duration || 0)}`;
    });

    function togglePlay() {
        if (video.paused) {
            video.play();
            playBtn.textContent = '⏸';
        } else {
            video.pause();
            playBtn.textContent = '▶';
        }
    }

    playBtn.addEventListener('click', togglePlay);
    video.addEventListener('click', togglePlay);

    // Expose methods for other components to use
    return {
        play: () => { video.play(); playBtn.textContent = '⏸'; },
        pause: () => { video.pause(); playBtn.textContent = '▶'; },
        seek: (time) => { video.currentTime = time; },
        getCurrentTime: () => video.currentTime,
        getDuration: () => video.duration || 0
    };
}
