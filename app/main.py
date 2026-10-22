from app.models.hub import Hub, HubMembership
import os
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, Request, Depends, HTTPException, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func, or_

from app.config import settings
from app.database import get_db, engine, AsyncSessionLocal
from app.database_init import init_db
from app.models.user import User, UserPreference
from app.models.tournament import Tournament, TournamentParticipant, TournamentQualificationPath
from app.models.match import Match, MatchResultEvent, MatchEvidence
from app.models.game import GameAccount
from app.models.ratings import PlatformRating
from app.models.badge import UserBadge, Badge
from app.models.dispute import Dispute
from app.services.rating_service import RatingService
from app.services.analytics_service import AnalyticsService
from app.services.card_generator import generate_player_share_card
from app.services.demo_service import DemoService
from app.localization.i18n import t
from app.auth.dependencies import (
    get_current_user_optional,
    get_managed_hub_ids,
    require_captain,
    user_manages_hub,
)
from app.utils.logger import logger

# Import API Routers
from app.api.auth import router as auth_router
from app.api.tournaments import router as tournaments_router
from app.api.matches import router as matches_router
from app.api.disputes import router as disputes_router
from app.api.games import router as games_router
from app.api.telegram_webhook import router as telegram_router
from app.api.analytics import router as analytics_router
from app.api.demo import router as demo_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing GameBridge application...")
    await init_db()
    # Auto-seed initial demo dataset if running in demo mode and database is fresh
    if settings.DEMO_MODE:
        async with AsyncSessionLocal() as session:
            await DemoService.remove_demo_captain_review_fixture(session)
            stmt = select(Tournament).where(Tournament.is_demo == True)
            existing = (await session.execute(stmt)).first()
            if not existing:
                logger.info("Demo mode enabled & fresh DB: seeding demo dataset...")
                await DemoService.seed_demo_data(session)
    logger.info("GameBridge startup complete.")
    yield
    logger.info("GameBridge application shutting down...")

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    lifespan=lifespan
)

# Mount static and uploads
os.makedirs("app/static", exist_ok=True)
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")

def render_template(request: Request, name: str, context: dict) -> HTMLResponse:
    context["request"] = request
    return templates.TemplateResponse(request=request, name=name, context=context)

def get_locale_for_request(request: Request, user: Optional[User]) -> str:
    cookie_lang = request.cookies.get("gb_lang")
    if cookie_lang in settings.SUPPORTED_LOCALES:
        return cookie_lang
    return settings.DEFAULT_LOCALE

# Health & Readiness Checks
@app.get("/health", tags=["monitoring"])
async def health_check():
    return {"status": "ok", "app": settings.PROJECT_NAME, "version": settings.VERSION}

