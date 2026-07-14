from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
from app.db import Base


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    videos = relationship("Video", back_populates="project", cascade="all, delete-orphan")


class Video(Base):
    __tablename__ = "videos"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True)
    original_filename = Column(String, nullable=False)
    storage_path = Column(String, nullable=False)
    duration_sec = Column(Float, nullable=True)
    status = Column(String, default="uploaded")  # uploaded, processing, transcribed, processed, failed
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="videos")
    jobs = relationship("Job", back_populates="video", cascade="all, delete-orphan")
    segments = relationship("TranscriptSegment", back_populates="video", cascade="all, delete-orphan")
    clips = relationship("ClipCandidate", back_populates="video", cascade="all, delete-orphan")

class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    video_id = Column(Integer, ForeignKey("videos.id"), nullable=False)
    clip_candidate_id = Column(Integer, ForeignKey("clip_candidates.id"), nullable=True)
    job_type = Column(String, nullable=False)  # transcribe, rank, render
    status = Column(String, default="queued")  # queued, running, done, failed
    progress = Column(Float, default=0.0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    video = relationship("Video", back_populates="jobs")
    clip_candidate = relationship("ClipCandidate")


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"

    id = Column(Integer, primary_key=True, index=True)
    video_id = Column(Integer, ForeignKey("videos.id"), nullable=False)
    speaker = Column(String, nullable=True)
    start_time = Column(Float, nullable=False)
    end_time = Column(Float, nullable=False)
    text = Column(Text, nullable=False)
    confidence = Column(Float, nullable=True)

    video = relationship("Video", back_populates="segments")

class ClipCandidate(Base):
    __tablename__ = "clip_candidates"

    id = Column(Integer, primary_key=True, index=True)
    video_id = Column(Integer, ForeignKey("videos.id"), nullable=False)
    rank = Column(Integer, nullable=True)
    start_time = Column(Float, nullable=False)
    end_time = Column(Float, nullable=False)
    duration_sec = Column(Float, nullable=False)
    category = Column(String, nullable=True)
    
    virality_score = Column(Float, default=0.0)
    hook_score = Column(Float, default=0.0)
    emotion_score = Column(Float, default=0.0)
    curiosity_score = Column(Float, default=0.0)
    surprise_score = Column(Float, default=0.0)
    reaction_score = Column(Float, default=0.0)
    context_completeness = Column(Float, default=0.0)
    
    # New short-form psychology scores
    scroll_stop_score = Column(Float, default=0.0)
    part2_score = Column(Float, default=0.0)
    rewatch_score = Column(Float, default=0.0)
    standalone_score = Column(Float, default=0.0)
    cliffhanger_score = Column(Float, default=0.0)

    reason = Column(Text, nullable=True)
    hook_line = Column(Text, nullable=True)
    transcript_excerpt = Column(Text, nullable=True)
    why_viewers_keep_watching = Column(Text, nullable=True)
    needs_manual_review = Column(Boolean, default=False)
    suggested_title = Column(String, nullable=True)
    suggested_caption = Column(Text, nullable=True)
    best_aspect_ratio = Column(String, default="9:16")

    # New narrative attributes
    section_type = Column(String, nullable=True)
    narrative_summary = Column(Text, nullable=True)
    cut_rationale = Column(Text, nullable=True)

    # Clip identity
    title = Column(String, nullable=True)            # user-editable display name
    source = Column(String, default="ai")             # "ai" | "manual"
    status = Column(String, default="suggested")     # suggested, approved, rejected, trimmed, rendered

    video = relationship("Video", back_populates="clips")
    exports = relationship("ClipExport", back_populates="clip_candidate", cascade="all, delete-orphan")


class ClipExport(Base):
    __tablename__ = "clip_exports"

    id = Column(Integer, primary_key=True, index=True)
    clip_candidate_id = Column(Integer, ForeignKey("clip_candidates.id"), nullable=False)
    file_path = Column(String, nullable=False)
    subtitle_path = Column(String, nullable=True)
    format = Column(String, nullable=True)  # vertical (9:16) or landscape (16:9)
    created_at = Column(DateTime, default=datetime.utcnow)

    clip_candidate = relationship("ClipCandidate", back_populates="exports")
