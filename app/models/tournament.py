import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, Integer, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from app.database import Base

class Tournament(Base):
    __tablename__ = "tournaments"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(128), nullable=False)
    slug = Column(String(128), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    game_key = Column(String(32), default="lichess", nullable=False)
    mode = Column(String(32), default="rapid", nullable=False)
    hub_id = Column(String(36), ForeignKey("hubs.id", ondelete="SET NULL"), nullable=True)
    city = Column(String(64), default="Addis Ababa", nullable=False, index=True)
    status = Column(String(32), default="registration_open", nullable=False, index=True)
    # draft, registration_open, in_progress, completed, cancelled
    max_participants = Column(Integer, default=16, nullable=False)
    start_time = Column(DateTime, nullable=False)
    registration_deadline = Column(DateTime, nullable=False)
    rules_text = Column(Text, nullable=True)
    prize_description = Column(Text, default="Honor, Platform Elo, and City Ladder Standing", nullable=True)
    created_by_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    is_demo = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    hub = relationship("Hub", back_populates="tournaments")
    participants = relationship("TournamentParticipant", back_populates="tournament", cascade="all, delete-orphan")
    matches = relationship("Match", back_populates="tournament", cascade="all, delete-orphan")

class TournamentParticipant(Base):
    __tablename__ = "tournament_participants"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tournament_id = Column(String(36), ForeignKey("tournaments.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    seed = Column(Integer, nullable=True)
    status = Column(String(32), default="registered", nullable=False)  # registered, active, eliminated, winner
    registered_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint('tournament_id', 'user_id', name='uq_tournament_user'),
    )

    tournament = relationship("Tournament", back_populates="participants")
    user = relationship("User", back_populates="tournament_participations")


class TournamentQualificationPath(Base):
    __tablename__ = "tournament_qualification_paths"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_tournament_id = Column(String(36), nullable=False, unique=True, index=True)
    destination_tournament_id = Column(String(36), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
