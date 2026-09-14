"""Generic dry-run candidate log patterns shared by all games.

These never trigger supervisor actions. They only highlight likely lines in the
status UI so operators can promote precise regexes into each game's plugin
`log_patterns` (active) section.

Keep these cross-game and mundane — game-specific phrasing belongs in that
game's `games/game.yaml` under `log_pattern_candidates`, not here.

Bias toward over-matching: many short, case-insensitive guesses beat one
clever regex. Active plugin patterns should be much tighter after promotion.

Shapes below were calibrated on several shipped dedicated servers (port bind,
Steam/userid handshake vs in-world spawn, Unity NetCode, tagged JOIN/LEAVE,
headcount, protocol mismatch) without baking those titles into the regexes.
"""

from __future__ import annotations

# Broad-on-purpose. Matched case-insensitively after ANSI stripping.
# Prefer several vague alternatives over a single precise pattern.
DEFAULT_CANDIDATE_PATTERNS: dict[str, list[str]] = {
    "ready": [
        # Port bind / accepting connections — usually when clients can join.
        r"\blistening on\b",
        r"\blistening\b",
        r"\bopening socket\b",
        r"\bsocket bound\b",
        r"\bbound to\b",
        r"\bbinding to\b",
        r"\baccepting connections\b",
        r"\bready for connections\b",
        r"\bserver using port\b",
        r"\busing port\b",
        r"\bhosting game at\b",
        r"\bhosting game\b",
        r"\bhosting\b",
        # World / session loaded (can be later than bind — still useful samples).
        r"\bserver started\b",
        r"\bstarted server\b",
        r"\bserver is ready\b",
        r"\bstarted session\b",
        r"\bsession started\b",
        r"\bregistered with session\b",
        r"\bsuccessfully loaded\b",
        r"\bloaded world\b",
        r"\bworld loaded\b",
        r"\bmap loaded\b",
        r"\bgame loaded\b",
        r"\bchanging state\b",
        r"\bingame\b",
        r"\bin\s*game\b",
        # Last-resort broad.
        r"\bonline\b",
        r"\bready\b",
        r"\bstarted\b",
    ],
    "player_join": [
        r"\[join\]",
        r"\bjoined the (game|server|world)\b",
        r"\bhas joined\b",
        r"\bplayer .+ joined\b",
        r"\bjoined\b",
        r"\bconnected on slot\b",
        r"\bplayer \S+ connected\b",
        r"\[userid:",
        r"\bconnected to userid\b",
        r"\baccepted connection\b",
        r"\baccepted connect\b",
        r"\bsuccessful authentication\b",
        r"\bauthentication from\b",
        r"\bclient:.+\bconnected\b",
        r"\bclient \S+ is ready\b",
        r"\bstarted game for\b",
        r"\bnew connection\b",
        r"\bconnecting\b",
        r"\bconnected\b",
        r"\blogin\b",
        r"\bentering\b",
        r"\bspawn(ed)?\b",
    ],
    "player_leave": [
        r"\[leave\]",
        r"\bleft the (game|server|world)\b",
        r"\bhas left\b",
        r"\bplayer .+ left\b",
        r"\bdisconnected from\b",
        r"\bclient disconnected\b",
        r"\bplayer disconnect\b",
        r"\bdisconnected\b",
        r"\bdisconnecting\b",
        r"\bleft\b",
        r"\blogout\b",
        r"\bquit\b",
        r"\bkicked\b",
    ],
    "player_count": [
        r"\bplayers?\s+online\b",
        r"\bonline\s+players?\s*[:=]\s*\d+",
        r"\bthere are \d+ players?\b",
        r"\b\d+\s+players?\s+online\b",
        r"players?\s*[:=]\s*\d+",
        r"\bplayer count\b",
        r"\bnum(ber)? of players?\b",
        r"\bclients?\s*[:=]\s*\d+",
        r"\bplayers?\s+\d+\s*/\s*\d+\b",
    ],
    "players_empty": [
        r"\bno (clients|players) connected\b",
        r"\bserver (is )?empty\b",
        r"\b0 players?\s+online\b",
        r"\bnobody (online|connected)\b",
        r"\ball players? (left|disconnected)\b",
        r"\blast player\b",
    ],
    # Announce lines only — mismatch/outdated belong in version_mismatch.
    "game_version": [
        r"\bgame version:\s*(?P<version>\S+)",
        r"\bgame version\s+(?P<version>\d+(?:\.\d+)+)\b",
        r"\bserver version\s*(?:[:=]\s*)?(?P<version>\d+(?:\.\d+)+)\b",
        r"\bon version\s+(?P<version>\d+(?:\.\d+)+)\b",
        r"\bVersion\s*:\s*(?P<version>\d+(?:\.\d+)+)\b",
        r"\bversion\s*(?:[:=]\s*)(?P<version>\d+(?:\.\d+)+)\b",
        r"\bbuild\s+(?P<version>\d+)\b",
        r"\b(?P<version>\d+\.\d+\.\d+(?:\.\d+)?)\b",
    ],
    "version_mismatch": [
        r"\bwrong version\b",
        r"\bversion mismatch\b",
        r"\bbad protocol version\b",
        r"\bprotocol version\b",
        r"\bclient version\b",
        r"\bincompatible version\b",
        r"\bincompatible (client|protocol|save)\b",
        r"\boutdated (client|server|version)\b",
        r"\bnewer version\b",
        r"\bversion (too )?(old|new)\b",
        r"\brejected.+(version|protocol)\b",
        r"\bmod mismatch\b",
        r"\bmap version\b",
        r"\bdesync\b",
    ],
}
