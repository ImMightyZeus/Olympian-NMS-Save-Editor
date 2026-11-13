"""JSON keys used by Olympian.

Current saves use obfuscated keys (e.g. "6f=" = PlayerStateData). Older/plain saves use readable
names. Each entry maps a readable name to its obfuscated key; the values below were verified
against real saves and against the libNOM.map / MBINCompiler mapping (see tests).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

OBF = {
    'BaseStatValues': '@bB',
    'BaseStatID': 'QL1',
    'Value': '>MX',
    'InventorySpecialSlotType': 'QA1',
    'CommonStateData': '<h0',
    'CurrentFreighter': 'bIR',
    'PlayerFreighterName': 'vxi',
    'FreighterInventory': '8ZP',
    'FreighterInventory_TechOnly': '0wS',
    'FreighterUniverseAddress': 'RB7',
    'Chest1Inventory': '3Nc',
    'Chest2Inventory': 'IDc',
    'Chest3Inventory': 'M=:',
    'Chest4Inventory': 'iYp',
    'Chest5Inventory': '<IP',
    'Chest6Inventory': 'qYJ',
    'Chest7Inventory': '@e5',
    'Chest8Inventory': '5uh',
    'Chest9Inventory': '5Tg',
    'Chest10Inventory': 'Bq<',
    'CorvetteStorageInventory': 'wem',
    'StackSizeGroup': 'WA4',
    'InventoryStackSizeGroup': 'rri',
    'DifficultyState': 'LyC',
    'Settings': ':fe',
    'InventoryStackLimits': 'kZ5',
    'InventoryStackLimitsDifficulty': '?SS',
    'Inventory': ';l5',
    'Inventory_TechOnly': 'PMT',
    'Inventory_Cargo': 'gan',
    'Slots': ':No',
    'ValidSlotIndices': 'hl?',
    'Class': 'B@N',
    'InventoryClass': '1o6',
    'Width': '=Tb',
    'Height': 'N9>',
    'Type': 'Vn8',
    'InventoryType': 'elv',
    'Id': 'b2n',
    'Amount': '1o9',
    'MaxAmount': 'F9q',
    'DamageFactor': 'eVk',
    'MissionProgress': 'dwb',
    'Mission': 'p0c',
    'Progress': 'tW6',
    'CurrentMissionID': ';R7',
    'CurrentMissionSeed': 'k?G',
    'PreviousMissionSeed': 'rej',
    'PreviousMissionID': 'Mg<',
    'MissionVersion': 'yq:',
    'AddedAutomatically': '5tH',
    'FullyInstalled': 'b76',
    'Index': '3ZH',
    'X': '>Qh',
    'Y': 'XJ>',
    'SpecialSlots': 'MMm',
    'ShipUsesLegacyColours': '4hl',
    'CorvetteEditAssociatedShipIndex': 'rXy',
    'PersistentPlayerBases': 'F?0',
    'BaseType': 'peI',
    'PersistentBaseTypes': 'DPp',
    'UserData': 'CVX',
    'BaseContext': 'vLc',
    'PlayerStateData': '6f=',
    'SpawnStateData': 'rnc',
    'UniverseAddress': 'yhJ',
    'PreviousUniverseAddress': 'ux@',
    'RealityIndex': 'Iis',
    'GalacticAddress': 'oZw',
    'VoxelX': 'dZj',
    'VoxelY': 'IyE',
    'VoxelZ': 'uXE',
    'SolarSystemIndex': 'vby',
    'PlanetIndex': 'jsv',
    'LastKnownPlayerState': 'jk4',
    'PlayerPositionInSystem': 'mEH',
    'PlayerDeathRespawnPositionInSystem': 'tmM',
    'ShipPositionInSystem': 'tnP',
    'ShipHovering': 'lKr',
    'ShipOwnership': '@Cs',
    'Name': 'NKm',
    'Resource': 'NTx',
    'Filename': '93M',
    'Seed': '@EL',
    'PrimaryShip': 'aBE',
    'SaveName': 'Pk4',
    'SaveSummary': 'n:R',
    # phase 5: settlements, exocraft and multi-tools
    'SettlementStatesV2': 'GQA',
    'SettlementHistory': 'Hdt',
    'UniqueId': '20I',
    'SeedValue': 'qK9',
    'Owner': '3?K',
    'UID': 'K7E',
    'LID': 'f5Q',
    'USN': 'V?:',
    'UsedDiscoveryOwnersV2': 'F=J',
    'Stats': 'gUR',
    'Perks': 'OEf',
    'PendingJudgementType': 'HMQ',
    'SettlementJudgementType': '?SU',
    'PendingCustomJudgementID': 'Xs1',
    'LastJudgementTime': '0Qr',
    'ProductionState': 'abj',
    'ElementId': 'CZw',
    'ProductionAccumulationCap': 'T=R',
    'LastChangeTimestamp': '6X1',
    'Population': 'x3<',
    'Race': 'SS2',
    'AlienRace': '0Hi',
    'NextBuildingUpgradeIndex': 'hiD',
    'NextBuildingUpgradeClass': ':Qn',
    'BuildingClass': 'iqv',
    'PlayerClaimedTime': '<:S',
    'JudgementsSettled': '9=d',
    'LongestDebtStretch': 'b5K',
    'VehicleOwnership': 'P;m',
    'Multitools': 'SuJ',
    'Store': 'OsQ',
    'WeaponInventory': 'Kgt',
    'CurrentWeapon': 'e6e',
}

DATA_DIR = Path(__file__).resolve().parent.parent / 'data'


class Keys:
    """Resolve readable names to the key style used by a given save."""

    def __init__(self, obfuscated: bool):
        self.obfuscated = obfuscated

    def __call__(self, name: str) -> bytes:
        return (OBF[name] if self.obfuscated else name).encode()

    def s(self, name: str) -> str:
        return OBF[name] if self.obfuscated else name

    def path(self, *names) -> list[bytes]:
        return [self(n) for n in names]

    @property
    def psd(self) -> list[bytes]:
        return self.path('BaseContext', 'PlayerStateData')

    @property
    def spawn(self) -> list[bytes]:
        return self.path('BaseContext', 'SpawnStateData')


@lru_cache(maxsize=1)
def mapping() -> dict[str, str]:
    """Obfuscated -> readable mapping from libNOM.map (GPL-3.0, see data/LICENSE-libNOM.map.txt)."""
    p = DATA_DIR / 'mapping_mbincompiler.json'
    data = json.loads(p.read_text(encoding='utf-8'))
    return {e['Key']: e['Value'] for e in data['Mapping']}
