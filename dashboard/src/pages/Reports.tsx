import { useMemo, useState } from "react";
import {
  Autocomplete,
  Chip,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { MaterialReactTable, type MRT_ColumnDef, useMaterialReactTable } from "material-react-table";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { useActiveAccount } from "../components/ActiveAccountContext";
import NoAccountSelected from "../components/NoAccountSelected";

interface ReportRow {
  id: number;
  account_id: number;
  tile_id: number | null;
  type: string;
  when: string | null;
  target_x: number | null;
  target_y: number | null;
  bounty_total: number;
  bounty: { wood: number; clay: number; iron: number; crop: number };
  capacity_used_pct: number | null;
}

const TYPE_COLOR: Record<string, "default" | "success" | "warning" | "error" | "info"> = {
  raid_win: "success",
  raid_loss: "warning",
  raid_empty: "default",
  defense: "error",
  scout: "info",
  other: "default",
};

type OutcomeOption = { value: string; label: string };
const OUTCOME_OPTIONS: OutcomeOption[] = [
  { value: "", label: "All" },
  { value: "raid_win", label: "Win" },
  { value: "raid_loss", label: "Loss" },
  { value: "raid_empty", label: "Empty" },
  { value: "defense", label: "Defense" },
  { value: "scout", label: "Scout" },
];

export default function Reports() {
  const { activeAccountId, activeAccount } = useActiveAccount();
  const [type, setType] = useState("");
  const [params, setParams] = useSearchParams();
  const tileId = params.get("tile_id");

  const { data = [], isPending } = useQuery({
    queryKey: ["reports", activeAccountId, type, tileId],
    enabled: activeAccountId != null,
    queryFn: () => {
      const q = new URLSearchParams();
      if (activeAccountId != null) q.set("account_id", String(activeAccountId));
      if (tileId) q.set("tile_id", tileId);
      if (type) q.set("type", type);
      return api.get<ReportRow[]>(`/reports?${q.toString()}`);
    },
  });

  const columns = useMemo<MRT_ColumnDef<ReportRow>[]>(
    () => [
      {
        accessorKey: "type",
        header: "Outcome",
        size: 120,
        Cell: ({ cell }) => (
          <Chip
            size="small"
            label={String(cell.getValue()).replace(/_/g, " ")}
            color={TYPE_COLOR[cell.getValue<string>()] ?? "default"}
          />
        ),
      },
      { header: "Target", accessorFn: (r) => (r.target_x != null ? `(${r.target_x}|${r.target_y})` : "?") },
      { accessorKey: "bounty_total", header: "Bounty" },
      {
        header: "W / C / I / Cr",
        id: "rcs",
        accessorFn: (r) =>
          `${r.bounty.wood} / ${r.bounty.clay} / ${r.bounty.iron} / ${r.bounty.crop}`,
      },
      { accessorKey: "capacity_used_pct", header: "Cap %" },
      { accessorKey: "when", header: "When" },
    ],
    [],
  );

  const table = useMaterialReactTable({
    columns,
    data,
    state: { isLoading: isPending },
    initialState: { density: "compact" },
  });

  if (activeAccountId == null) {
    return (
      <Stack spacing={2}>
        <Typography variant="h5">Reports</Typography>
        <NoAccountSelected />
      </Stack>
    );
  }

  return (
    <Stack spacing={2}>
      <Stack direction="row" alignItems="center" spacing={1}>
        <Typography variant="h5">Reports</Typography>
        {activeAccount && (
          <Chip size="small" label={`account: ${activeAccount.label}`} />
        )}
        {tileId && (
          <Chip
            size="small"
            label={`tile #${tileId}`}
            onDelete={() => {
              const next = new URLSearchParams(params);
              next.delete("tile_id");
              setParams(next, { replace: true });
            }}
          />
        )}
      </Stack>
      <Stack direction="row" spacing={2}>
        <Autocomplete<OutcomeOption, false, true, false>
          size="small"
          sx={{ minWidth: 200 }}
          options={OUTCOME_OPTIONS}
          value={OUTCOME_OPTIONS.find((o) => o.value === type) ?? OUTCOME_OPTIONS[0]}
          onChange={(_e, v) => setType(v.value)}
          getOptionLabel={(o) => o.label}
          isOptionEqualToValue={(a, b) => a.value === b.value}
          disableClearable
          renderInput={(params) => <TextField {...params} label="Outcome" />}
        />
      </Stack>
      <MaterialReactTable table={table} />
    </Stack>
  );
}
