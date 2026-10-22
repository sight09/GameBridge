import pytest
from datetime import datetime, timedelta
from sqlalchemy import select
from app.tournaments.bracket import (
    next_power_of_two,
    generate_standard_seeding_pairs,
    build_single_elimination_bracket
)
from app.services.tournament_service import TournamentService
from app.services.user_service import UserService
from app.services.match_service import MatchService
from app.auth.session import create_session_token
from app.models.match import Match
from app.models.tournament import TournamentParticipant

def test_next_power_of_two():
    assert next_power_of_two(2) == 2
    assert next_power_of_two(3) == 4
    assert next_power_of_two(4) == 4
    assert next_power_of_two(5) == 8
    assert next_power_of_two(8) == 8
    assert next_power_of_two(9) == 16

def test_seeding_pairs():
    # 8 players: standard 1 vs 8, 4 vs 5, 2 vs 7, 3 vs 6
    pairs = generate_standard_seeding_pairs(8)
    assert len(pairs) == 4
    assert (1, 8) in pairs
    assert (2, 7) in pairs

def test_bracket_with_byes_odd_count():
    # 5 players => bracket size 8 (3 Byes)
    players = ["p1", "p2", "p3", "p4", "p5"]
    matches = build_single_elimination_bracket(players)
    
    # 8-bracket has 7 matches total: 4 in R1, 2 in R2, 1 in R3
    assert len(matches) == 7

    # Round 1 matches
    r1 = [m for m in matches if m["round_number"] == 1]
    assert len(r1) == 4

    # Top seed p1 should have a bye (matched against seed 8 which is None)
    bye_matches = [m for m in r1 if m["is_bye"]]
    assert len(bye_matches) == 3
    # In bye matches, the present player is automatically winner
    for bm in bye_matches:
        assert bm["status"] == "completed"
        assert bm["winner_id"] is not None

    # Round 2 matches should have bye winners pre-advanced
    r2 = [m for m in matches if m["round_number"] == 2]
    assert len(r2) == 2

@pytest.mark.asyncio
async def test_tournament_service_registration_and_bracket(db_session):
    u1 = await UserService.register_user(db_session, "player_one", "Player One")
    u2 = await UserService.register_user(db_session, "player_two", "Player Two")
    u3 = await UserService.register_user(db_session, "player_three", "Player Three")

    tourney = await TournamentService.create_tournament(
        db=db_session,
        name="Test Cup",
        game_key="lichess",
        mode="rapid",
        city="Addis Ababa",
        start_time=datetime.utcnow() + timedelta(hours=2),
        registration_deadline=datetime.utcnow() + timedelta(hours=1),
        max_participants=8
    )

    # Register players
    await TournamentService.register_player(db_session, tourney.id, u1.id)
    await TournamentService.register_player(db_session, tourney.id, u2.id)
    await TournamentService.register_player(db_session, tourney.id, u3.id)

    # Prevent duplicate registration
    with pytest.raises(ValueError, match="already registered"):
        await TournamentService.register_player(db_session, tourney.id, u1.id)

    # Start tournament and verify seeded bracket
    matches = await TournamentService.start_and_generate_bracket(db_session, tourney.id)
    assert len(matches) == 3  # 4-bracket (2 in R1, 1 in R2)
    assert tourney.status == "in_progress"
    with pytest.raises(ValueError, match="cannot be started"):
        await TournamentService.start_and_generate_bracket(db_session, tourney.id)

@pytest.mark.asyncio
async def test_join_tournament_api(client, db_session):
    registration = await client.post("/api/auth/register", json={
        "username": "join_api_player",
        "display_name": "Join API Player",
        "city": "Addis Ababa",
        "language": "en",
    })
    assert registration.status_code == 200

    tournament = await TournamentService.create_tournament(
        db=db_session,
        name="Join API Cup",
        game_key="lichess",
        mode="rapid",
        city="Addis Ababa",
        start_time=datetime.utcnow() + timedelta(hours=2),
        registration_deadline=datetime.utcnow() + timedelta(hours=1),
    )

    response = await client.post(f"/api/tournaments/{tournament.id}/join")
    assert response.status_code == 200
    assert response.json()["success"] is True