@app.get("/ready", tags=["monitoring"])
async def readiness_check(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(select(1))
        return {"status": "ready", "database": "connected"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {str(e)}")

# Include API Routers
app.include_router(auth_router)
app.include_router(tournaments_router)
app.include_router(matches_router)
app.include_router(disputes_router)
app.include_router(games_router)
app.include_router(telegram_router)
app.include_router(analytics_router)
app.include_router(demo_router)

# Service Worker route at root
@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    with open("app/static/sw.js", "r", encoding="utf-8") as f:
        return Response(content=f.read(), media_type="application/javascript")

# --- HTML Web Pages ---

@app.get("/", response_class=HTMLResponse)
async def page_index(
    request: Request,
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db)
):
    locale = get_locale_for_request(request, user)
    featured_players = await RatingService.get_platform_leaderboard(
        db=db,
        game_key="lichess",
        mode="rapid",
        city="All",
        limit=3
    )
    tournaments_stmt = select(Tournament).where(
        Tournament.status.in_(["registration_open", "in_progress"])
    ).order_by(Tournament.start_time).limit(3)
    featured_tournaments = (await db.execute(tournaments_stmt)).scalars().all()
    return render_template(request, "index.html", {
        "current_user": user,
        "featured_players": featured_players,
        "featured_tournaments": featured_tournaments,
        "locale": locale,
        "t": t,
        "is_demo": settings.DEMO_MODE
    })

@app.get("/login", response_class=HTMLResponse)
async def page_login(
    request: Request,
    user: Optional[User] = Depends(get_current_user_optional)
):
    if user:
        return RedirectResponse("/dashboard")
    locale = get_locale_for_request(request, user)
    return render_template(request, "login.html", {
        "current_user": user,
        "locale": locale,
        "t": t,
        "is_demo": settings.DEMO_MODE
    })

@app.get("/register", response_class=HTMLResponse)
async def page_register(
    request: Request,
    user: Optional[User] = Depends(get_current_user_optional)
):
    if user:
        return RedirectResponse("/dashboard")
    locale = get_locale_for_request(request, user)
    return render_template(request, "register.html", {
        "current_user": user,
        "locale": locale,
        "t": t,
        "is_demo": settings.DEMO_MODE
    })

@app.get("/dashboard", response_class=HTMLResponse)
async def page_dashboard(
    request: Request,
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db)
):
    if not user:
        return RedirectResponse("/login")
    locale = get_locale_for_request(request, user)

    # Fetch user ratings
    r_stmt = select(PlatformRating).where(PlatformRating.user_id == user.id)
    rating = (await db.execute(r_stmt)).scalars().first()

    # Fetch user linked game accounts
    g_stmt = select(GameAccount).where(GameAccount.user_id == user.id)
    game_accounts = (await db.execute(g_stmt)).scalars().all()

    # Fetch tournaments user is participating in
    t_stmt = select(Tournament).join(TournamentParticipant, Tournament.id == TournamentParticipant.tournament_id)\
        .where(TournamentParticipant.user_id == user.id)
    tourneys = (await db.execute(t_stmt)).scalars().all()

    return render_template(request, "dashboard.html", {
        "current_user": user,
        "platform_rating": rating,
        "game_accounts": game_accounts,
        "user_tournaments": tourneys,
        "locale": locale,
        "t": t,
        "is_demo": settings.DEMO_MODE
    })

@app.get("/account/link", response_class=HTMLResponse)
async def page_account_link(
    request: Request,
    user: Optional[User] = Depends(get_current_user_optional)
):
    if not user:
        return RedirectResponse("/login")
    locale = get_locale_for_request(request, user)
    return render_template(request, "account_link.html", {
        "current_user": user,
        "locale": locale,
        "t": t,
        "is_demo": settings.DEMO_MODE
    })

@app.get("/leaderboards", response_class=HTMLResponse)
async def page_leaderboards(
    request: Request,
    city: Optional[str] = "All",
    game: Optional[str] = "lichess",
    q: Optional[str] = None,
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db)
):
    locale = get_locale_for_request(request, user)
    players = await RatingService.get_platform_leaderboard(
        db=db,
        game_key=game,
        mode="rapid",
        city=city,
        limit=50,
        search=q
    )
    return render_template(request, "leaderboards.html", {
        "current_user": user,
        "players": players,
        "selected_city": city,
        "selected_game": game,
        "search_query": q or "",
        "locale": locale,
        "t": t,
        "is_demo": settings.DEMO_MODE
    })

@app.get("/tournaments", response_class=HTMLResponse)
async def page_tournaments(
    request: Request,
    city: Optional[str] = "All",
    game: Optional[str] = "All",
    status_filter: Optional[str] = "All",
    q: Optional[str] = None,
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db)
):
    locale = get_locale_for_request(request, user)
    stmt = select(Tournament).order_by(desc(Tournament.created_at))
    if city and city != "All":
        stmt = stmt.where(Tournament.city == city)
    if game and game != "All":
        stmt = stmt.where(Tournament.game_key == game)
    if status_filter and status_filter != "All":
        stmt = stmt.where(Tournament.status == status_filter)
    if q and q.strip():
        stmt = stmt.where(Tournament.name.ilike(f"%{q.strip()}%"))
    tourneys = (await db.execute(stmt)).scalars().all()
    return render_template(request, "tournaments/list.html", {
        "current_user": user,
        "tournaments": tourneys,
        "selected_city": city or "All",
        "selected_game": game or "All",
        "selected_status": status_filter or "All",
        "search_query": q or "",
        "locale": locale,
        "t": t,
        "is_demo": settings.DEMO_MODE
    })

