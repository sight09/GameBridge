from datetime import datetime
from typing import Optional, Dict, Any, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func
from app.models.match import Match, MatchResultEvent, MatchReport, MatchEvidence
from app.models.tournament import Tournament, TournamentParticipant, TournamentQualificationPath
from app.models.game import GameAccount
from app.models.dispute import Dispute
from app.adapters.lichess import LichessAdapter
from app.services.rating_service import RatingService
from app.services.badge_service import BadgeService
from app.services.tournament_service import TournamentService
from app.utils.logger import logger

class MatchService:
    @staticmethod
    async def submit_lichess_result(
        db: AsyncSession,
        match_id: str,
        user_id: str,
        game_link: str
    ) -> Dict[str, Any]:
        stmt = select(Match).where(Match.id == match_id)
        match = (await db.execute(stmt)).scalar_one_or_none()
        if not match:
            return {"success": False, "message": "Match not found."}

        if match.status in ["completed", "forfeited"]:
            return {"success": False, "message": "Match is already completed."}

        # Verify submitting user is one of the match players
        if user_id != match.player1_id and user_id != match.player2_id:
            return {"success": False, "message": "User is not a participant in this match."}

        # Fetch players' linked Lichess usernames
        acc1_stmt = select(GameAccount).where(GameAccount.user_id == match.player1_id, GameAccount.game_key == "lichess")
        acc2_stmt = select(GameAccount).where(GameAccount.user_id == match.player2_id, GameAccount.game_key == "lichess")
        acc1 = (await db.execute(acc1_stmt)).scalar_one_or_none()
        acc2 = (await db.execute(acc2_stmt)).scalar_one_or_none()

        if not acc1 or not acc2:
            return {"success": False, "message": "Both players must have linked Lichess accounts to auto-verify."}
        if acc1.status != "verified" or acc2.status != "verified":
            return {"success": False, "message": "Both players must verify their Lichess accounts before result checking."}

        # Check duplicate game usage across all match reports
        dup_stmt = select(MatchReport).where(MatchReport.game_link.contains(game_link.strip().split('/')[-1]))
        dup_report = (await db.execute(dup_stmt)).scalar_one_or_none()
        if dup_report and dup_report.match_id != match_id:
            return {"success": False, "message": "This Lichess game has already been submitted for another match."}

        # Call Lichess adapter verification
        adapter = LichessAdapter()
        verify_res = await adapter.verify_match_game(
            game_id_or_url=game_link,
            player1_username=acc1.external_username,
            player2_username=acc2.external_username
        )

        if not verify_res.get("valid"):
            # Create a report but flag dispute or review
            report = MatchReport(
                match_id=match_id,
                reporting_user_id=user_id,
                claimed_winner_id=user_id,
                game_link=game_link,
                comments=f"Automated check failed: {verify_res.get('error')}"
            )
            db.add(report)
            await db.commit()
            return {"success": False, "message": verify_res.get("error")}

        # Determine winner
        winner_username = verify_res.get("winner_username")
        winner_id = None
        outcome = "draw"

        if winner_username:
            if winner_username.lower() == acc1.external_username.lower():
                winner_id = match.player1_id
                outcome = "player1_win"
            elif winner_username.lower() == acc2.external_username.lower():
                winner_id = match.player2_id
                outcome = "player2_win"

        # Record MatchReport
        report = MatchReport(
            match_id=match_id,
            reporting_user_id=user_id,
            claimed_winner_id=winner_id or user_id,
            game_link=game_link,
            comments="Verified automatically via Lichess official API."
        )
        db.add(report)

        # Record Append-Only MatchResultEvent
        event = MatchResultEvent(
            match_id=match_id,
            acting_user_id=user_id,
            outcome=outcome,
            winner_id=winner_id,
            reason="Automated verification via Lichess API export."
        )
        db.add(event)

        # Finalize match
        match.status = "completed"
        match.winner_id = winner_id

        # Update tournament bracket advancement
        await MatchService._advance_bracket_winner(db, match, winner_id)

        # Apply platform Elo rating update
        tourney_stmt = select(Tournament).where(Tournament.id == match.tournament_id)
        tourney = (await db.execute(tourney_stmt)).scalar_one_or_none()
        mode = tourney.mode if tourney else "rapid"

        rating_res = await RatingService.apply_match_elo_update(
            db=db,
            player1_id=match.player1_id,
            player2_id=match.player2_id,
            winner_id=winner_id,
            game_key="lichess",
            mode=mode
        )

        # Check and award badges
        if winner_id:
            await BadgeService.check_and_award_win_badges(db, winner_id)

        await db.commit()
        return {
            "success": True,
            "message": "Game result verified and confirmed!",
            "winner_id": winner_id,
            "rating_update": rating_res
        }

    @staticmethod
    async def submit_screenshot_report(
        db: AsyncSession,
        match_id: str,
        user_id: str,
        claimed_winner_id: str,
        score_p1: int,
        score_p2: int,
        comments: Optional[str] = None
    ) -> Dict[str, Any]:
        stmt = select(Match).where(Match.id == match_id)
        match = (await db.execute(stmt)).scalar_one_or_none()
        if not match:
            return {"success": False, "message": "Match not found."}

        if user_id not in (match.player1_id, match.player2_id):
            return {"success": False, "message": "Only match participants can report a result."}
        if match.status not in ["pending", "scheduled", "in_progress"]:
            return {"success": False, "message": "This match is not accepting result reports."}
        if claimed_winner_id not in (match.player1_id, match.player2_id):
            return {"success": False, "message": "The claimed winner must be a match participant."}
        if score_p1 == score_p2:
            return {"success": False, "message": "A winning result must have a non-tied score."}
        if (claimed_winner_id == match.player1_id and score_p1 < score_p2) or (
            claimed_winner_id == match.player2_id and score_p2 < score_p1
        ):
            return {"success": False, "message": "The score does not match the claimed winner."}

        # Check existing report by this user
        existing_stmt = select(MatchReport).where(MatchReport.match_id == match_id, MatchReport.reporting_user_id == user_id)
        existing = (await db.execute(existing_stmt)).scalar_one_or_none()
        if existing:
            return {"success": False, "message": "You have already submitted a report for this match."}

        report = MatchReport(
            match_id=match_id,
            reporting_user_id=user_id,
            claimed_winner_id=claimed_winner_id,
            score_p1=score_p1,
            score_p2=score_p2,
            comments=comments
        )
        db.add(report)
        await db.flush()

        # Check if other player has reported
        other_user_id = match.player2_id if user_id == match.player1_id else match.player1_id
        other_stmt = select(MatchReport).where(MatchReport.match_id == match_id, MatchReport.reporting_user_id == other_user_id)
        other_report = (await db.execute(other_stmt)).scalar_one_or_none()

        if not other_report:
            match.status = "in_progress"
            await db.commit()
            return {"success": True, "message": "Report submitted. Awaiting opponent report."}

        # Both reported! Compare outcomes
        if other_report.claimed_winner_id == claimed_winner_id:
            # Agreement! Auto-confirm
            outcome = "player1_win" if claimed_winner_id == match.player1_id else "player2_win"
            event = MatchResultEvent(
                match_id=match_id,
                acting_user_id=user_id,
                outcome=outcome,
                winner_id=claimed_winner_id,
                score_p1=score_p1,
                score_p2=score_p2,
                reason="Both players submitted agreeing reports."
            )
            db.add(event)
            match.status = "completed"
            match.winner_id = claimed_winner_id

            await MatchService._advance_bracket_winner(db, match, claimed_winner_id)
            await RatingService.apply_match_elo_update(
                db, match.player1_id, match.player2_id, claimed_winner_id, "tier_b", "standard"
            )
            await BadgeService.check_and_award_win_badges(db, claimed_winner_id)
            await db.commit()
            return {"success": True, "message": "Both reports agree! Match completed."}
        else:
            # Conflict! Open dispute automatically
            match.status = "disputed"
            dispute = Dispute(
                match_id=match_id,
                opened_by_id=user_id,
                status="open",
                reason=f"Conflicting match reports: Player {user_id} claimed {claimed_winner_id}, opponent claimed {other_report.claimed_winner_id}."
            )
            db.add(dispute)
            await db.commit()
            return {"success": True, "message": "Conflicting reports detected. A dispute has been opened for Hub Captain review."}

    @staticmethod
    async def _advance_bracket_winner(db: AsyncSession, match: Match, winner_id: Optional[str]):
        if not winner_id:
            return

        # Find next round match
        next_round = match.round_number + 1
        target_match_number = (match.match_number + 1) // 2
        is_p1_slot = (match.match_number % 2 == 1)

        next_stmt = select(Match).where(
            Match.tournament_id == match.tournament_id,
            Match.round_number == next_round,
            Match.match_number == target_match_number
        )
        next_m = (await db.execute(next_stmt)).scalar_one_or_none()

        if next_m:
            if is_p1_slot:
                next_m.player1_id = winner_id
            else:
                next_m.player2_id = winner_id

            if next_m.player1_id and next_m.player2_id:
                next_m.status = "scheduled"
        else:
            # No next match exists => This was the Final match!
            tourney_stmt = select(Tournament).where(Tournament.id == match.tournament_id)
            tourney = (await db.execute(tourney_stmt)).scalar_one_or_none()
            if tourney:
                tourney.status = "completed"
                # Mark tournament participant as winner
                part_stmt = select(TournamentParticipant).where(
                    TournamentParticipant.tournament_id == tourney.id,
                    TournamentParticipant.user_id == winner_id
                )
                winner_part = (await db.execute(part_stmt)).scalar_one_or_none()
                if winner_part:
                    winner_part.status = "winner"
                # Award City Cup Champion badge!
                await BadgeService.award_badge(db, winner_id, "city_champion")
                await MatchService._advance_top_qualifiers(db, tourney, winner_id)

    @staticmethod
    async def _advance_top_qualifiers(
        db: AsyncSession,
        source: Tournament,
        winner_id: str
    ) -> None:
        path = (await db.execute(
            select(TournamentQualificationPath).where(
                TournamentQualificationPath.source_tournament_id == source.id
            )
        )).scalar_one_or_none()
        if not path:
            return

        destination = (await db.execute(
            select(Tournament).where(Tournament.id == path.destination_tournament_id)
        )).scalar_one_or_none()
        if not destination or destination.status != "registration_open":
            raise ValueError("The linked next-stage tournament is not open for qualification.")

        participants = (await db.execute(
            select(TournamentParticipant)
            .where(TournamentParticipant.tournament_id == source.id)
            .order_by(TournamentParticipant.seed, TournamentParticipant.registered_at)
        )).scalars().all()
        matches = (await db.execute(
            select(Match).where(
                Match.tournament_id == source.id,
                Match.status == "completed"
            )
        )).scalars().all()

        furthest_round = {}
        for completed_match in matches:
            for player_id in (completed_match.player1_id, completed_match.player2_id):
                if player_id:
                    furthest_round[player_id] = max(
                        furthest_round.get(player_id, 0),
                        completed_match.round_number
                    )

        participants.sort(key=lambda participant: (
            participant.user_id != winner_id,
            -furthest_round.get(participant.user_id, 0),
            participant.seed if participant.seed is not None else float("inf"),
            participant.registered_at
        ))
        qualifiers = participants[:TournamentService.QUALIFICATION_SLOTS]

        existing_ids = set((await db.execute(
            select(TournamentParticipant.user_id).where(
                TournamentParticipant.tournament_id == destination.id,
                TournamentParticipant.user_id.in_([p.user_id for p in qualifiers])
            )
        )).scalars().all()) if qualifiers else set()
        destination_count = (await db.execute(
            select(func.count(TournamentParticipant.id)).where(
                TournamentParticipant.tournament_id == destination.id
            )
        )).scalar_one()
        new_qualifiers = [p for p in qualifiers if p.user_id not in existing_ids]
        if destination_count + len(new_qualifiers) > destination.max_participants:
            raise ValueError("The next-stage tournament no longer has room for all qualifiers.")

        for place, participant in enumerate(qualifiers, start=1):
            participant.status = "winner" if place == 1 else "qualified"
            if participant.user_id not in existing_ids:
                db.add(TournamentParticipant(
                    tournament_id=destination.id,
                    user_id=participant.user_id,
                    status="qualified"
                ))

        await db.flush()