@pytest.mark.asyncio
async def test_signed_in_tournament_detail_shows_join_button(client, db_session):
    player = await UserService.register_user(
        db_session, "tournament_detail_player", "Tournament Detail Player"
    )
    tournament = await TournamentService.create_tournament(
        db=db_session,
        name="Tournament Detail Cup",
        game_key="lichess",
        mode="rapid",
        city="Addis Ababa",
        start_time=datetime.utcnow() + timedelta(hours=2),
        registration_deadline=datetime.utcnow() + timedelta(hours=1),
    )
    client.cookies.set("gb_session", create_session_token(player.id, player.role))

    response = await client.get(f"/tournaments/{tournament.slug}")

    assert response.status_code == 200
    assert "Join Tournament" in response.text

@pytest.mark.asyncio
async def test_top_four_qualification_advances_and_reserves_places(db_session):
    players = [
        await UserService.register_user(db_session, f"qualifier_{index}", f"Qualifier {index}")
        for index in range(1, 6)
    ]
    direct_players = [
        await UserService.register_user(
            db_session, f"direct_entry_{index}", f"Direct Entry {index}"
        )
        for index in range(1, 5)
    ]
    source = await TournamentService.create_tournament(
        db=db_session,
        name="Local Qualifier",
        game_key="lichess",
        mode="rapid",
        city="Addis Ababa",
        start_time=datetime.utcnow() + timedelta(days=1),
        registration_deadline=datetime.utcnow() + timedelta(hours=12),
        max_participants=8,
    )
    next_stage = await TournamentService.create_tournament(
        db=db_session,
        name="City Finals",
        game_key="lichess",
        mode="rapid",
        city="Addis Ababa",
        start_time=datetime.utcnow() + timedelta(days=7),
        registration_deadline=datetime.utcnow() + timedelta(days=6),
        max_participants=8,
    )
    source_participants = [
        TournamentParticipant(
            tournament_id=source.id,
            user_id=player.id,
            seed=seed,
            status="active",
        )
        for seed, player in enumerate(players, start=1)
    ]
    db_session.add_all(source_participants)
    await db_session.flush()

    for direct_player in direct_players:
        await TournamentService.register_player(
            db_session, next_stage.id, direct_player.id
        )
    await TournamentService.link_qualification_path(
        db_session, source.id, next_stage.id
    )
    with pytest.raises(ValueError, match="maximum participant capacity"):
        await TournamentService.register_player(db_session, next_stage.id, players[0].id)

    final_match = Match(
        tournament_id=source.id, round_number=3, match_number=1,
        player1_id=players[0].id, player2_id=players[1].id,
        winner_id=players[0].id, status="completed",
    )
    db_session.add_all([
        Match(
            tournament_id=source.id, round_number=1, match_number=1,
            player1_id=players[3].id, player2_id=players[4].id,
            winner_id=players[3].id, status="completed",
        ),
        Match(
            tournament_id=source.id, round_number=1, match_number=2,
            player1_id=players[0].id, winner_id=players[0].id, status="completed",
        ),
        Match(
            tournament_id=source.id, round_number=1, match_number=3,
            player1_id=players[1].id, winner_id=players[1].id, status="completed",
        ),
        Match(
            tournament_id=source.id, round_number=1, match_number=4,
            player1_id=players[2].id, winner_id=players[2].id, status="completed",
        ),
        Match(
            tournament_id=source.id, round_number=2, match_number=1,
            player1_id=players[0].id, player2_id=players[3].id,
            winner_id=players[0].id, status="completed",
        ),
        Match(
            tournament_id=source.id, round_number=2, match_number=2,
            player1_id=players[1].id, player2_id=players[2].id,
            winner_id=players[1].id, status="completed",
        ),
        final_match,
    ])
    await db_session.flush()

    await MatchService._advance_bracket_winner(
        db_session, final_match, players[0].id
    )

    qualified = (await db_session.execute(
        select(TournamentParticipant).where(
            TournamentParticipant.tournament_id == next_stage.id,
            TournamentParticipant.status == "qualified"
        )
    )).scalars().all()
    assert {participant.user_id for participant in qualified} == {
        player.id for player in players[:4]
    }
    statuses = {participant.user_id: participant.status for participant in source_participants}
    assert source.status == "completed"
    assert statuses[players[0].id] == "winner"
    assert all(statuses[player.id] == "qualified" for player in players[1:4])
    assert statuses[players[4].id] == "active"
    assert len(await TournamentService.start_and_generate_bracket(db_session, next_stage.id)) == 7