@app.get("/tournaments/{slug}", response_class=HTMLResponse)
async def page_tournament_detail(
    slug: str,
    request: Request,
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db)
):
    locale = get_locale_for_request(request, user)
    stmt = select(Tournament).where(Tournament.slug == slug)
    tourney = (await db.execute(stmt)).scalar_one_or_none()
    if not tourney:
        raise HTTPException(status_code=404, detail="Tournament not found")

    p_stmt = select(TournamentParticipant).where(TournamentParticipant.tournament_id == tourney.id)
    participants = (await db.execute(p_stmt)).scalars().all()

    is_registered = False
    if user:
        is_registered = any(p.user_id == user.id for p in participants)

    m_stmt = select(Match).where(Match.tournament_id == tourney.id).order_by(Match.round_number, Match.match_number)
    matches = (await db.execute(m_stmt)).scalars().all()

    path_stmt = select(TournamentQualificationPath).where(
        TournamentQualificationPath.source_tournament_id == tourney.id
    )
    qualification_path = (await db.execute(path_stmt)).scalar_one_or_none()
    next_stage = None
    if qualification_path:
        next_stage = (await db.execute(
            select(Tournament).where(
                Tournament.id == qualification_path.destination_tournament_id
            )
        )).scalar_one_or_none()

    qualified_stmt = select(TournamentParticipant, User).join(
        User, User.id == TournamentParticipant.user_id
    ).where(
        TournamentParticipant.tournament_id == tourney.id,
        TournamentParticipant.status.in_(["qualified", "winner", "runner_up", "semifinalist"])
    ).order_by(TournamentParticipant.status.desc(), TournamentParticipant.seed)
    qualified_players = [
        {"participant": participant, "user": qualified_user}
        for participant, qualified_user in (await db.execute(qualified_stmt)).all()
    ]

    incoming_paths = (await db.execute(
        select(TournamentQualificationPath, Tournament)
        .join(Tournament, Tournament.id == TournamentQualificationPath.source_tournament_id)
        .where(TournamentQualificationPath.destination_tournament_id == tourney.id)
        .order_by(Tournament.start_time)
    )).all()
    feeder_tournaments = []
    for path, source_tournament in incoming_paths:
        feeder_qualifiers = (await db.execute(
            select(User)
            .join(TournamentParticipant, TournamentParticipant.user_id == User.id)
            .where(
                TournamentParticipant.tournament_id == tourney.id,
                TournamentParticipant.status == "qualified",
                TournamentParticipant.user_id.in_(
                    select(TournamentParticipant.user_id).where(
                        TournamentParticipant.tournament_id == source_tournament.id
                    )
                )
            )
            .order_by(User.display_name)
        )).scalars().all()
        feeder_tournaments.append({
            "tournament": source_tournament,
            "players": feeder_qualifiers
        })

    available_next_stages_stmt = select(Tournament).where(
        Tournament.id != tourney.id,
        Tournament.status == "registration_open",
        Tournament.game_key == tourney.game_key,
        Tournament.mode == tourney.mode
    ).order_by(Tournament.start_time)
    available_next_stages = (await db.execute(available_next_stages_stmt)).scalars().all()
    can_manage_tournament = bool(
        user and await user_manages_hub(db, user, tourney.hub_id)
    )

    user_ids = set()
    for m in matches:
        if m.player1_id: user_ids.add(m.player1_id)
        if m.player2_id: user_ids.add(m.player2_id)
    
    users_stmt = select(User).where(User.id.in_(user_ids)) if user_ids else select(User).where(False)
    name_map = {u.id: u.display_name for u in (await db.execute(users_stmt)).scalars().all()}

    rounds = {}
    for m in matches:
        rounds.setdefault(m.round_number, []).append({
            "id": m.id,
            "match_number": m.match_number,
            "player1_id": m.player1_id,
            "player2_id": m.player2_id,
            "winner_id": m.winner_id,
            "status": m.status,
            "is_bye": (m.player1_id is None or m.player2_id is None),
            "p1_name": name_map.get(m.player1_id),
            "p2_name": name_map.get(m.player2_id)
        })

    return render_template(request, "tournaments/detail.html", {
        "current_user": user,
        "tournament": tourney,
        "participants": participants,
        "is_registered": is_registered,
        "qualification_path": qualification_path,
        "next_stage": next_stage,
        "qualified_players": qualified_players,
        "feeder_tournaments": feeder_tournaments,
        "available_next_stages": available_next_stages,
        "can_manage_tournament": can_manage_tournament,
        "rounds": rounds,
        "locale": locale,
        "t": t,
        "is_demo": settings.DEMO_MODE
    })

