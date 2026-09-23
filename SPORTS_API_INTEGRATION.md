# Facelook Challenge — Professional Multi-Sport Live-Games API Integration

This document describes the backend architecture, database schema, synchronization engine, settlement system, and integration patterns for connecting Facelook Challenge with external professional sports-data providers.

---

## 1. Architecture Overview

The backend integration follows a decoupled, provider-independent adapter architecture:

```
┌───────────────────────────────────────────────────────────────────┐
│                    External Sports APIs                          │
│     (API-Football, Sportmonks, Sportradar, TheSportsDB, etc.)     │
└───────────────────────────────┬───────────────────────────────────┘
                                │ JSON / HTTPS REST
                                ▼
┌───────────────────────────────────────────────────────────────────┐
│                   Sports Provider Adapter Layer                  │
│       SportsProvider (Interface) + ProviderAdapter (Base)         │
│  - Rate limiting & backoff    - Error mapping to SportsApiException│
│  - GenericRestSportsProvider   - MockSportsProvider (Fixtures)     │
└───────────────────────────────┬───────────────────────────────────┘
                                │ Normalized Domain Models
                                ▼
┌───────────────────────────────────────────────────────────────────┐
│                 Synchronization & Settlement Services             │
│   GameSyncService (12-point sync) | ResultSettlementService       │
│  - Deduplication engine         - Official result verification    │
│  - Live score & status refresh  - Idempotent escrow payouts       │
└───────────────────────────────┬───────────────────────────────────┘
                                │ Room CRUD & Flow Streams
                                ▼
┌───────────────────────────────────────────────────────────────────┐
│                     Room Database (SQLite)                        │
│   sports, leagues, seasons, teams, players, venues, games,        │
│   game_events, game_statistics, game_results, sports_providers,  │
│   provider_mappings, settlement_audit_logs                        │
└───────────────────────────────┬───────────────────────────────────┘
                                │ Bridge Methods & StateFlows
                                ▼
┌───────────────────────────────────────────────────────────────────┐
│              Facelook Challenge & Escrow Systems                  │
│  - FacelookRepository & FacelookViewModel                         │
│  - ActiveGame Bridge (CreateChallengeDialog, ChallengesScreen)    │
│  - Escrow wallet deduction & payout ledger                        │
└───────────────────────────────────────────────────────────────────┘
```

### Key Architectural Tenets:
* **Zero Disruption to Existing Code**: Existing challenge models (`ChallengeEntity`, `FaceBattleEntity`), escrow logic, and UI dialogs remain preserved.
* **Provider Independence**: Swapping external providers does not modify internal game representations or user challenges.
* **Separation of IDs**: Internal primary keys (`internalGameId`) are completely decoupled from external provider IDs (`providerName`, `providerGameId`).
* **Strict Idempotency**: Results cannot settle multiple times; duplicate payouts are mathematically and logically impossible.

---

## 2. Database Models & Schema

The sports database schema is persisted via Room in `AppDatabase` (Version 9):

### `sports` Table
* `id` (Long, PK, auto-generated)
* `code` (String, unique indexed): e.g. `"football"`, `"basketball"`, `"tennis"`, `"cricket"`
* `name` (String): Display name
* `emoji` (String): Sport symbol
* `isActive` (Boolean)
* `supportedMarkets` (String): e.g. `"1X2,OverUnder,BothTeamsToScore,Spread"`

### `leagues` Table
* `id` (Long, PK)
* `sportCode` (String, indexed)
* `name` (String)
* `country` (String)
* `isMajor` (Boolean)
* `providerName` (String)
* `providerLeagueId` (String) — Compound unique index on `(providerName, providerLeagueId)`

### `teams` Table
* `id` (Long, PK)
* `leagueId` (Long, indexed)
* `sportCode` (String)
* `name`, `shortName`, `code` (`"MUN"`, `"LAL"`, `"ALC"`)
* `logoUrl`, `country`, `venueName`
* `providerName`, `providerTeamId` — Compound unique index on `(providerName, providerTeamId)`

