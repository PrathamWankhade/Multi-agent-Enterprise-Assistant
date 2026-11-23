"""API request/response schemas using Pydantic."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(..., description="User message")
    session_id: Optional[str] = Field(None, description="Session ID for conversation continuity")
    auto_approve: bool = Field(False, description="Auto-approve execution actions")


class ChunkResult(BaseModel):
    chunk_id: str
    text: str
    source: str
    doc_id: str
    page: Optional[int] = None
    section: Optional[str] = None
    metadata: Dict[str, Any] = {}
    score: Optional[float] = None


class AgentResult(BaseModel):
    agent: str
    task_id: str
    data: Dict[str, Any]


class ChatResponse(BaseModel):
    task_id: str
    session_id: str
    orchestrator_plan: Dict[str, Any]
    agent_results: List[AgentResult]
    final_response: str
    requires_approval: bool = False
    status: str = "completed"
    pending_action: Optional[Dict[str, Any]] = None
    review: Optional[Dict[str, Any]] = None
    itsm: Optional[Dict[str, Any]] = None
    trace: Optional[Dict[str, Any]] = None


class IngestRequest(BaseModel):
    text: Optional[str] = None
    source: Optional[str] = None
    doc_id: Optional[str] = None
    file_path: Optional[str] = None
    directory: Optional[str] = None
    chunk_size: int = 1000
    overlap: int = 200


class IngestResponse(BaseModel):
    status: str
    chunks_ingested: int
    doc_id: Optional[str] = None


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    min_score: float = 0.0
    filter_doc_id: Optional[str] = None


class SearchResponse(BaseModel):
    query: str
    results: List[ChunkResult]
    count: int


class HealthResponse(BaseModel):
    status: str
    llm_provider: str
    vector_store: str
    memory_backend: str


class ServiceRequestCreate(BaseModel):
    message: str = Field(..., description="Employee's issue description")
    requester: str = Field(default="employee", description="Employee name")
    department: str = Field(default="", description="Employee department")
    title: Optional[str] = Field(default=None, description="Optional explicit title")


class TicketDecision(BaseModel):
    priority: Optional[str] = Field(default=None, description="Admin priority override")
    technician: Optional[str] = Field(default=None, description="Specific technician to assign")
    note: Optional[str] = Field(default=None, description="Decision note")
    actor: str = Field(default="admin", description="Actor name")


class TicketUpdate(BaseModel):
    status: str = Field(..., description="Target lifecycle status")
    resolution: Optional[str] = Field(default=None)
    note: Optional[str] = Field(default=None)
    actor: str = Field(default="technician")