@app.get("/matches/{match_id}", response_class=HTMLResponse)
async def page_match_room(
    match_id: str,
    request: Request,
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db)
):
    locale = get_locale_for_request(request, user)
    stmt = select(Match).where(Match.id == match_id)
    match = (await db.execute(stmt)).scalar_one_or_none()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    t_stmt = select(Tournament).where(Tournament.id == match.tournament_id)
    tourney = (await db.execute(t_stmt)).scalar_one_or_none()

    p1 = (await db.execute(select(User).where(User.id == match.player1_id))).scalar_one_or_none() if match.player1_id else None
    p2 = (await db.execute(select(User).where(User.id == match.player2_id))).scalar_one_or_none() if match.player2_id else None

    ev_stmt = select(MatchResultEvent).where(MatchResultEvent.match_id == match.id).order_by(MatchResultEvent.created_at)
    events = (await db.execute(ev_stmt)).scalars().all()

    return render_template(request, "tournaments/match.html", {
        "current_user": user,
        "match": match,
        "tournament": tourney,
        "p1": p1,
        "p2": p2,
        "events": events,
        "locale": locale,
        "t": t,
        "is_demo": settings.DEMO_MODE
    })

@app.get("/p/{public_identifier}", response_class=HTMLResponse)
async def page_passport(
    public_identifier: str,
    request: Request,
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db)
):
    locale = get_locale_for_request(request, user)
    stmt = select(User).where(User.public_identifier == public_identifier)
    player = (await db.execute(stmt)).scalar_one_or_none()
    if not player or not player.is_active:
        raise HTTPException(status_code=404, detail="Player Gamer Passport not found")

    r_stmt = select(PlatformRating).where(PlatformRating.user_id == player.id)
    rating = (await db.execute(r_stmt)).scalars().first()

    g_stmt = select(GameAccount).where(GameAccount.user_id == player.id, GameAccount.status == "verified")
    accounts = (await db.execute(g_stmt)).scalars().all()

    b_stmt = select(Badge).join(UserBadge, Badge.id == UserBadge.badge_id).where(UserBadge.user_id == player.id)
    badges = (await db.execute(b_stmt)).scalars().all()

    completed_player_match = (
        Match.status == "completed",
        or_(Match.player1_id == player.id, Match.player2_id == player.id)
    )
    w_stmt = select(func.count(Match.id)).where(
        Match.winner_id == player.id,
        *completed_player_match
    )
    win_count = (await db.execute(w_stmt)).scalar_one()
    l_stmt = select(func.count(Match.id)).where(
        Match.winner_id.is_not(None),
        Match.winner_id != player.id,
        *completed_player_match
    )
    loss_count = (await db.execute(l_stmt)).scalar_one()
    total_matches = (await db.execute(
        select(func.count(Match.id)).where(*completed_player_match)
    )).scalar_one()
    draw_count = total_matches - win_count - loss_count
    win_rate = round((win_count / total_matches * 100), 1) if total_matches > 0 else 0.0

    recent_match_rows = (await db.execute(
        select(Match, Tournament.name)
        .join(Tournament, Tournament.id == Match.tournament_id)
        .where(*completed_player_match)
        .order_by(desc(Match.updated_at), desc(Match.created_at))
        .limit(8)
    )).all()
    opponent_ids = {
        match.player2_id if match.player1_id == player.id else match.player1_id
        for match, _ in recent_match_rows
    }
    opponent_ids.discard(None)
    opponents = {}
    if opponent_ids:
        opponents = {
            opponent.id: opponent.display_name
            for opponent in (await db.execute(
                select(User).where(User.id.in_(opponent_ids))
            )).scalars().all()
        }

    recent_match_ids = [match.id for match, _ in recent_match_rows]
    latest_event_by_match = {}
    if recent_match_ids:
        result_events = (await db.execute(
            select(MatchResultEvent)
            .where(MatchResultEvent.match_id.in_(recent_match_ids))
            .order_by(MatchResultEvent.created_at)
        )).scalars().all()
        for event in result_events:
            latest_event_by_match[event.match_id] = event

    recent_matches = []
    for match, tournament_name in recent_match_rows:
        opponent_id = match.player2_id if match.player1_id == player.id else match.player1_id
        event = latest_event_by_match.get(match.id)
        reason = (event.reason or "").lower() if event else ""
        if "automated verification via lichess" in reason:
            verification_label = "Lichess verified"
        elif "both players submitted agreeing reports" in reason:
            verification_label = "Player confirmed"
        elif "captain decision" in reason:
            verification_label = "Captain reviewed"
        else:
            verification_label = "Result recorded"

        recent_matches.append({
            "id": match.id,
            "tournament_name": tournament_name,
            "opponent_name": opponents.get(opponent_id, "Opponent unavailable"),
            "outcome": "WIN" if match.winner_id == player.id else (
                "DRAW" if match.winner_id is None else "LOSS"
            ),
            "verification_label": verification_label,
            "played_at": match.updated_at
        })

    history_stmt = select(Tournament).join(
        TournamentParticipant, Tournament.id == TournamentParticipant.tournament_id
    ).where(TournamentParticipant.user_id == player.id).order_by(desc(Tournament.start_time)).limit(5)
    tournament_history = (await db.execute(history_stmt)).scalars().all()

    return render_template(request, "passport.html", {
        "current_user": user,
        "player": player,
        "platform_rating": rating,
        "game_accounts": accounts,
        "badges": badges,
        "win_count": win_count,
        "loss_count": loss_count,
        "draw_count": draw_count,
        "win_rate": win_rate,
        "total_matches": total_matches,
        "recent_matches": recent_matches,
        "tournament_history": tournament_history,
        "locale": locale,
        "t": t,
        "settings": settings,
        "is_demo": settings.DEMO_MODE
    })