### `games` Table
* `internalGameId` (Long, PK)
* `sportCode`, `leagueId`, `leagueName`, `season`, `round`
* `homeTeamId`, `awayTeamId`, `homeTeamName`, `awayTeamName`, `homeTeamCode`, `awayTeamCode`
* `scheduledStartTime` (Long, timestamp)
* `venue` (String)
* `status` (String, indexed): `SCHEDULED`, `IN_PLAY`, `HALF_TIME`, `POSTPONED`, `CANCELLED`, `FINISHED`
* `liveState` (Boolean): `true` when currently playing
* `currentPeriod` (String): e.g. `"74'"`, `"Q3 04:12"`, `"Set 3 (4-3)"`, `"16.4 ov"`
* `periodInfo` (String): Formatted badge for display
* `homeScore`, `awayScore` (Int, nullable for upcoming games)
* **Sport-Specific Breakdowns**:
  * Football: `halfTimeHomeScore`, `halfTimeAwayScore`
  * Basketball: `homeQuarterScores`, `awayQuarterScores` (`"28,26,24"`)
  * Tennis: `tennisSetScores` (`"6-4, 3-6, 7-6"`), `tennisCurrentSet`, `tennisCurrentGameScore`
  * Cricket: `cricketHomeInnings`, `cricketAwayInnings`
* `odds1`, `oddsX`, `odds2` (String): Default decimal odds
* `providerName`, `providerGameId` — Compound unique index for strict deduplication
* `resultStatus` (String): `NOT_AVAILABLE`, `PROVISIONAL`, `OFFICIAL_FINAL`, `CANCELLED`
* `settlementStatus` (String): `UNSETTLED`, `PENDING`, `SETTLED`, `VOIDED`
* `winnerSide` (String, nullable): `"HOME"`, `"AWAY"`, `"DRAW"`, `"CANCELLED"`

### `game_events` Table
* `id`, `gameId`, `minute`, `period`, `type` (`GOAL`, `CARD`, `SUBSTITUTION`, `WICKET`), `teamSide`, `playerName`, `detail`

### `game_statistics` Table
* `id`, `gameId`, `statName` (`Possession`, `Shots`, `Rebounds`, `RunRate`), `homeValue`, `awayValue`

### `game_results` Table
* `id`, `gameId` (unique), `sportCode`, `homeScore`, `awayScore`, `winnerOutcome`, `isOfficial` (Boolean), `verifiedAt` (Long), `verifiedBy` (String)

### `provider_mappings` Table
* `id`, `entityType` (`GAME`, `LEAGUE`, `TEAM`), `internalId`, `providerName`, `providerId`, `metadata`

### `settlement_audit_logs` Table
* `settlementId` (String, PK): e.g. `"SETTLE-G104-P501-1774200000"`
* `gameId`, `challengeId`, `postId`, `matchTitle`, `officialResult`, `winnerSide`, `challengerStake`, `opponentStake`, `totalPayout`, `recipientUserId`, `settlementStatus`, `timestamp`, `notes`

---

## 3. Provider Adapter Pattern

All external API communication occurs through the `SportsProvider` interface (`com.example.data.sports.provider.SportsProvider`):

```kotlin
interface SportsProvider {
    val providerName: String
    val displayName: String

    suspend fun getSports(): Result<List<SportEntity>>
    suspend fun getLeagues(sportCode: String? = null): Result<List<LeagueEntity>>
    suspend fun getTeams(providerLeagueId: String? = null): Result<List<TeamEntity>>
    suspend fun getPlayers(providerTeamId: String? = null): Result<List<PlayerEntity>>
    suspend fun getUpcomingGames(sportCode: String? = null, providerLeagueId: String? = null, date: String? = null): Result<List<GameEntity>>
    suspend fun getLiveGames(sportCode: String? = null): Result<List<GameEntity>>
    suspend fun getGameById(providerGameId: String): Result<GameEntity?>
    suspend fun getGameEvents(providerGameId: String): Result<List<GameEventEntity>>
    suspend fun getGameStatistics(providerGameId: String): Result<List<GameStatisticEntity>>
    suspend fun getGameResult(providerGameId: String): Result<GameResultEntity?>
    suspend fun getStandings(providerLeagueId: String, season: String? = null): Result<List<Standing>>
}
```

* `ProviderAdapter`: Enforces exponential backoff, request rate pacing (4 requests/sec max default throttle), and converts HTTP errors (`429`, `401`, `503`, timeouts) into domain-safe `SportsApiException` instances.
* `GenericRestSportsProvider`: Connects to external REST endpoints with HTTP header authentication (`x-apisports-key`, `Authorization: Bearer`). If credentials are empty, it gracefully delegates to `MockSportsProvider`.
* `MockSportsProvider`: Full in-memory multi-sport simulation engine containing Premier League, Champions League, La Liga, Serie A, Bundesliga, Ligue 1, NBA, WNBA, EuroLeague, ATP/WTA Tennis, and ICC Cricket fixtures.

