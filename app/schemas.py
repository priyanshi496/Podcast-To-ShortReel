from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Optional, List

# ── Project Schemas ────────────────────────────────────────────────────────────
class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None

class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None

class ProjectResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)

class ProjectSummary(BaseModel):
    """Lightweight summary for the Home screen Recent Projects list."""
    id: int
    name: str
    description: Optional[str] = None
    video_count: int
    clip_count: int
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class VideoBase(BaseModel):
    original_filename: str
    storage_path: str
    duration_sec: Optional[float] = None
    status: str = "uploaded"

class VideoCreate(VideoBase):
    pass

class VideoResponse(VideoBase):
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

# YouTube Download Schemas
class YouTubeInfoRequest(BaseModel):
    url: str

class YouTubeFormat(BaseModel):
    format_id: str
    resolution: str
    ext: str
    filesize_approx: Optional[int] = None
    format_note: Optional[str] = None

class YouTubeInfoResponse(BaseModel):
    url: str
    title: str
    thumbnail: Optional[str] = None
    formats: List[YouTubeFormat]

class YouTubeDownloadRequest(BaseModel):
    url: str
    format_id: str


# Job Schemas
class JobBase(BaseModel):
    video_id: int
    clip_candidate_id: Optional[int] = None
    job_type: str
    status: str = "queued"
    progress: float = 0.0
    error_message: Optional[str] = None


class JobCreate(JobBase):
    pass

class JobResponse(JobBase):
    id: int
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)

# Transcript Segment Schemas
class TranscriptSegmentBase(BaseModel):
    video_id: int
    speaker: Optional[str] = None
    start_time: float
    end_time: float
    text: str
    confidence: Optional[float] = None

class TranscriptSegmentCreate(TranscriptSegmentBase):
    pass

class TranscriptSegmentResponse(TranscriptSegmentBase):
    id: int
    model_config = ConfigDict(from_attributes=True)

# Clip Candidate Schemas
class ClipCandidateBase(BaseModel):
    video_id: int
    rank: Optional[int] = None
    start_time: float
    end_time: float
    duration_sec: float
    category: Optional[str] = None
    virality_score: float = 0.0
    hook_score: float = 0.0
    emotion_score: float = 0.0
    curiosity_score: float = 0.0
    surprise_score: float = 0.0
    reaction_score: float = 0.0
    context_completeness: float = 0.0
    
    # New short-form psychology scores
    scroll_stop_score: float = 0.0
    part2_score: float = 0.0
    rewatch_score: float = 0.0
    standalone_score: float = 0.0
    cliffhanger_score: float = 0.0

    reason: Optional[str] = None
    hook_line: Optional[str] = None
    transcript_excerpt: Optional[str] = None
    why_viewers_keep_watching: Optional[str] = None
    needs_manual_review: bool = False
    suggested_title: Optional[str] = None
    suggested_caption: Optional[str] = None
    best_aspect_ratio: str = "9:16"

    # New narrative attributes
    section_type: Optional[str] = None
    narrative_summary: Optional[str] = None
    cut_rationale: Optional[str] = None

    status: str = "suggested"
    source: str = "ai"   # "ai" | "manual"
    title: Optional[str] = None


class ClipCandidateCreate(ClipCandidateBase):
    pass

class ClipCandidateTrim(BaseModel):
    start_time: float
    end_time: float

# Compile Schemas
class CompilePart(BaseModel):
    start_time: float
    end_time: float

class CompileRequest(BaseModel):
    parts: List[CompilePart]

class ClipExportResponse(BaseModel):
    id: int
    clip_candidate_id: int
    file_path: str
    subtitle_path: Optional[str] = None
    format: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class ClipCandidateResponse(ClipCandidateBase):
    id: int
    exports: List[ClipExportResponse] = []
    model_config = ConfigDict(from_attributes=True)
