import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type {
  Account,
  BuildingCatalog,
  BuildOrder,
  Farmlist,
  Village,
  WorkersStatusResponse,
} from "./types";

// Cadence — poll the status endpoint frequently; leave lists on tanstack's default.
const STATUS_REFETCH_MS = 5_000;

// --- queries ---

export const useAccounts = () =>
  useQuery({ queryKey: ["accounts"], queryFn: () => api.get<Account[]>("/accounts") });

export const useWorkerStatus = () =>
  useQuery({
    queryKey: ["accounts", "status"],
    queryFn: () => api.get<WorkersStatusResponse>("/accounts/status"),
    refetchInterval: STATUS_REFETCH_MS,
  });

export const useVillages = (accountId?: number) =>
  useQuery({
    queryKey: ["villages", accountId ?? "all"],
    queryFn: () =>
      api.get<Village[]>(
        accountId != null ? `/villages?account_id=${accountId}` : "/villages",
      ),
  });

export const useFarmlists = (villageId?: number) =>
  useQuery({
    queryKey: ["farmlists", villageId ?? "all"],
    queryFn: () =>
      api.get<Farmlist[]>(
        villageId != null ? `/farmlists?village_id=${villageId}` : "/farmlists",
      ),
  });

export const useBuildOrders = (villageId: number | undefined) =>
  useQuery({
    queryKey: ["build", "orders", villageId],
    enabled: villageId != null,
    queryFn: () => api.get<BuildOrder[]>(`/build/orders?village_id=${villageId}`),
  });

export const useBuildingCatalog = () =>
  useQuery({
    queryKey: ["build", "catalog"],
    queryFn: () => api.get<BuildingCatalog>("/build/catalog"),
    staleTime: 60 * 60_000, // catalog is ~immutable at runtime
  });

// --- mutations ---

export const useCreateAccount = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      label: string;
      server_url: string;
      username: string;
      password: string;
      active_hours?: string;
    }) => api.post<Account>("/accounts", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts"] }),
  });
};

export const usePatchAccount = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: { active_hours?: string } }) =>
      api.patch<Account>(`/accounts/${id}`, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts"] }),
  });
};

export const useStartAccount = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.post(`/accounts/${id}/start`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts", "status"] }),
  });
};

export const useStopAccount = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.post(`/accounts/${id}/stop`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts", "status"] }),
  });
};

export const useStartAllAccounts = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post("/accounts/start_all"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts", "status"] }),
  });
};

export const useStopAllAccounts = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post("/accounts/stop_all"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts", "status"] }),
  });
};

export const useCreateBuildOrder = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      village_id: number;
      building_key: string;
      target_level: number;
      slot?: number | null;
      priority?: number;
    }) => api.post<BuildOrder>("/build/orders", body),
    onSuccess: (_res, vars) =>
      qc.invalidateQueries({ queryKey: ["build", "orders", vars.village_id] }),
  });
};

