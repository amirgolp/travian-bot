import { useMemo, useState } from "react";
import {
  Alert,
  Autocomplete,
  Button,
  Card,
  CardContent,
  Chip,
  Grid2 as Grid,
  IconButton,
  LinearProgress,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import {
  ArrowLeft,
  Crown,
  Hammer,
  Home,
  Swords,
  TreePine,
  Pickaxe,
  Wheat,
  HelpCircle,
  ShieldAlert,
  Shield,
  Plus,
  Trash2,
  Target,
} from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import {
  useDeleteTroopGoal,
  usePatchTroopGoal,
  useTroopCatalog,
  useTroopGoals,
  useUpsertTroopGoal,
  useVillageOverview,
  type Movement,
  type TroopInfo,
} from "../api/hooks";

export default function VillageDetail() {
  const { id } = useParams();
  const villageId = id ? Number(id) : undefined;
  const navigate = useNavigate();
  const { data, isPending, isError, error } = useVillageOverview(villageId);
  const { data: catalog } = useTroopCatalog(villageId);

  // key -> short display name ("t4" -> "Theutates Thunder"). Falls back to the
  // raw key for unknown tribes or when the catalog hasn't loaded yet.
  const troopNames = useMemo<Record<string, string>>(() => {
    const m: Record<string, string> = {};
    (catalog?.troops ?? []).forEach((t) => (m[t.key] = t.name));
    return m;
  }, [catalog]);

  if (!villageId) return <Typography color="error">Missing village id</Typography>;
  if (isPending) return <LinearProgress />;
  if (isError) return <Alert severity="error">{String(error)}</Alert>;
  if (!data) return null;

  const { village, resources, build, buildings, troops, movements_out, incoming_attacks, incoming_reinforcements, under_attack, missing } = data;
  const troopsNotYet = missing.includes("troops");

  return (
    <Stack spacing={2}>
      <Stack direction="row" alignItems="center" spacing={1}>
        <Chip
          icon={<ArrowLeft size={14} />}
          label="back"
          size="small"
          onClick={() => navigate(-1)}
          sx={{ cursor: "pointer" }}
        />
        <Home size={18} />
        <Typography variant="h5">{village.name}</Typography>
        <Typography variant="body2" color="text.secondary">
          ({village.x}|{village.y})
        </Typography>
        {village.is_capital && <Chip size="small" icon={<Crown size={12} />} label="capital" color="warning" />}
        {under_attack && (
          <Chip
            size="small"
            icon={<ShieldAlert size={12} />}
            label={`UNDER ATTACK (${incoming_attacks.length})`}
            color="error"
          />
        )}
      </Stack>

      <Grid container spacing={2}>
        <Grid size={{ xs: 12, md: 6 }}>
          <Card>
            <CardContent>
              <Typography variant="subtitle1" gutterBottom>
                Resources
              </Typography>
              <Grid container spacing={1}>
                <ResCell icon={TreePine} label="Wood" value={resources.wood} cap={resources.warehouse_cap} />
                <ResCell icon={Hammer} label="Clay" value={resources.clay} cap={resources.warehouse_cap} />
                <ResCell icon={Pickaxe} label="Iron" value={resources.iron} cap={resources.warehouse_cap} />
                <ResCell icon={Wheat} label="Crop" value={resources.crop} cap={resources.granary_cap} />
              </Grid>
              <Typography variant="caption" color="text.secondary" mt={1} component="div">
                warehouse {resources.warehouse_cap} · granary {resources.granary_cap}
              </Typography>
            </CardContent>
          </Card>
        </Grid>

        <Grid size={{ xs: 12, md: 6 }}>
          <Card>
            <CardContent>
              <Stack direction="row" alignItems="center" spacing={1} mb={1}>
                <Hammer size={16} />
                <Typography variant="subtitle1">
                  Currently upgrading ({build.observed.length})
                </Typography>
                {(() => {
                  // Empty dorf2 slots (key=null, slot>=19) — how many places
                  // are free if we want to queue a brand-new building.
                  const free = buildings.filter(
                    (b) => b.key == null && b.slot >= 19,
                  ).length;
                  return free > 0 ? (
                    <Chip
                      size="small"
                      variant="outlined"
                      color="success"
                      label={`${free} free slot${free === 1 ? "" : "s"}`}
                    />
                  ) : null;
                })()}
              </Stack>
              {build.observed.length === 0 ? (
                <Typography color="text.secondary" variant="body2">— nothing in progress —</Typography>
              ) : (
                build.observed.map((o, i) => {
                  // `finishes_in_seconds` is "time left" at scrape time.
                  // Add it to observed_at for an absolute, locale-formatted ETA.
                  const etaMs =
                    build.observed_at != null
                      ? new Date(build.observed_at).getTime() + o.finishes_in_seconds * 1000
                      : null;
                  return (
                    <Stack key={i} direction="row" spacing={1} alignItems="center" flexWrap="wrap">
                      <Typography variant="body2">
                        {o.name} → lvl {o.level}
                      </Typography>
                      <Chip
                        size="small"
                        variant="outlined"
                        label={formatDuration(o.finishes_in_seconds)}
                      />
                      {etaMs != null && (
                        <Typography variant="caption" color="text.secondary">
                          ETA {new Date(etaMs).toLocaleTimeString()}
                        </Typography>
                      )}
                    </Stack>
                  );
                })
              )}
              {build.observed_at && (
                <Typography variant="caption" color="text.secondary" component="div" mt={1}>
                  observed {new Date(build.observed_at).toLocaleString()}
                </Typography>
              )}
              {build.in_progress.length > 0 && (
                <>
                  <Typography variant="caption" color="text.secondary" component="div" mt={1}>
                    bot queue (IN_PROGRESS):
                  </Typography>
                  {build.in_progress.map((o) => (
                    <Typography key={o.id} variant="body2">
                      {o.building_key} → lvl {o.target_level}
                      {o.completes_at ? ` · ${new Date(o.completes_at).toLocaleTimeString()}` : ""}
                    </Typography>
                  ))}
                </>
              )}
            </CardContent>
          </Card>
        </Grid>

        <Grid size={{ xs: 12, md: 6 }}>
          <Card>
            <CardContent>
              <Typography variant="subtitle1" gutterBottom>
                Upgrade queue ({build.queued.length})
              </Typography>
              {build.queued.length === 0 ? (
                <Typography color="text.secondary" variant="body2">— empty —</Typography>
              ) : (
                build.queued.map((o) => (
                  <Stack key={o.id} direction="row" spacing={1} alignItems="center">
                    <Typography variant="body2">
                      {o.building_key} → lvl {o.target_level}
                    </Typography>
                    <Chip
                      size="small"
                      label={o.status}
                      color={o.status === "blocked" ? "warning" : "default"}
                    />
                    {o.blocked_reason && (
                      <Typography variant="caption" color="text.secondary">
                        {o.blocked_reason}
                      </Typography>
                    )}
                  </Stack>
                ))
              )}
            </CardContent>
          </Card>
        </Grid>

        <Grid size={{ xs: 12, md: 6 }}>
          <Card>
            <CardContent>
              <Stack direction="row" alignItems="center" spacing={1} mb={1}>
                <Swords size={16} />
                <Typography variant="subtitle1">
                  Troops at home ({troops.total})
                </Typography>
                {troops.consumption_per_hour > 0 && (
                  <Chip
                    size="small"
                    variant="outlined"
                    icon={<Wheat size={12} />}
                    label={`${troops.consumption_per_hour}/h`}
                  />
                )}
              </Stack>
              {troopsNotYet ? (
                <Alert severity="info" icon={<HelpCircle size={16} />} sx={{ py: 0.5 }}>
                  TroopsController hasn&apos;t scraped this village yet — it runs every ~7 min.
                </Alert>
              ) : Object.keys(troops.own).length === 0 ? (
                <Typography color="text.secondary" variant="body2">
                  — no troops at home —
                </Typography>
              ) : (
                <Grid container spacing={1}>
                  {Object.entries(troops.own)
                    .filter(([, n]) => n > 0)
                    .sort(([a], [b]) => a.localeCompare(b))
                    .map(([key, n]) => (
                      <Grid key={key} size={{ xs: 6, sm: 4, md: 3 }}>
                        <Chip
                          size="small"
                          variant="outlined"
                          label={`${n} · ${troopNames[key] ?? key}`}
                        />
                      </Grid>
                    ))}
                </Grid>
              )}
              {troops.observed_at && (
                <Typography variant="caption" color="text.secondary" component="div" mt={1}>
                  observed {new Date(troops.observed_at).toLocaleString()}
                </Typography>
              )}
            </CardContent>
          </Card>
        </Grid>

        <Grid size={{ xs: 12, md: 6 }}>
          <Card>
            <CardContent>
              <Stack direction="row" alignItems="center" spacing={1} mb={1}>
                <ShieldAlert size={16} />
                <Typography variant="subtitle1">
                  Incoming ({incoming_attacks.length + incoming_reinforcements.length})
                </Typography>
              </Stack>
              {incoming_attacks.length === 0 && incoming_reinforcements.length === 0 ? (
                <Typography color="text.secondary" variant="body2">— nothing incoming —</Typography>
              ) : (
                <Stack spacing={0.5}>
                  {incoming_attacks.map((m, i) => (
                    <MovementLine key={`a${i}`} m={m} troopNames={troopNames} attack />
                  ))}
                  {incoming_reinforcements.map((m, i) => (
                    <MovementLine key={`r${i}`} m={m} troopNames={troopNames} />
                  ))}
                </Stack>
              )}
            </CardContent>
          </Card>
        </Grid>

        <Grid size={{ xs: 12 }}>
          <Card>
            <CardContent>
              <Stack direction="row" alignItems="center" spacing={1} mb={1}>
                <Shield size={16} />
                <Typography variant="subtitle1">Outgoing ({movements_out.length})</Typography>
              </Stack>
              {movements_out.length === 0 ? (
                <Typography color="text.secondary" variant="body2">— nothing out —</Typography>
              ) : (
                <Stack spacing={0.5}>
                  {movements_out.map((m, i) => (
                    <MovementLine key={i} m={m} troopNames={troopNames} />
                  ))}
                </Stack>
              )}
            </CardContent>
          </Card>
        </Grid>

        <Grid size={{ xs: 12 }}>
          <TroopGoalsCard villageId={villageId} currentCounts={troops.own} />
        </Grid>

        <Grid size={{ xs: 12 }}>
          <Card>
            <CardContent>
              <Typography variant="subtitle1" gutterBottom>
                Buildings ({buildings.length} known slots)
              </Typography>
              <Grid container spacing={1}>
                {buildings.map((b) => (
                  <Grid key={b.slot} size={{ xs: 6, sm: 4, md: 3, lg: 2 }}>
                    <Chip
                      size="small"
                      variant="outlined"
                      label={`#${b.slot} ${b.key ?? "—"} lvl ${b.level}`}
                    />
                  </Grid>
                ))}
              </Grid>
            </CardContent>
          </Card>
        </Grid>
      </Grid>
    </Stack>
  );
}

function TroopGoalsCard({
  villageId,
  currentCounts,
}: {
  villageId: number;
  currentCounts: Record<string, number>;
}) {
  const { data: goals = [], isPending } = useTroopGoals(villageId);
  const { data: catalog } = useTroopCatalog(villageId);
  const upsert = useUpsertTroopGoal();
  const patch = usePatchTroopGoal();
  const del = useDeleteTroopGoal();

  const [newKey, setNewKey] = useState<string>("");
  const [newTarget, setNewTarget] = useState<string>("100");

  const byKey = useMemo(() => {
    const m = new Map<string, TroopInfo>();
    (catalog?.troops ?? []).forEach((t) => m.set(t.key, t));
    return m;
  }, [catalog]);

  const sorted = [...goals].sort((a, b) => a.priority - b.priority || a.id - b.id);

  const trainableKeys = (catalog?.troops ?? []).filter((t) => t.gid !== null);

  return (
    <Card>
      <CardContent>
        <Stack direction="row" alignItems="center" spacing={1} mb={1}>
          <Target size={16} />
          <Typography variant="subtitle1">Troop goals</Typography>
          {catalog?.tribe && (
            <Chip size="small" variant="outlined" label={catalog.tribe} />
          )}
        </Stack>
        <Typography variant="caption" color="text.secondary" mb={1} component="div">
          The training controller keeps topping up each troop until the target is hit.
          Uses whatever resources your barracks / stable / workshop has available; lower
          priority = trained first.
        </Typography>

        {isPending && <LinearProgress />}
        {!isPending && sorted.length === 0 && (
          <Typography color="text.secondary" variant="body2" mb={1}>
            — no goals set —
          </Typography>
        )}

        <Stack spacing={1} mt={1}>
          {sorted.map((g) => {
            const info = byKey.get(g.troop_key);
            const current = currentCounts?.[g.troop_key] ?? 0;
            const pct = g.target_count > 0
              ? Math.min(100, Math.round((current * 100) / g.target_count))
              : 0;
            const done = current >= g.target_count;
            return (
              <Stack
                key={g.id}
                direction="row"
                spacing={1}
                alignItems="center"
                sx={{ opacity: g.paused ? 0.6 : 1 }}
              >
                <Chip
                  size="small"
                  variant="outlined"
                  label={info?.building ?? "?"}
                  sx={{ minWidth: 80 }}
                />
                <Typography variant="body2" sx={{ minWidth: 160 }}>
                  {info?.name ?? g.troop_key}{" "}
                  <Typography component="span" variant="caption" color="text.secondary">
                    ({g.troop_key})
                  </Typography>
                </Typography>
                <Stack flex={1} spacing={0.5}>
                  <LinearProgress
                    variant="determinate"
                    value={pct}
                    color={done ? "success" : "primary"}
                    sx={{ height: 6, borderRadius: 3 }}
                  />
                  <Typography variant="caption" color="text.secondary">
                    {current} / {g.target_count}
                  </Typography>
                </Stack>
                <TextField
                  size="small"
                  type="number"
                  value={g.target_count}
                  sx={{ width: 90 }}
                  onChange={(e) =>
                    patch.mutate({
                      id: g.id,
                      patch: { target_count: Number(e.target.value || 0) },
                    })
                  }
                />
                <Switch
                  size="small"
                  checked={!g.paused}
                  onChange={(_e, v) => patch.mutate({ id: g.id, patch: { paused: !v } })}
                />
                <IconButton
                  size="small"
                  color="error"
                  onClick={() => del.mutate(g.id)}
                  aria-label="delete goal"
                >
                  <Trash2 size={14} />
                </IconButton>
              </Stack>
            );
          })}
        </Stack>

        {/* Add goal */}
        <Stack direction="row" spacing={1} alignItems="center" mt={2}>
          <Autocomplete<TroopInfo>
            size="small"
            sx={{ minWidth: 220 }}
            options={trainableKeys}
            value={trainableKeys.find((t) => t.key === newKey) ?? null}
            onChange={(_e, v) => setNewKey(v?.key ?? "")}
            getOptionLabel={(t) => `${t.name} (${t.key}) · ${t.building}`}
            isOptionEqualToValue={(a, b) => a.key === b.key}
            getOptionDisabled={(t) => goals.some((g) => g.troop_key === t.key)}
            renderInput={(params) => <TextField {...params} label="Troop" />}
          />
          <TextField
            size="small"
            label="Target"
            type="number"
            value={newTarget}
            sx={{ width: 120 }}
            onChange={(e) => setNewTarget(e.target.value)}
          />
          <Button
            variant="contained"
            size="small"
            startIcon={<Plus size={14} />}
            disabled={!newKey || !newTarget || Number(newTarget) <= 0}
            onClick={async () => {
              await upsert.mutateAsync({
                village_id: villageId,
                troop_key: newKey,
                target_count: Number(newTarget),
              });
              setNewKey("");
            }}
          >
            Add goal
          </Button>
        </Stack>
      </CardContent>
    </Card>
  );
}

function formatDuration(seconds: number): string {
  if (seconds <= 0) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

// Palette maps the rally-point `direction` string to an MUI Chip color.
// `attack` is a forcing override used for the incoming-attacks bucket — a
// reinforcement MIGHT be hostile but we don't know; the caller flips this
// manually when it's sure the row is an incoming attack.
const DIRECTION_COLOR: Record<
  string,
  "default" | "primary" | "secondary" | "error" | "info" | "success" | "warning"
> = {
  out_raid: "warning",
  out_attack: "error",
  out_reinforce: "info",
  out_hero: "secondary",
  in_attack: "error",
  in_reinforce: "info",
  in_return: "success",
  in_hero: "secondary",
};

function MovementLine({
  m,
  troopNames,
  attack = false,
}: {
  m: Movement;
  troopNames: Record<string, string>;
  attack?: boolean;
}) {
  const total = Object.values(m.troops).reduce((a, b) => a + b, 0);
  const troopChips = Object.entries(m.troops)
    .filter(([, n]) => n > 0)
    .sort(([a], [b]) => a.localeCompare(b));
  const coords =
    m.target_x != null && m.target_y != null ? ` (${m.target_x}|${m.target_y})` : "";
  const color = attack ? "error" : DIRECTION_COLOR[m.direction] ?? "default";

  return (
    <Stack spacing={0.5} sx={{ borderLeft: 3, borderColor: `${color}.main`, pl: 1, py: 0.5 }}>
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
        <Chip size="small" color={color} label={m.direction.replace("_", " ")} />
        <Typography variant="body2" sx={{ flex: 1, minWidth: 0 }} noWrap title={m.headline}>
          {m.headline.slice(0, 80)}
          {coords}
        </Typography>
        <Chip size="small" variant="outlined" label={formatDuration(m.arrival_in_seconds)} />
      </Stack>
      {troopChips.length > 0 && (
        <Stack direction="row" spacing={0.5} flexWrap="wrap" alignItems="center" useFlexGap>
          <Typography variant="caption" color="text.secondary" sx={{ mr: 0.5 }}>
            {total} total:
          </Typography>
          {troopChips.map(([key, n]) => (
            <Chip
              key={key}
              size="small"
              variant="outlined"
              color={color}
              label={`${n} × ${troopNames[key] ?? key}`}
            />
          ))}
        </Stack>
      )}
    </Stack>
  );
}

function ResCell({
  icon: Icon,
  label,
  value,
  cap,
}: {
  icon: typeof TreePine;
  label: string;
  value: number;
  cap: number;
}) {
  const pct = cap > 0 ? Math.min(100, Math.round((value * 100) / cap)) : 0;
  return (
    <Grid size={{ xs: 6 }}>
      <Stack direction="row" alignItems="center" spacing={1}>
        <Icon size={16} />
        <Stack flex={1}>
          <Typography variant="caption" color="text.secondary">
            {label}
          </Typography>
          <Typography variant="body2">{value.toLocaleString()}</Typography>
          <LinearProgress
            variant="determinate"
            value={pct}
            sx={{ height: 4, borderRadius: 2 }}
            color={pct > 90 ? "warning" : "primary"}
          />
        </Stack>
      </Stack>
    </Grid>
  );
}
