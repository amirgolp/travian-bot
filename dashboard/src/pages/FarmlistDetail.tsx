import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Autocomplete,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  LinearProgress,
  Stack,
  Switch,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { MaterialReactTable, type MRT_ColumnDef, useMaterialReactTable } from "material-react-table";
import { ArrowLeft, FileText, Plus, Power, PowerOff, Save, Swords, Trash2 } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import {
  useFarmlistDetail,
  useSetDefaultTroops,
  useSetFarmlistInterval,
  useSetTroopsReserve,
  useToggleAllSlots,
  useToggleSlot,
  useTroopCatalog,
  useTroopsReserve,
  type FarmlistSlotRow,
  type TroopInfo,
} from "../api/hooks";
import { Shield } from "lucide-react";

export default function FarmlistDetail() {
  const { id } = useParams();
  const farmlistId = id ? Number(id) : undefined;
  const navigate = useNavigate();
  const { data, isPending, isError, error } = useFarmlistDetail(farmlistId);
  const toggle = useToggleSlot();
  const toggleAll = useToggleAllSlots();

  // Troop keys shown as columns: union of every slot's keys + the list's default.
  const troopKeys = useMemo(() => {
    const set = new Set<string>();
    Object.keys(data?.farmlist.default_troops ?? {}).forEach((k) => set.add(k));
    (data?.slots ?? []).forEach((s) => Object.keys(s.troops ?? {}).forEach((k) => set.add(k)));
    return Array.from(set).sort();
  }, [data]);

  const columns = useMemo<MRT_ColumnDef<FarmlistSlotRow>[]>(() => {
    const base: MRT_ColumnDef<FarmlistSlotRow>[] = [
      {
        header: "Target",
        id: "target",
        accessorFn: (r) => (r.tile ? `(${r.tile.x}|${r.tile.y}) ${r.tile.name ?? ""}` : "—"),
      },
      {
        header: "Type",
        accessorFn: (r) => r.tile?.type ?? "?",
        id: "type",
        Cell: ({ cell }) => <Chip size="small" label={String(cell.getValue() ?? "?")} />,
        size: 90,
      },
      {
        header: "Distance",
        accessorFn: (r) => r.distance,
        id: "distance",
        Cell: ({ cell }) => {
          const v = cell.getValue<number | null>();
          return v == null ? "—" : v.toFixed(1);
        },
        size: 90,
      },
      { header: "Raids", accessorFn: (r) => r.tile?.raid_count ?? 0, id: "raids", size: 70 },
      { header: "Empty", accessorFn: (r) => r.tile?.empty_count ?? 0, id: "empties", size: 70 },
      { header: "Loot", accessorFn: (r) => r.tile?.total_bounty ?? 0, id: "loot", size: 80 },
      {
        header: "Losses",
        id: "losses",
        accessorFn: (r) => r.consecutive_losses,
        Cell: ({ cell }) => {
          const v = cell.getValue<number>();
          return v > 0 ? (
            <Chip size="small" color={v >= 3 ? "warning" : "default"} label={v} />
          ) : (
            "0"
          );
        },
        size: 80,
      },
    ];
    troopKeys.forEach((k) => {
      base.push({
        header: k,
        id: `troop-${k}`,
        accessorFn: (r) => r.troops[k] ?? 0,
        size: 70,
      });
    });
    base.push({
      header: "On",
      id: "toggle",
      enableSorting: false,
      muiTableBodyCellProps: { onClick: (e) => e.stopPropagation() },
      Cell: ({ row }) => (
        <Switch
          size="small"
          checked={row.original.enabled}
          onChange={(_e, v) =>
            toggle.mutate({ slotId: row.original.slot_id, enabled: v })
          }
        />
      ),
      size: 70,
    });
    base.push({
      header: "Reports",
      id: "reports",
      enableSorting: false,
      muiTableBodyCellProps: { onClick: (e) => e.stopPropagation() },
      Cell: ({ row }) => {
        const tile = row.original.tile;
        if (!tile) return null;
        return (
          <Tooltip title="Open reports for this target">
            <Chip
              size="small"
              icon={<FileText size={12} />}
              label="open"
              onClick={() => navigate(`/reports?tile_id=${tile.id}`)}
              sx={{ cursor: "pointer" }}
            />
          </Tooltip>
        );
      },
      size: 100,
    });
    return base;
  }, [troopKeys, toggle, navigate]);

  const table = useMaterialReactTable({
    columns,
    data: data?.slots ?? [],
    state: { isLoading: isPending },
    initialState: {
      density: "compact",
      sorting: [{ id: "distance", desc: false }],
    },
    // Colour each row by the outcome of the most recent raid on that tile:
    //   win  + capacity ≥ 90   → green   (hauled full)
    //   win  + capacity <  90  → yellow  (partial / target almost empty)
    //   empty                  → grey    (nothing there — may still recover)
    //   loss                   → red     (our troops died)
    //   no raid yet            → default
    muiTableBodyRowProps: ({ row }) => {
      const tile = row.original.tile;
      const out = tile?.last_raid_outcome;
      const cap = tile?.last_raid_capacity_pct ?? 0;
      let bg: string | undefined;
      if (out === "win" && cap >= 90) bg = "rgba(76, 175, 80, 0.15)";
      else if (out === "win") bg = "rgba(255, 193, 7, 0.18)";
      else if (out === "empty") bg = "rgba(158, 158, 158, 0.15)";
      else if (out === "loss") bg = "rgba(244, 67, 54, 0.18)";
      return { sx: bg ? { backgroundColor: bg } : undefined };
    },
  });

  if (!farmlistId) return <Typography color="error">Missing farmlist id</Typography>;
  if (isPending) return <LinearProgress />;
  if (isError) return <Alert severity="error">{String(error)}</Alert>;
  if (!data) return null;

  const anySlotOn = (data.slots ?? []).some((s) => s.enabled);
  const anySlotOff = (data.slots ?? []).some((s) => !s.enabled);

  return (
    <Stack spacing={2}>
      <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap">
        <Chip
          icon={<ArrowLeft size={14} />}
          label="back"
          size="small"
          onClick={() => navigate(-1)}
          sx={{ cursor: "pointer" }}
        />
        <Typography variant="h5">{data.farmlist.name}</Typography>
        <Chip size="small" label={data.farmlist.kind} />
        <Chip
          size="small"
          label={data.farmlist.enabled ? "enabled" : "disabled"}
          color={data.farmlist.enabled ? "success" : "default"}
        />
        <Typography variant="caption" color="text.secondary">
          from {data.source_village.name} ({data.source_village.x}|{data.source_village.y})
        </Typography>
        <IntervalEditor
          farmlistId={farmlistId!}
          intervalSeconds={data.farmlist.interval_seconds}
        />
        <Box flex={1} />
        <Button
          size="small"
          variant="outlined"
          startIcon={<Power size={14} />}
          disabled={!anySlotOff}
          onClick={() => toggleAll.mutate({ farmlistId: farmlistId!, enabled: true })}
        >
          Enable all
        </Button>
        <Button
          size="small"
          variant="outlined"
          color="warning"
          startIcon={<PowerOff size={14} />}
          disabled={!anySlotOn}
          onClick={() => toggleAll.mutate({ farmlistId: farmlistId!, enabled: false })}
        >
          Disable all
        </Button>
      </Stack>

      <DefaultTroopsCard
        farmlistId={farmlistId!}
        villageId={data.source_village.id}
        initial={data.farmlist.default_troops}
      />

      <TroopsReserveCard villageId={data.source_village.id} />

      <MaterialReactTable table={table} />
    </Stack>
  );
}


