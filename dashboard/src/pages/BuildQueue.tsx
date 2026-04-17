import { useEffect, useMemo, useState } from "react";
import {
  Autocomplete,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { MaterialReactTable, type MRT_ColumnDef, useMaterialReactTable } from "material-react-table";
import { ArrowUp, ArrowDown, Plus, Trash2 } from "lucide-react";
import {
  useBuildingCatalog,
  useBuildOrders,
  useCreateBuildOrder,
  useDeleteBuildOrder,
  useReorderBuildOrders,
  useVillages,
} from "../api/hooks";
import type { BuildOrder, BuildingCatalogEntry, Village } from "../api/types";
import { useActiveAccount } from "../components/ActiveAccountContext";
import NoAccountSelected from "../components/NoAccountSelected";

const STATUS_COLOR: Record<BuildOrder["status"], "default" | "primary" | "success" | "warning" | "error"> = {
  queued: "default",
  blocked: "warning",
  in_progress: "primary",
  done: "success",
  failed: "error",
  cancelled: "default",
};

type BuildingOption = { key: string; def: BuildingCatalogEntry };

export default function BuildQueue() {
  const { activeAccountId, activeAccount } = useActiveAccount();
  const { data: villages = [] } = useVillages(activeAccountId ?? undefined);
  const [village, setVillage] = useState<Village | null>(null);
  const vid = village?.id;

  // Preselect the first village (and re-select if the active account changes
  // and the current pick no longer belongs to it).
  useEffect(() => {
    if (villages.length === 0) {
      if (village != null) setVillage(null);
      return;
    }
    if (village == null || !villages.some((v) => v.id === village.id)) {
      setVillage(villages[0]);
    }
  }, [villages, village]);

  const { data: orders = [], isPending } = useBuildOrders(vid);
  const { data: catalog } = useBuildingCatalog();
  const reorder = useReorderBuildOrders();
  const del = useDeleteBuildOrder();
  const create = useCreateBuildOrder();

  const buildingOptions = useMemo<BuildingOption[]>(
    () => Object.entries(catalog ?? {}).map(([key, def]) => ({ key, def })),
    [catalog],
  );

  const [open, setOpen] = useState(false);
  const [building, setBuilding] = useState<BuildingOption | null>(null);
  const [draft, setDraft] = useState({
    target_level: 1,
    slot: "" as number | "",
    priority: 100,
  });

  const move = (id: number, direction: -1 | 1) => {
    if (vid == null) return;
    const ids = orders.map((o) => o.id);
    const idx = ids.indexOf(id);
    const j = idx + direction;
    if (idx < 0 || j < 0 || j >= ids.length) return;
    [ids[idx], ids[j]] = [ids[j], ids[idx]];
    reorder.mutate({ village_id: vid, ordered_ids: ids });
  };

  const columns = useMemo<MRT_ColumnDef<BuildOrder>[]>(
    () => [
      {
        header: "#",
        id: "order",
        size: 60,
        Cell: ({ row }) => (
          <Stack direction="row" spacing={0.5}>
            <Button size="small" sx={{ minWidth: 28 }} onClick={() => move(row.original.id, -1)}>
              <ArrowUp size={14} />
            </Button>
            <Button size="small" sx={{ minWidth: 28 }} onClick={() => move(row.original.id, 1)}>
              <ArrowDown size={14} />
            </Button>
          </Stack>
        ),
      },
      { accessorKey: "building_key", header: "Building" },
      { accessorKey: "target_level", header: "Target lvl" },
      { accessorKey: "slot", header: "Slot" },
      { accessorKey: "priority", header: "Priority" },
      {
        accessorKey: "status",
        header: "Status",
        Cell: ({ cell }) => (
          <Chip
            size="small"
            label={String(cell.getValue())}
            color={STATUS_COLOR[cell.getValue<BuildOrder["status"]>()]}
          />
        ),
      },
      { accessorKey: "blocked_reason", header: "Blocked reason" },
      {
        header: "",
        id: "del",
        Cell: ({ row }) => (
          <Button color="error" size="small" onClick={() => del.mutate(row.original.id)}>
            <Trash2 size={14} />
          </Button>
        ),
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [orders, vid],
  );

  const table = useMaterialReactTable({
    columns,
    data: orders,
    state: { isLoading: isPending },
    initialState: { density: "compact" },
  });

  if (activeAccountId == null) {
    return (
      <Stack spacing={2}>
        <Typography variant="h5">Build queue</Typography>
        <NoAccountSelected />
      </Stack>
    );
  }

  return (
    <Stack spacing={2}>
      <Stack direction="row" alignItems="center" spacing={1}>
        <Typography variant="h5">Build queue</Typography>
        {activeAccount && (
          <Chip size="small" label={`account: ${activeAccount.label}`} />
        )}
      </Stack>
      <Stack direction="row" spacing={2} alignItems="center">
        <Autocomplete<Village>
          size="small"
          sx={{ minWidth: 320 }}
          options={villages}
          value={village}
          onChange={(_e, v) => setVillage(v)}
          getOptionLabel={(v) => `${v.name} (${v.x}|${v.y})`}
          isOptionEqualToValue={(a, b) => a.id === b.id}
          renderInput={(params) => <TextField {...params} label="Village" />}
        />
        <Button
          variant="contained"
          startIcon={<Plus size={16} />}
          disabled={vid == null}
          onClick={() => setOpen(true)}
        >
          Add order
        </Button>
      </Stack>
      <MaterialReactTable table={table} />

      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Add build order</DialogTitle>
        <DialogContent>
          <Stack spacing={2} mt={1}>
            <Autocomplete<BuildingOption>
              size="small"
              options={buildingOptions}
              value={building}
              onChange={(_e, v) => setBuilding(v)}
              getOptionLabel={(o) => `${o.def.name} (${o.def.category})`}
              isOptionEqualToValue={(a, b) => a.key === b.key}
              renderInput={(params) => <TextField {...params} label="Building" />}
            />
            <TextField
              label="Target level"
              type="number"
              value={draft.target_level}
              onChange={(e) => setDraft({ ...draft, target_level: Number(e.target.value) })}
            />
            <TextField
              label="Slot (optional)"
              type="number"
              value={draft.slot}
              onChange={(e) =>
                setDraft({ ...draft, slot: e.target.value === "" ? "" : Number(e.target.value) })
              }
            />
            <TextField
              label="Priority"
              type="number"
              value={draft.priority}
              onChange={(e) => setDraft({ ...draft, priority: Number(e.target.value) })}
              helperText="Lower = earlier"
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            disabled={!building || vid == null}
            onClick={async () => {
              await create.mutateAsync({
                village_id: vid!,
                building_key: building!.key,
                target_level: draft.target_level,
                slot: draft.slot === "" ? null : (draft.slot as number),
                priority: draft.priority,
              });
              setOpen(false);
              setBuilding(null);
            }}
          >
            Add
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  );
}