export const useDeleteBuildOrder = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.del(`/build/orders/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["build", "orders"] }),
  });
};

export const useReorderBuildOrders = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { village_id: number; ordered_ids: number[] }) =>
      api.post("/build/reorder", body),
    onSuccess: (_res, vars) =>
      qc.invalidateQueries({ queryKey: ["build", "orders", vars.village_id] }),
  });
};

// --- controller toggles ---
export interface ControllerToggleState {
  enabled: Record<string, boolean>;
}

export const useControllerToggles = (accountId: number | undefined) =>
  useQuery({
    queryKey: ["accounts", accountId, "controllers"],
    enabled: accountId != null,
    queryFn: () => api.get<ControllerToggleState>(`/accounts/${accountId}/controllers`),
  });

export const useSetControllerToggles = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ accountId, enabled }: { accountId: number; enabled: Record<string, boolean> }) =>
      api.post<ControllerToggleState>(`/accounts/${accountId}/controllers`, { enabled }),
    onSuccess: (_res, vars) =>
      qc.invalidateQueries({ queryKey: ["accounts", vars.accountId, "controllers"] }),
  });
};

// --- feature flags ---
export interface FeatureFlags {
  watch_video_bonuses: boolean;
}

export const useFeatures = (accountId: number | undefined) =>
  useQuery({
    queryKey: ["accounts", accountId, "features"],
    enabled: accountId != null,
    queryFn: () => api.get<FeatureFlags>(`/accounts/${accountId}/features`),
  });

export const useSetFeatures = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ accountId, patch }: { accountId: number; patch: Partial<FeatureFlags> }) =>
      api.post<FeatureFlags>(`/accounts/${accountId}/features`, patch),
    onSuccess: (_res, vars) =>
      qc.invalidateQueries({ queryKey: ["accounts", vars.accountId, "features"] }),
  });
};

// --- village overview ---

export interface Movement {
  direction:
    | "out_raid"
    | "out_attack"
    | "out_reinforce"
    | "in_return"
    | "in_attack"
    | "in_reinforce"
    | "unknown";
  headline: string;
  target_x: number | null;
  target_y: number | null;
  troops: Record<string, number>;
  arrival_in_seconds: number;
  is_attack: boolean;
}

export interface VillageOverview {
  village: {
    id: number; account_id: number; travian_id: number;
    name: string; x: number; y: number; is_capital: boolean; tribe: string | null;
  };
  resources: {
    wood: number; clay: number; iron: number; crop: number;
    warehouse_cap: number; granary_cap: number;
  };
  build: {
    in_progress: BuildOrderLite[];
    queued: BuildOrderLite[];
    history: BuildOrderLite[];
    // In-game queue scraped from dorf1 — what Travian is actually
    // building right now (includes user-initiated upgrades).
    observed: { name: string; level: number; finishes_in_seconds: number }[];
    // Scrape timestamp the `finishes_in_seconds` values were captured at.
    // Client computes ETA = observed_at + finishes_in_seconds.
    observed_at: string | null;
  };
  buildings: { slot: number; key: string | null; level: number }[];
  troops: {
    own: Record<string, number>;
    consumption_per_hour: number;
    total: number;
    observed_at: string | null;
  };
  movements_in: Movement[];
  movements_out: Movement[];
  incoming_attacks: Movement[];
  incoming_reinforcements: Movement[];
  under_attack: boolean;
  missing: string[];
}

interface BuildOrderLite {
  id: number;
  building_key: string;
  target_level: number;
  slot: number | null;
  priority: number;
  status: string;
  blocked_reason: string | null;
  completes_at: string | null;
}

export const useVillageOverview = (villageId: number | undefined) =>
  useQuery({
    queryKey: ["village", villageId, "overview"],
    enabled: villageId != null,
    queryFn: () => api.get<VillageOverview>(`/villages/${villageId}/overview`),
    refetchInterval: 15_000,
  });

// --- farmlist detail ---

export interface FarmlistDetail {
  farmlist: {
    id: number; village_id: number; name: string;
    kind: "villages" | "oases_natars" | "mixed";
    enabled: boolean; interval_seconds: number;
    default_troops: Record<string, number>;
  };
  source_village: { id: number; name: string; x: number; y: number };
  slots: FarmlistSlotRow[];
}

export interface FarmlistSlotRow {
  slot_id: number;
  enabled: boolean;
  consecutive_losses: number;
  last_raid_at: string | null;
  troops: Record<string, number>;
  distance: number | null;
  tile: null | {
    id: number; x: number; y: number; type: string; name: string | null;
    player_name: string | null; alliance_name: string | null;
    population: number | null; oasis_type: string | null;
    raid_count: number; win_count: number; loss_count: number;
    empty_count: number; total_bounty: number;
    last_raid_outcome: "win" | "loss" | "empty" | null;
    last_raid_capacity_pct: number | null;
  };
}

export const useFarmlistDetail = (farmlistId: number | undefined) =>
  useQuery({
    queryKey: ["farmlist", farmlistId],
    enabled: farmlistId != null,
    queryFn: () => api.get<FarmlistDetail>(`/farmlists/${farmlistId}`),
    refetchInterval: 20_000,
  });

export const useToggleSlot = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ slotId, enabled }: { slotId: number; enabled: boolean }) =>
      api.post(`/farmlists/slots/${slotId}/toggle`, { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["farmlist"] }),
  });
};

export const useToggleFarmlist = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ farmlistId, enabled }: { farmlistId: number; enabled: boolean }) =>
      api.post(`/farmlists/${farmlistId}/toggle`, { enabled }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["farmlists"] });
      qc.invalidateQueries({ queryKey: ["farmlist"] });
    },
  });
};

export const useToggleAllSlots = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ farmlistId, enabled }: { farmlistId: number; enabled: boolean }) =>
      api.post(`/farmlists/${farmlistId}/slots/toggle_all`, { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["farmlist"] }),
  });
};

export const useSetFarmlistInterval = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ farmlistId, intervalSeconds }: { farmlistId: number; intervalSeconds: number }) =>
      api.patch(`/farmlists/${farmlistId}/interval`, { interval_seconds: intervalSeconds }),
    onSuccess: (_res, vars) => {
      qc.invalidateQueries({ queryKey: ["farmlist", vars.farmlistId] });
      qc.invalidateQueries({ queryKey: ["farmlists"] });
    },
  });
};

// Update the per-raid troop composition used when slots have no override.
export const useSetDefaultTroops = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ farmlistId, troops }: { farmlistId: number; troops: Record<string, number> }) =>
      api.patch(`/farmlists/${farmlistId}/default_troops`, { troops }),
    onSuccess: (_res, vars) => {
      qc.invalidateQueries({ queryKey: ["farmlist", vars.farmlistId] });
      qc.invalidateQueries({ queryKey: ["farmlists"] });
    },
  });
};

// Village-level minimum troops to keep home — deducted from the farming
// dispatch budget before any raid is sent.
export const useTroopsReserve = (villageId: number | undefined) =>
  useQuery({
    queryKey: ["village-reserve", villageId],
    enabled: villageId !== undefined,
    queryFn: async () => {
      const res = await api.get(`/villages/${villageId}/troops_reserve`);
      return res.data as { troops: Record<string, number> };
    },
  });

export const useSetTroopsReserve = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ villageId, troops }: { villageId: number; troops: Record<string, number> }) =>
      api.patch(`/villages/${villageId}/troops_reserve`, { troops }),
    onSuccess: (_res, vars) => {
      qc.invalidateQueries({ queryKey: ["village-reserve", vars.villageId] });
    },
  });
};

// --- troop goals ---

export interface TroopGoal {
  id: number;
  village_id: number;
  troop_key: string;           // "t1".."t10"
  target_count: number;
  priority: number;
  paused: boolean;
}

export interface TroopInfo {
  key: string;
  name: string;
  building: "barracks" | "stable" | "workshop" | "residence" | null;
  gid: number | null;
}

export const useTroopGoals = (villageId: number | undefined) =>
  useQuery({
    queryKey: ["troop_goals", villageId],
    enabled: villageId != null,
    queryFn: () => api.get<TroopGoal[]>(`/troop_goals?village_id=${villageId}`),
  });

export const useTroopCatalog = (villageId: number | undefined) =>
  useQuery({
    queryKey: ["troop_catalog", villageId],
    enabled: villageId != null,
    queryFn: () =>
      api.get<{ tribe: string | null; troops: TroopInfo[] }>(
        `/troop_goals/catalog?village_id=${villageId}`,
      ),
    staleTime: 60 * 60_000,
  });

export const useUpsertTroopGoal = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      village_id: number;
      troop_key: string;
      target_count: number;
      priority?: number;
    }) => api.post<TroopGoal>("/troop_goals", body),
    onSuccess: (_res, vars) =>
      qc.invalidateQueries({ queryKey: ["troop_goals", vars.village_id] }),
  });
};

export const usePatchTroopGoal = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: Partial<Omit<TroopGoal, "id" | "village_id" | "troop_key">> }) =>
      api.patch<TroopGoal>(`/troop_goals/${id}`, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["troop_goals"] }),
  });
};

export const useDeleteTroopGoal = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.del(`/troop_goals/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["troop_goals"] }),
  });
};
