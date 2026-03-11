## Engine Architecture

```mermaid
flowchart TB

%% FRONTEND
subgraph FRONTEND["Front End / CLI"]
    UI[CLI / Menus / Views]
    INPUT[Player Input]
end

UI --> INPUT

%% GAME CORE
subgraph GAME["Game Core (Orchestrator)"]
    GAMECORE[Game]
    MODE[Game Mode State\nTown / Wilderness / Dungeon / Combat]
    STATE[Authoritative Runtime State]
    EID[next_event_eid allocator]
end

INPUT --> GAMECORE
GAMECORE --> MODE
GAMECORE --> STATE
GAMECORE --> EID

%% DOMAIN SYSTEMS
subgraph SYSTEMS["Domain Systems"]
    TOWN[Town System]
    WILD[Wilderness System]
    DUNGEON[Dungeon System]
    COMBAT[Combat System]
    CONTRACTS[Contracts / Quests]
end

GAMECORE --> TOWN
GAMECORE --> WILD
GAMECORE --> DUNGEON
GAMECORE --> COMBAT
GAMECORE --> CONTRACTS

%% RNG
subgraph RNG["Randomness / Dice"]
    DICE[Dice.d()]
    SEED[Seeded RNG]
    ROLLS[Combat / Encounter / Generation Rolls]
end

SYSTEMS --> DICE
DICE --> SEED
SEED --> ROLLS

%% EVENT SPINE
subgraph EVENTS["Canonical Event Spine"]
    HISTORY[event_history]
    EVENT[PlayerEvent]
    ORDER[eid ordering]
end

GAMECORE --> EVENT
EVENT --> HISTORY
EVENT --> ORDER

%% JOURNAL
subgraph JOURNAL["Journal Projection Layer"]
    DISC[Discoveries]
    RUMOR[Rumors]
    CLUE[Clues]
    DIST[District Notes]
end

HISTORY --> DISC
HISTORY --> RUMOR
HISTORY --> CLUE
HISTORY --> DIST

%% REPLAY
subgraph REPLAY["Replay / Deterministic Test Harness"]
    STREAM[Recorded RNG Stream]
    TELEMETRY[AI / Event Telemetry]
    SNAPSHOT[Snapshot Comparison]
end

STREAM --> DICE
GAMECORE --> TELEMETRY
TELEMETRY --> SNAPSHOT

%% PERSISTENCE
subgraph SAVELOAD["Persistence Layer"]
    SAVELOADCORE[save_load.py]
    MIGRATE[Migration + Normalization]
    SAVEFILE[(Save File)]
end

STATE --> SAVELOADCORE
HISTORY --> SAVELOADCORE
EID --> SAVELOADCORE
SAVELOADCORE --> MIGRATE
MIGRATE --> SAVEFILE
