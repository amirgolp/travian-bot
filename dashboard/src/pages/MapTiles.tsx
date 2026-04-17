import { useMemo, useState } from "react";
import {
  Alert,
  Autocomplete,
  Chip,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { MaterialReactTable, type MRT_ColumnDef, useMaterialReactTable } from "material-react-table";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useVillages } from "../api/hooks";
import type { Village } from "../api/types";
import { useActiveAccount } from "../components/ActiveAccountContext";
import NoAccountSelected from "../components/NoAccountSelected";

interface TileRow {
  id: number;
  server_code: string;
  x: number;
  y: number;
  type: string;
  name: string | null;
  player_name: string | null;
  alliance_name: string | null;
  oasis_type: string | null;
  population: number | null;
  raid_count: number;
  win_count: number;
  loss_count: number;
  empty_count: number;
  total_bounty: number;
  last_raid_at: string | null;
}

const TYPE_COLOR: Record<string, "default" | "primary" | "secondary" | "warning" | "success"> = {
  village: "primary",
  oasis: "secondary",
  natar: "warning",
  own: "success",
  unknown: "default",
};

type TypeOption = { value: string; label: string };
const TYPE_OPTIONS: TypeOption[] = [
  { value: "", label: "All" },
  { value: "village", label: "Village" },
  { value: "oasis", label: "Oasis" },
  { value: "natar", label: "Natar" },
  { value: "own", label: "Own" },
  { value: "unknown", label: "Unknown" },
];

type TileRowWithDistance = TileRow & { distance: number | null };

export default function MapTiles() {
  const { activeAccountId, activeAccount } = useActiveAccount();
  const [type, setType] = useState("");
  const [originVillage, setOriginVillage] = useState<Village | null>(null);
  const { data = [], isPending } = useQuery({
    queryKey: ["map", "tiles", type],
    queryFn: () =>
      api.get<TileRow[]>(`/map/tiles?limit=1000${type ? `&type=${type}` : ""}`),
  });
  const { data: villages = [] } = useVillages(activeAccountId ?? undefined);

  // Bake the distance onto each row so MRT re-renders when the origin changes.
  // Computing it via `accessorFn` would close over `originVillage`, but MRT
  // memoizes columns internally and doesn't always pick up closure changes —
  // precomputing as a real row field sidesteps that entirely and also lets the
  // column sort/filter as a plain number.
  const rows = useMemo<TileRowWithDistance[]>(
    () =>
      data.map((r) => {
        if (!originVillage) return { ...r, distance: null };
        const dx = r.x - originVillage.x;
        const dy = r.y - originVillage.y;
        return { ...r, distance: Math.sqrt(dx * dx + dy * dy) };
      }),
    [data, originVillage],
  );

  const columns = useMemo<MRT_ColumnDef<TileRowWithDistance>[]>(
    () => [
      {
        accessorKey: "type",
        header: "Type",
        size: 100,
        Cell: ({ cell }) => (
          <Chip
            size="small"
            label={String(cell.getValue())}
            color={TYPE_COLOR[cell.getValue<string>()] ?? "default"}
          />
        ),
      },
      { header: "Coords", id: "xy", accessorFn: (r) => `(${r.x}|${r.y})` },
      {
        // Euclidean distance in tiles from the selected origin village to this
        // tile. Travian's travel time uses Euclidean distance (√(dx²+dy²)), so
        // this matches the in-game arrival clock.
        accessorKey: "distance",
        header: "Distance",
        size: 110,
        Cell: ({ cell }) => {
          const v = cell.getValue<number | null>();
          return v == null ? "—" : v.toFixed(1);
        },
        sortUndefined: "last",
      },
      { accessorKey: "name", header: "Name" },
      { accessorKey: "player_name", header: "Player" },
      { accessorKey: "alliance_name", header: "Alliance" },
      { accessorKey: "population", header: "Pop" },
      { accessorKey: "raid_count", header: "Raids" },
      { accessorKey: "win_count", header: "Wins" },
      { accessorKey: "empty_count", header: "Empty" },
      { accessorKey: "total_bounty", header: "Total bounty" },
      { accessorKey: "last_raid_at", header: "Last raid" },
    ],
    [],
  );

  const table = useMaterialReactTable({
    columns,
    data: rows,
    state: { isLoading: isPending },
    initialState: {
      density: "compact",
      sorting: [{ id: "raid_count", desc: true }],
      pagination: { pageSize: 25, pageIndex: 0 },
    },
    enableColumnFilters: true,
    enablePagination: true,
    enableBottomToolbar: true,
    paginationDisplayMode: "pages",
    muiPaginationProps: {
      rowsPerPageOptions: [10, 25, 50, 100, 250],
      showFirstButton: true,
      showLastButton: true,
      shape: "rounded",
    },
    muiTableContainerProps: { sx: { maxWidth: "100%", overflowX: "auto" } },
    muiTablePaperProps: { sx: { maxWidth: "100%" } },
  });

  const emptyHint =
    !isPending && data.length === 0 && type === "oasis"
      ? "No oasis tiles yet. MapScanController sweeps the map around each village (~25 tiles Chebyshev) and runs once per ~24 h — on a fresh account the first sweep can take several minutes per village."
      : !isPending && data.length === 0 && type === "natar"
      ? "No Natar tiles yet. Populated by the same map scan that discovers oases."
      : null;

  if (activeAccountId == null) {
    return (
      <Stack spacing={2}>
        <Typography variant="h5">Map tiles</Typography>
        <NoAccountSelected />
      </Stack>
    );
  }

  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={1} alignItems="center">
        <Typography variant="h5">Map tiles</Typography>
        {activeAccount && (
          <Chip size="small" label={`account: ${activeAccount.label}`} />
        )}
      </Stack>
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
        <Autocomplete<TypeOption, false, true, false>
          size="small"
          sx={{ width: 180 }}
          options={TYPE_OPTIONS}
          value={TYPE_OPTIONS.find((o) => o.value === type) ?? TYPE_OPTIONS[0]}
          onChange={(_e, v) => setType(v.value)}
          getOptionLabel={(o) => o.label}
          isOptionEqualToValue={(a, b) => a.value === b.value}
          disableClearable
          renderInput={(params) => <TextField {...params} label="Type" />}
        />
        <Autocomplete<Village>
          size="small"
          sx={{ width: 280 }}
          options={villages}
          value={originVillage}
          onChange={(_e, v) => setOriginVillage(v)}
          getOptionLabel={(v) => `${v.name} (${v.x}|${v.y})`}
          isOptionEqualToValue={(a, b) => a.id === b.id}
          renderInput={(params) => (
            <TextField {...params} label="Distance from village" />
          )}
        />
        <Typography variant="caption" color="text.secondary">
          {isPending ? "loading…" : `${data.length} tile${data.length === 1 ? "" : "s"}`}
        </Typography>
      </Stack>
      {emptyHint && <Alert severity="info">{emptyHint}</Alert>}
      <MaterialReactTable table={table} />
    </Stack>
  );
}
