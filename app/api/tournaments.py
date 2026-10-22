from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from app.database import get_db
from app.models.tournament import Tournament, TournamentParticipant
from app.models.match import Match
from app.models.user import User
from app.models.hub import Hub, HubMembership
from app.schemas.tournament import TournamentCreate, TournamentQualificationPathCreate
from app.services.tournament_service import TournamentService
from app.auth.dependencies import get_current_user, require_captain, user_manages_hub

router = APIRouter(prefix="/api/tournaments", tags=["tournaments"])

@router.post("/{tournament_id}/qualification-path")
async def configure_qualification_path(
    tournament_id: str,
    data: TournamentQualificationPathCreate,
    db: AsyncSession = Depends(get_db),
    captain: User = Depends(require_captain)
):
    source = (await db.execute(
        select(Tournament).where(Tournament.id == tournament_id)
    )).scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="Tournament not found.")
    if not await user_manages_hub(db, captain, source.hub_id):
        raise HTTPException(status_code=403, detail="You can only configure qualification for tournaments in your assigned hub.")

    try:
        path = await TournamentService.link_qualification_path(
            db=db,
            source_tournament_id=source.id,
            destination_tournament_id=data.destination_tournament_id
        )
        return {
            "success": True,
            "source_tournament_id": path.source_tournament_id,
            "destination_tournament_id": path.destination_tournament_id,
            "qualifying_places": TournamentService.QUALIFICATION_SLOTS
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("")
async def list_tournaments(db: AsyncSession = Depends(get_db)):
    stmt = select(Tournament).order_by(desc(Tournament.created_at))
    tourneys = (await db.execute(stmt)).scalars().all()
    return [{
        "id": t.id,
        "name": t.name,
        "slug": t.slug,
        "game_key": t.game_key,
        "mode": t.mode,
        "city": t.city,
        "status": t.status,
        "max_participants": t.max_participants,
        "start_time": t.start_time.isoformat(),
        "registration_deadline": t.registration_deadline.isoformat(),
        "prize_description": t.prize_description,
        "is_demo": t.is_demo
    } for t in tourneys]

@router.post("")
async def create_tournament(
    data: TournamentCreate,
    db: AsyncSession = Depends(get_db),
    captain: User = Depends(require_captain)
):
    try:
        hub_id = data.hub_id
        if captain.role != "super_admin":
            if hub_id:
                if not await user_manages_hub(db, captain, hub_id):
                    raise HTTPException(status_code=403, detail="You can only manage tournaments in your assigned hub.")
            else:
                hub_stmt = select(Hub.id).join(
                    HubMembership, Hub.id == HubMembership.hub_id
                ).where(
                    HubMembership.user_id == captain.id,
                    HubMembership.role == "captain",
                    Hub.city == data.city,
                    Hub.is_active == True
                ).limit(1)
                hub_id = (await db.execute(hub_stmt)).scalar_one_or_none()
                if not hub_id:
                    raise HTTPException(status_code=403, detail="No captain membership exists for the selected city.")
        tourney = await TournamentService.create_tournament(
            db=db,
            name=data.name,
            game_key=data.game_key,
            mode=data.mode,
            city=data.city,
            hub_id=hub_id,
            max_participants=data.max_participants,
            start_time=data.start_time,
            registration_deadline=data.registration_deadline,
            description=data.description,
            rules_text=data.rules_text,
            prize_description=data.prize_description,
            created_by_id=captain.id
        )
        return {"success": True, "id": tourney.id, "slug": tourney.slug}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{tournament_id}/join")
async def join_tournament(
    tournament_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    try:
        part = await TournamentService.register_player(db, tournament_id, user.id)
        return {"success": True, "participant_id": part.id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{tournament_id}/start")
async def start_tournament(
    tournament_id: str,
    db: AsyncSession = Depends(get_db),
    captain: User = Depends(require_captain)
):
    try:
        tourney = (await db.execute(select(Tournament).where(Tournament.id == tournament_id))).scalar_one_or_none()
        if not tourney:
            raise HTTPException(status_code=404, detail="Tournament not found.")
        if not await user_manages_hub(db, captain, tourney.hub_id):
            raise HTTPException(status_code=403, detail="You can only start tournaments in your assigned hub.")
        matches = await TournamentService.start_and_generate_bracket(db, tournament_id)
        return {"success": True, "total_matches": len(matches)}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
