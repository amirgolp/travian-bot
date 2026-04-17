// Mirrors app/api/schemas.py — keep in sync by hand.

export interface Account {
  id: number;
  label: string;
  server_url: string;
  server_code: string;
  status: "active" | "paused" | "banned" | "error";
  active_hours: string | null;
}

export interface Village {
  id: number;
  account_id: number;
  name: string;
  x: number;
  y: number;
  is_capital: boolean;
}

export interface Farmlist {
  id: number;
  village_id: number;
  name: string;
  kind: "villages" | "oases_natars" | "mixed";
  enabled: boolean;
  interval_seconds: number;
}

export interface BuildOrder {
  id: number;
  village_id: number;
  building_key: string;
  target_level: number;
  slot: number | null;
  priority: number;
  status: "queued" | "blocked" | "in_progress" | "done" | "failed" | "cancelled";
  blocked_reason: string | null;
}

export interface BuildingCatalogEntry {
  gid: number;
  name: string;
  category: string;
  placement: "dorf1" | "dorf2" | "both";
  max_level: number;
  unique: boolean;
  prereqs: { key: string; level: number }[];
}

export type BuildingCatalog = Record<string, BuildingCatalogEntry>;

export interface ControllerSnapshot {
  name: string;
  errors: number;
  last_run: string | null;
  last_message: string;
  resync_seconds: number;
}

export interface WorkerStatus {
  running: boolean;
  controllers: ControllerSnapshot[];
}

export type WorkersStatusResponse = { workers: Record<string, WorkerStatus> };