---

## 4. Environment Variables & Security

Configure provider keys via the AI Studio Secrets panel or `.env`:

```properties
# ACTIVE PROVIDER IDENTIFIER
SPORTS_API_PROVIDER="generic_rest"

# BASE URL OF EXTERNAL DATA FEED
SPORTS_API_BASE_URL="https://api.sportsdata.io/v4"

# EXTERNAL PROVIDER API KEY
# Injected at runtime via BuildConfig / AI Studio Secrets panel
SPORTS_API_KEY=""

# NETWORK TIMEOUT (milliseconds)
SPORTS_API_TIMEOUT="15000"

# SYNC CADENCE SETTINGS
UPCOMING_SYNC_INTERVAL_MINUTES="60"
LIVE_SYNC_INTERVAL_SECONDS="30"
RESULT_SYNC_INTERVAL_MINUTES="15"
```

### Security Directives:
* Never expose `SPORTS_API_KEY` in UI composables or client logs.
* In production, the Android client should preferably talk to a secure backend reverse-proxy that signs and caches sports data requests.

---

## 5. Synchronization Process (`GameSyncService`)

The sync engine implements 12 explicit responsibilities:

1. **Metadata Ingestion**: Syncs sports, active leagues, and team rosters into Room.
2. **Upcoming Games Import**: Queries scheduled games for target leagues.
3. **Deduplication**: Queries by `providerName` + `providerGameId`. If an existing match is found, it performs an in-place update (odds, timing, venue) without generating duplicate primary keys.
4. **Live Games Ingestion**: High-frequency sync checking active matches.
5. **Live Score Updates**: Updates `homeScore`, `awayScore`, and sport-specific quarter/set/innings data.
6. **Live Period & Clock**: Updates `currentPeriod` (`74'`, `Q3 04:22`, `Set 3`) and `periodInfo`.
7. **Postponed Match Detection**: Detects `POSTPONED` status transitions and flags the match.
8. **Cancelled / Abandoned Detection**: Updates status to `CANCELLED` and marks wagers for refund.
9. **Finished Game Transition**: Moves `status` from `IN_PLAY` to `FINISHED`.
10. **Official Result Ingestion**: Fetches `GameResultEntity` with `isOfficial = true` and `verifiedAt`.
11. **Rate Limit Handling**: Exponential backoff prevents 429 quota exhaustion.
12. **Sync Logging**: All operational events, errors, and throughput metrics are recorded in `SyncLogger`.

---

## 6. Game Lifecycle

```
[ SCHEDULED ]
      │  (Start time reached / kickoff)
      ▼
[  IN_PLAY  ] ◄─── (Live score, clock, and event updates)
      │
      ├───────────────────────┬────────────────────────┐
      ▼                       ▼                        ▼
[ FINISHED ]            [ POSTPONED ]            [ CANCELLED ]
      │                       │                        │
      ▼                       ▼                        ▼
Official Result        Rescheduled / Held       Escrow Stake Refunded
Verified & Ingested
      │
      ▼
Escrow Settlement
```

---

## 7. Result Lifecycle & Verification

1. Match concludes (`status = "FINISHED"`).
2. `GameSyncService.syncFinishedGameResults()` fetches result from external provider.
3. Result is verified:
   * Must have `isOfficial = true`
   * Must have valid non-null `verifiedAt` timestamp
   * Must indicate terminal outcome (`winnerOutcome` in `HOME`, `AWAY`, `DRAW`, `CANCELLED`).
4. Result is written to `game_results` and game is flagged `settlementStatus = "PENDING"`.

---

## 8. Challenge Integration (Bridge to Existing Architecture)

Imported games seamlessly integrate into Facelook's existing challenge hub via `Game.toActiveGame()`:

```kotlin
fun Game.toActiveGame(): ActiveGame {
    val cat = when {
        liveState || status == "IN_PLAY" || status == "HALF_TIME" -> "Live"
        status == "FINISHED" -> "Highlights"
        else -> "Upcoming"
    }
    val pInfo = if (periodInfo.isNotBlank()) periodInfo else "$homeScore - $awayScore"
    return ActiveGame(
        title = "$homeTeamName vs $awayTeamName",
        category = cat,
        periodInfo = pInfo,
        homeTeam = homeTeamName,
        awayTeam = awayTeamName,
        hashtag = hashtag.ifEmpty { "#${leagueName.replace(" ", "")}" },
        odds1 = odds1,
        oddsX = oddsX,
        odds2 = odds2
    )
}
```