@app.get("/p/{public_identifier}/card.png")
async def get_share_card_png(public_identifier: str, db: AsyncSession = Depends(get_db)):
    stmt = select(User).where(User.public_identifier == public_identifier)
    player = (await db.execute(stmt)).scalar_one_or_none()
    if not player or not player.is_active:
        raise HTTPException(status_code=404, detail="Player not found")

    r_stmt = select(PlatformRating).where(PlatformRating.user_id == player.id)
    rating = (await db.execute(r_stmt)).scalars().first()
    elo = rating.elo if rating else 1200.0
    tier = rating.tier if rating else "Bronze"
    completed_player_match = (
        Match.status == "completed",
        or_(Match.player1_id == player.id, Match.player2_id == player.id)
    )
    matches = (await db.execute(
        select(func.count(Match.id)).where(*completed_player_match)
    )).scalar_one()
    win_count = (await db.execute(select(func.count(Match.id)).where(
        Match.winner_id == player.id,
        *completed_player_match
    ))).scalar_one()
    draw_count = (await db.execute(select(func.count(Match.id)).where(
        Match.winner_id.is_(None),
        *completed_player_match
    ))).scalar_one()

    g_stmt = select(GameAccount).where(GameAccount.user_id == player.id, GameAccount.status == "verified")
    accs = [f"{a.game_key}:{a.external_username}" for a in (await db.execute(g_stmt)).scalars().all()]

    png_bytes = generate_player_share_card(
        display_name=player.display_name,
        public_id=player.public_identifier,
        city=player.city,
        tier=tier,
        elo=elo,
        matches_played=matches,
        wins=win_count,
        verified_accounts=accs,
        draws=draw_count
    )
    return Response(content=png_bytes, media_type="image/png")

