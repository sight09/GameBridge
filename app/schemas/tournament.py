from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class TournamentCreate(BaseModel):
    name: str = Field(..., min_length=3, max_length=128)
    game_key: str = Field(default="lichess")
    mode: str = Field(default="rapid")
    city: str = Field(default="Addis Ababa")
    hub_id: Optional[str] = None
    max_participants: int = Field(default=16, ge=4, le=64)
    start_time: datetime
    registration_deadline: datetime
    description: Optional[str] = None
    rules_text: Optional[str] = None
    prize_description: Optional[str] = "Honor, Platform Elo, and City Ladder Standing"

class TournamentOut(BaseModel):
    id: str
    name: str
    slug: str
    game_key: str
    mode: str
    city: str
    hub_id: Optional[str]
    status: str
    max_participants: int
    start_time: datetime
    registration_deadline: datetime
    prize_description: Optional[str]
    participant_count: Optional[int] = 0

    class Config:
        from_attributes = True


class TournamentQualificationPathCreate(BaseModel):
    destination_tournament_id: str = Field(..., min_length=1, max_length=36)