This guarantees:
* **No UI Redesign Needed**: `CreateChallengeDialog`, `ChallengesScreen`, search filters, and odds calculators work immediately with imported games.
* **Dropdown Selection**: Users can select imported Football, Basketball, Tennis, or Cricket matches when throwing a challenge or matching a wager.

---

## 9. Escrow Integration & Settlement Engine

When a match reaches `OFFICIAL_FINAL` status, `ResultSettlementService.settleGame(gameId)` executes:

1. **Terminal State Verification**: Verifies game status is `FINISHED`, `CANCELLED`, or `ABANDONED`.
2. **Official Result Check**: Fails safely if official verified result is absent.
3. **Game-Level Idempotency**: If `game.settlementStatus == "SETTLED"`, the settlement immediately returns without altering user balances.
4. **Post / Wager Resolution**:
   * Identifies all matched bets referencing the teams or challenge hashtag.
   * If game was **CANCELLED**: Returns full wager stake from `escrowBalance` to `availableBalance`. Records `"REFUND"` transaction.
   * If user **WON**: Deducts stake from `escrowBalance`, credits total pool to `availableBalance`. Records `"BET_WIN"` transaction and dispatches winning notification.
   * If user **LOST**: Deducts stake from `escrowBalance`.
5. **Audit Trail**: Writes an immutable `SettlementAuditLogEntity` with unique `settlementId`.
6. **Finalize**: Updates `game.settlementStatus = "SETTLED"`.

---

## 10. How to Add a New Sports API Provider

To integrate a new provider (e.g., *Sportmonks*):

1. Create a class implementing `SportsProvider`:
   ```kotlin
   class SportmonksProvider(
       private val apiKey: String,
       private val baseUrl: String = "https://api.sportmonks.com/v3"
   ) : SportsProvider {
       override val providerName: String = "sportmonks"
       override val displayName: String = "Sportmonks Football Feed"

       override suspend fun getLiveGames(sportCode: String?): Result<List<GameEntity>> {
           // Call Sportmonks HTTP API and map JSON response into List<GameEntity>
       }
       // Implement other methods...
   }
   ```
2. In `FacelookRepository.kt`, instantiate your provider based on `SPORTS_API_PROVIDER`:
   ```kotlin
   val sportsProvider: SportsProvider = when (BuildConfig.SPORTS_API_PROVIDER) {
       "sportmonks" -> SportmonksProvider(BuildConfig.SPORTS_API_KEY)
       "generic_rest" -> GenericRestSportsProvider(BuildConfig.SPORTS_API_BASE_URL, BuildConfig.SPORTS_API_KEY)
       else -> MockSportsProvider()
   }
   ```

---

## 11. How to Add a New Sport

To add a new sport (e.g., *Formula 1* or *Rugby*):

1. In `com.example.data.sports.models.SportsEnums.kt`, add the enum entry:
   ```kotlin
   enum class SportType(val code: String, val displayName: String, val emoji: String) {
       // ... existing sports
       RUGBY("rugby", "Rugby Union", "🏉"),
       MOTORSPORT("motorsport", "Formula 1", "🏎️")
   }
   ```
2. In `SportScores` (`SportsModels.kt`), add the sport-specific score model:
   ```kotlin
   data class RugbyScores(
       val homeTries: Int,
       val awayTries: Int,
       val homeConversions: Int,
       val awayConversions: Int
   ) : SportScores()
   ```
3. Add the leagues and teams in your provider adapter.
4. The existing `GameSyncService`, database tables, and `ActiveGame` bridge will immediately accommodate the new sport.

---

## 12. How to Test the Integration

Run the automated test suite locally via Gradle:

```bash
# Run unit & Robolectric integration tests:
gradle :app:testDebugUnitTest --tests "com.example.sports.SportsBackendIntegrationTest"
```

The test suite validates:
* Metadata sync and league ingestion across all four sports
* Duplicate prevention (deduplication against provider IDs)
* Live score updates and live clock badge formatting
* Multi-sport scoring breakdown (Football, Basketball, Tennis, Cricket)
* Postponed and cancelled game status transitions
* Official result ingestion and escrow settlement
* Strict settlement idempotency (verifying zero double-payouts)
* Rate limit mitigation and HTTP error handling