function IntervalEditor({
  farmlistId,
  intervalSeconds,
}: {
  farmlistId: number;
  intervalSeconds: number;
}) {
  const setter = useSetFarmlistInterval();
  const currentMin = Math.max(1, Math.round(intervalSeconds / 60));
  const [draft, setDraft] = useState<string>(String(currentMin));

  useEffect(() => {
    setDraft(String(currentMin));
  }, [currentMin]);

  const n = Number(draft);
  const valid = Number.isFinite(n) && n >= 1;
  const dirty = valid && n !== currentMin;

  const save = () => {
    if (!dirty) return;
    setter.mutate({ farmlistId, intervalSeconds: Math.floor(n) * 60 });
  };

  return (
    <Stack direction="row" spacing={0.5} alignItems="center">
      <TextField
        size="small"
        label="Interval (min, 1x)"
        type="number"
        inputProps={{ min: 1 }}
        value={draft}
        error={!valid}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") save();
        }}
        sx={{ width: 150 }}
      />
      <Button
        size="small"
        variant="contained"
        startIcon={<Save size={14} />}
        disabled={!dirty || setter.isPending}
        onClick={save}
      >
        Save
      </Button>
    </Stack>
  );
}


function DefaultTroopsCard({
  farmlistId,
  villageId,
  initial,
}: {
  farmlistId: number;
  villageId: number;
  initial: Record<string, number>;
}) {
  const { data: catalog } = useTroopCatalog(villageId);
  const setter = useSetDefaultTroops();

  // Local draft so the user can stage several edits before hitting Save.
  const [draft, setDraft] = useState<Record<string, number>>({});
  const [pickKey, setPickKey] = useState<string>("");
  const [pickCount, setPickCount] = useState<string>("1");

  useEffect(() => {
    setDraft(initial ?? {});
  }, [initial, farmlistId]);

  const nameFor = (key: string) =>
    catalog?.troops.find((t) => t.key === key)?.name ?? key;

  const dirty = useMemo(() => {
    const a = JSON.stringify(Object.entries(draft).sort());
    const b = JSON.stringify(Object.entries(initial ?? {}).sort());
    return a !== b;
  }, [draft, initial]);

  const add = () => {
    const n = Number(pickCount);
    if (!pickKey || !Number.isFinite(n) || n <= 0) return;
    setDraft((d) => ({ ...d, [pickKey]: (d[pickKey] ?? 0) + Math.floor(n) }));
    setPickCount("1");
  };

  const remove = (k: string) =>
    setDraft((d) => {
      const next = { ...d };
      delete next[k];
      return next;
    });

  const save = () => setter.mutate({ farmlistId, troops: draft });

  return (
    <Card>
      <CardContent>
        <Stack direction="row" alignItems="center" spacing={1} mb={1}>
          <Swords size={16} />
          <Typography variant="subtitle1">Per-raid troop composition</Typography>
          <Typography variant="caption" color="text.secondary">
            · applied to every slot without its own override · dispatch stops
            when home troops run out · cavalry routes to farther targets first,
            infantry to nearer ones
          </Typography>
        </Stack>

        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" mb={1}>
          <Autocomplete<TroopInfo>
            size="small"
            sx={{ minWidth: 220 }}
            options={catalog?.troops ?? []}
            value={(catalog?.troops ?? []).find((t) => t.key === pickKey) ?? null}
            onChange={(_e, v) => setPickKey(v?.key ?? "")}
            getOptionLabel={(t) => t.name}
            isOptionEqualToValue={(a, b) => a.key === b.key}
            renderInput={(params) => <TextField {...params} label="Troop" />}
          />
          <TextField
            size="small"
            label="Per raid"
            type="number"
            inputProps={{ min: 1 }}
            value={pickCount}
            sx={{ width: 120 }}
            onChange={(e) => setPickCount(e.target.value)}
          />
          <Button
            size="small"
            variant="contained"
            startIcon={<Plus size={14} />}
            disabled={!pickKey || Number(pickCount) <= 0}
            onClick={add}
          >
            Add
          </Button>
          <Box flex={1} />
          <Button
            size="small"
            variant="contained"
            color="success"
            startIcon={<Save size={14} />}
            disabled={!dirty || setter.isPending}
            onClick={save}
          >
            Save
          </Button>
        </Stack>

        <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
          {Object.entries(draft).length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              — no default composition — slots without their own override will be skipped
            </Typography>
          ) : (
            Object.entries(draft)
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([k, n]) => (
                <Chip
                  key={k}
                  size="small"
                  label={`${n} × ${nameFor(k)}`}
                  onDelete={() => remove(k)}
                  deleteIcon={<Trash2 size={12} />}
                />
              ))
          )}
        </Stack>
      </CardContent>
    </Card>
  );
}


