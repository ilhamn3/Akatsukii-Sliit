"""Agent 1 input/output contracts; timestamps are offsets in seconds.

Whisper diagnostics are quality signals, not calibrated probabilities of
transcript correctness. Flagged text is retained for human inspection.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CaptureModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class CaptureRequest(CaptureModel):
    source_path: str = Field(min_length=1)
    media_type: Literal["audio", "video"] = "audio"
    language: str = "en"
    frame_interval_seconds: float = Field(default=10.0, gt=0)
    max_frames: int = Field(default=12, ge=1, le=100)
    frame_output_dir: str = "logs/capture_frames"


class TranscriptSegment(CaptureModel):
    evidence_id: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    text: str = Field(min_length=1)
    avg_logprob: Optional[float] = None
    no_speech_prob: Optional[float] = Field(default=None, ge=0, le=1)
    compression_ratio: Optional[float] = Field(default=None, ge=0)
    quality_flags: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_interval(self):
        if self.end_seconds < self.start_seconds:
            raise ValueError("Segment end must not precede its start")
        return self


class VideoFrame(CaptureModel):
    evidence_id: str
    timestamp_seconds: float = Field(ge=0)
    path: str


class CaptureResult(CaptureModel):
    capture_id: str
    source_path: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: Literal["audio", "video"]
    language: str
    text: str
    segments: List[TranscriptSegment]
    frames: List[VideoFrame] = Field(default_factory=list)
    quality_flags: List[str] = Field(default_factory=list)
    review_required: bool
