import secrets
from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func
from app.models.tournament import Tournament, TournamentParticipant, TournamentQualificationPath
from app.models.match import Match
from app.models.ratings import PlatformRating
from app.tournaments.bracket import build_single_elimination_bracket
from app.utils.logger import logger

class TournamentService:
    QUALIFICATION_SLOTS = 4

    @staticmethod
    async def _reserved_qualification_slots(db: AsyncSession, tournament_id: str) -> int:
        paths = (await db.execute(
            select(TournamentQualificationPath)
            .join(Tournament, Tournament.id == TournamentQualificationPath.source_tournament_id)
            .where(
                TournamentQualificationPath.destination_tournament_id == tournament_id,
                Tournament.status != "completed"
            )
        )).scalars().all()
        return len(paths) * TournamentService.QUALIFICATION_SLOTS

    @staticmethod
    async def link_qualification_path(
        db: AsyncSession,
        source_tournament_id: str,
        destination_tournament_id: str
    ) -> TournamentQualificationPath:
        if source_tournament_id == destination_tournament_id:
            raise ValueError("A tournament cannot qualify players into itself.")

        source = (await db.execute(
            select(Tournament).where(Tournament.id == source_tournament_id)
        )).scalar_one_or_none()
        destination = (await db.execute(
            select(Tournament).where(Tournament.id == destination_tournament_id)
        )).scalar_one_or_none()
        if not source or not destination:
            raise ValueError("Source and destination tournaments must both exist.")
        if source.status == "completed":
            raise ValueError("A completed tournament cannot be linked to a qualification stage.")
        if destination.status != "registration_open":
            raise ValueError("The next-stage tournament must be open for registration.")
        if (source.game_key, source.mode) != (destination.game_key, destination.mode):
            raise ValueError("Qualification tournaments must use the same game and mode.")

        existing = (await db.execute(
            select(TournamentQualificationPath).where(
                TournamentQualificationPath.source_tournament_id == source_tournament_id
            )
        )).scalar_one_or_none()
        if existing:
            raise ValueError("This tournament already has a configured next stage.")

        next_stage_id = destination_tournament_id
        visited = {source_tournament_id}
        while True:
            if next_stage_id in visited:
                raise ValueError("This qualification link would create a tournament pathway cycle.")
            visited.add(next_stage_id)
            next_path = (await db.execute(
                select(TournamentQualificationPath).where(
                    TournamentQualificationPath.source_tournament_id == next_stage_id
                )
            )).scalar_one_or_none()
            if not next_path:
                break
            next_stage_id = next_path.destination_tournament_id

        destination_count = (await db.execute(
            select(func.count(TournamentParticipant.id)).where(
                TournamentParticipant.tournament_id == destination_tournament_id
            )
        )).scalar_one()
        reserved_slots = await TournamentService._reserved_qualification_slots(
            db, destination_tournament_id
        )
        if destination_count + reserved_slots + TournamentService.QUALIFICATION_SLOTS > destination.max_participants:
            raise ValueError("The next-stage tournament needs four available places for qualifiers.")

        path = TournamentQualificationPath(
            source_tournament_id=source_tournament_id,
            destination_tournament_id=destination_tournament_id
        )
        db.add(path)
        await db.commit()
        await db.refresh(path)
        return path

    @staticmethod
    async def create_tournament(
        db: AsyncSession,
        name: str,
        game_key: str,
        mode: str,
        city: str,
        start_time: datetime,
        registration_deadline: datetime,
        hub_id: Optional[str] = None,
        max_participants: int = 16,
        description: Optional[str] = None,
        rules_text: Optional[str] = None,
        prize_description: Optional[str] = "Honor, Platform Elo, and City Ladder Standing",
        created_by_id: Optional[str] = None,
        is_demo: bool = False
    ) -> Tournament:
        slug = f"{name.lower().replace(' ', '-')[:40]}-{secrets.token_hex(3)}"
        tourney = Tournament(
            name=name,
            slug=slug,
            game_key=game_key,
            mode=mode,
            city=city,
            hub_id=hub_id,
            status="registration_open",
            max_participants=max_participants,
            start_time=start_time,
            registration_deadline=registration_deadline,
            description=description,
            rules_text=rules_text,
            prize_description=prize_description,
            created_by_id=created_by_id,
            is_demo=is_demo
        )
        db.add(tourney)
        await db.commit()
        await db.refresh(tourney)
        return tourney

    @staticmethod
    async def register_player(
        db: AsyncSession,
        tournament_id: str,
        user_id: str
    ) -> TournamentParticipant:
        stmt = select(Tournament).where(Tournament.id == tournament_id)
        tourney = (await db.execute(stmt)).scalar_one_or_none()
        if not tourney:
            raise ValueError("Tournament not found.")

        if tourney.status != "registration_open":
            raise ValueError(f"Registration is not open (status: {tourney.status}).")

        if datetime.utcnow() > tourney.registration_deadline:
            raise ValueError("Registration deadline has passed.")

        # Check existing registration
        part_stmt = select(TournamentParticipant).where(
            TournamentParticipant.tournament_id == tournament_id,
            TournamentParticipant.user_id == user_id
        )
        existing = (await db.execute(part_stmt)).scalar_one_or_none()
        if existing:
            raise ValueError("Player is already registered for this tournament.")

        # Check capacity
        current_count = (await db.execute(
            select(func.count(TournamentParticipant.id)).where(
                TournamentParticipant.tournament_id == tournament_id
            )
        )).scalar_one()
        reserved_slots = await TournamentService._reserved_qualification_slots(db, tournament_id)
        if current_count + reserved_slots >= tourney.max_participants:
            raise ValueError("Tournament has reached maximum participant capacity.")

        participant = TournamentParticipant(
            tournament_id=tournament_id,
            user_id=user_id,
            status="registered"
        )
        db.add(participant)
        await db.commit()
        await db.refresh(participant)
        return participant

    @staticmethod
    async def start_and_generate_bracket(
        db: AsyncSession,
        tournament_id: str
    ) -> List[Match]:
        stmt = select(Tournament).where(Tournament.id == tournament_id)
        tourney = (await db.execute(stmt)).scalar_one_or_none()
        if not tourney:
            raise ValueError("Tournament not found.")
        if tourney.status != "registration_open":
            raise ValueError(f"Tournament cannot be started from status '{tourney.status}'.")

        pending_feeders = (await db.execute(
            select(TournamentQualificationPath)
            .join(Tournament, Tournament.id == TournamentQualificationPath.source_tournament_id)
            .where(
                TournamentQualificationPath.destination_tournament_id == tournament_id,
                Tournament.status != "completed"
            )
        )).scalars().first()
        if pending_feeders:
            raise ValueError("Wait for all linked qualifying tournaments to finish before starting this stage.")

        # Fetch participants
        part_stmt = select(TournamentParticipant).where(TournamentParticipant.tournament_id == tournament_id)
        participants = (await db.execute(part_stmt)).scalars().all()
        if len(participants) < 2:
            raise ValueError("Cannot start tournament with fewer than 2 participants.")

        # Sort participants by platform Elo rating descending for seeding
        participant_ratings = []
        for p in participants:
            r_stmt = select(PlatformRating).where(
                PlatformRating.user_id == p.user_id,
                PlatformRating.game_key == tourney.game_key,
                PlatformRating.mode == tourney.mode
            )
            r = (await db.execute(r_stmt)).scalar_one_or_none()
            elo = r.elo if r else 1200.0
            participant_ratings.append((p, elo))

        participant_ratings.sort(key=lambda x: x[1], reverse=True)

        # Assign seeds
        sorted_user_ids = []
        for seed_idx, (p, _) in enumerate(participant_ratings, start=1):
            p.seed = seed_idx
            p.status = "active"
            sorted_user_ids.append(p.user_id)

        # Generate bracket matches
        match_defs = build_single_elimination_bracket(sorted_user_ids)

        created_matches: List[Match] = []
        round_matches_db: Dict[int, List[Match]] = {}

        for m_def in match_defs:
            m = Match(
                tournament_id=tournament_id,
                round_number=m_def["round_number"],
                match_number=m_def["match_number"],
                player1_id=m_def["player1_id"],
                player2_id=m_def["player2_id"],
                winner_id=m_def["winner_id"],
                status=m_def["status"]
            )
            db.add(m)
            created_matches.append(m)
            round_matches_db.setdefault(m_def["round_number"], []).append(m)

        tourney.status = "in_progress"
        await db.commit()
        return created_matches