function TroopsReserveCard({ villageId }: { villageId: number }) {
  const { data: reserve } = useTroopsReserve(villageId);
  const { data: catalog } = useTroopCatalog(villageId);
  const setter = useSetTroopsReserve();

  const [draft, setDraft] = useState<Record<string, number>>({});
  const [pickKey, setPickKey] = useState<string>("");
  const [pickCount, setPickCount] = useState<string>("10");

  useEffect(() => {
    setDraft(reserve?.troops ?? {});
  }, [reserve, villageId]);

  const nameFor = (key: string) =>
    catalog?.troops.find((t) => t.key === key)?.name ?? key;

  const dirty = useMemo(() => {
    const a = JSON.stringify(Object.entries(draft).sort());
    const b = JSON.stringify(Object.entries(reserve?.troops ?? {}).sort());
    return a !== b;
  }, [draft, reserve]);

  const add = () => {
    const n = Number(pickCount);
    if (!pickKey || !Number.isFinite(n) || n <= 0) return;
    setDraft((d) => ({ ...d, [pickKey]: Math.floor(n) }));
  };

  const remove = (k: string) =>
    setDraft((d) => {
      const next = { ...d };
      delete next[k];
      return next;
    });

  const save = () => setter.mutate({ villageId, troops: draft });

  return (
    <Card>
      <CardContent>
        <Stack direction="row" alignItems="center" spacing={1} mb={1}>
          <Shield size={16} />
          <Typography variant="subtitle1">Village troop reserve</Typography>
          <Typography variant="caption" color="text.secondary">
            · kept home at all times · raids can only dispatch what's above
            these minimums
          </Typography>
        </Stack>

        <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" mb={1}>
          <Autocomplete<TroopInfo>
            size="small"
            sx={{ minWidth: 220 }}
            options={catalog?.troops ?? []}
            value={(catalog?.troops ?? []).find((t) => t.key === pickKey) ?? null}
            onChange={(_e, v) => setPickKey(v?.key ?? "")}
            getOptionLabel={(t) => t.name}
            isOptionEqualToValue={(a, b) => a.key === b.key}
            renderInput={(params) => <TextField {...params} label="Troop" />}
          />
          <TextField
            size="small"
            label="Keep home"
            type="number"
            inputProps={{ min: 1 }}
            value={pickCount}
            sx={{ width: 120 }}
            onChange={(e) => setPickCount(e.target.value)}
          />
          <Button
            size="small"
            variant="contained"
            startIcon={<Plus size={14} />}
            disabled={!pickKey || Number(pickCount) <= 0}
            onClick={add}
          >
            Set
          </Button>
          <Box flex={1} />
          <Button
            size="small"
            variant="contained"
            color="success"
            startIcon={<Save size={14} />}
            disabled={!dirty || setter.isPending}
            onClick={save}
          >
            Save
          </Button>
        </Stack>

        <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
          {Object.entries(draft).length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              — no reserve — every troop is fair game for raiding
            </Typography>
          ) : (
            Object.entries(draft)
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([k, n]) => (
                <Chip
                  key={k}
                  size="small"
                  label={`keep ${n} × ${nameFor(k)}`}
                  onDelete={() => remove(k)}
                  deleteIcon={<Trash2 size={12} />}
                />
              ))
          )}
        </Stack>
      </CardContent>
    </Card>
  );
}
