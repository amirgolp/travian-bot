"""Reconciler controllers — one per concern.

Each controller is a subclass of `app.core.reconciler.Controller`. They are
assembled by `app.core.account_manager.AccountWorker` into a ControllerLoop.

Controllers:
  FarmingController       — runs due farmlists, jittered
  MaintenanceController   — trims losing slots, updates tile aggregates
  BuildingController      — reconciles BuildOrder queue vs live build state
  ReportsController       — ingests reports, parses bounty, attaches to tiles
  WorldSqlController      — nightly map.sql diff → MapTile + villages list
  MapScanController       — 24h-ish oasis/natar scrape → MapTile + oases list
"""
from app.services.controllers.building_ctrl import BuildingController
from app.services.controllers.farming_ctrl import FarmingController
from app.services.controllers.hero_ctrl import HeroController
from app.services.controllers.maintenance_ctrl import MaintenanceController
from app.services.controllers.map_scan_ctrl import MapScanController
from app.services.controllers.reports_ctrl import ReportsController
from app.services.controllers.training_ctrl import TrainingController
from app.services.controllers.troops_ctrl import TroopsController
from app.services.controllers.villages_ctrl import VillagesController
from app.services.controllers.world_sql_ctrl import WorldSqlController

__all__ = [
    "BuildingController",
    "FarmingController",
    "HeroController",
    "MaintenanceController",
    "MapScanController",
    "ReportsController",
    "TrainingController",
    "TroopsController",
    "VillagesController",
    "WorldSqlController",
]