@app.get("/captain", response_class=HTMLResponse)
async def page_captain(
    request: Request,
    user: User = Depends(require_captain),
    db: AsyncSession = Depends(get_db)
):
    locale = get_locale_for_request(request, user)
    d_stmt = select(Dispute).where(Dispute.status == "open").order_by(desc(Dispute.created_at))
    t_stmt = select(Tournament).order_by(desc(Tournament.created_at))
    hub_ids = await get_managed_hub_ids(db, user)
    if hub_ids is not None:
        if not hub_ids:
            raise HTTPException(status_code=403, detail="No hub captain membership is assigned to this account.")
        d_stmt = d_stmt.join(Match, Dispute.match_id == Match.id).join(
            Tournament, Match.tournament_id == Tournament.id
        ).where(Tournament.hub_id.in_(hub_ids))
        t_stmt = t_stmt.where(Tournament.hub_id.in_(hub_ids))

    disputes = (await db.execute(d_stmt)).scalars().all()
    tourneys = (await db.execute(t_stmt)).scalars().all()
    evidence_by_match = {}
    if disputes:
        evidence_stmt = select(MatchEvidence).where(
            MatchEvidence.match_id.in_([dispute.match_id for dispute in disputes])
        ).order_by(MatchEvidence.uploaded_at)
        for evidence in (await db.execute(evidence_stmt)).scalars().all():
            evidence_by_match.setdefault(evidence.match_id, []).append(evidence)

    return render_template(request, "captain/dashboard.html", {
        "current_user": user,
        "disputes": disputes,
        "evidence_by_match": evidence_by_match,
        "tournaments": tourneys,
        "locale": locale,
        "t": t,
        "is_demo": settings.DEMO_MODE
    })

@app.get("/judge", response_class=HTMLResponse)
async def page_judge(
    request: Request,
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db)
):
    locale = get_locale_for_request(request, user)
    match_stmt = select(Match.id).join(
        Tournament, Match.tournament_id == Tournament.id
    ).where(
        Tournament.slug == "bole-grassroots-cup-1",
        Match.status == "completed"
    ).order_by(Match.round_number, Match.match_number).limit(1)
    demo_match_id = (await db.execute(match_stmt)).scalars().first()
    return render_template(request, "judge.html", {
        "current_user": user,
        "demo_match_id": demo_match_id,
        "locale": locale,
        "t": t,
        "is_demo": settings.DEMO_MODE
    })

@app.get("/analytics", response_class=HTMLResponse)
async def page_analytics(
    request: Request,
    user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db)
):
    locale = get_locale_for_request(request, user)
    metrics = await AnalyticsService.get_dashboard_metrics(db)
    return render_template(request, "analytics.html", {
        "current_user": user,
        "metrics": metrics,
        "locale": locale,
        "t": t,
        "is_demo": settings.DEMO_MODE
    })

@app.get("/terms", response_class=HTMLResponse)
async def page_terms(request: Request, user: Optional[User] = Depends(get_current_user_optional)):
    locale = get_locale_for_request(request, user)
    return render_template(request, "terms.html", {
        "current_user": user, "locale": locale, "t": t, "is_demo": settings.DEMO_MODE
    })

@app.get("/privacy", response_class=HTMLResponse)
async def page_privacy(request: Request, user: Optional[User] = Depends(get_current_user_optional)):
    locale = get_locale_for_request(request, user)
    return render_template(request, "privacy.html", {
        "current_user": user, "locale": locale, "t": t, "is_demo": settings.DEMO_MODE
    })

@app.get("/offline", response_class=HTMLResponse)
async def page_offline(request: Request):
    return render_template(request, "offline.html", {
        "current_user": None, "locale": "en", "t": t, "is_demo": settings.DEMO_MODE
    })
