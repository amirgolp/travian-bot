import { useEffect, useMemo } from "react";
import {
  Autocomplete,
  Box,
  Button,
  Chip,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import { MaterialReactTable, type MRT_ColumnDef, useMaterialReactTable } from "material-react-table";
import { Power, PowerOff } from "lucide-react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useFarmlists, useToggleFarmlist, useVillages } from "../api/hooks";
import type { Farmlist, Village } from "../api/types";
import { useActiveAccount } from "../components/ActiveAccountContext";
import NoAccountSelected from "../components/NoAccountSelected";

const KIND_COLOR: Record<Farmlist["kind"], "default" | "primary" | "secondary"> = {
  villages: "primary",
  oases_natars: "secondary",
  mixed: "default",
};

export default function Farmlists() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const { activeAccountId, activeAccount } = useActiveAccount();
  const { data: villages = [] } = useVillages(activeAccountId ?? undefined);
  const villageIdRaw = params.get("village_id");
  const urlVillageId = villageIdRaw ? Number(villageIdRaw) : null;
  // Only honour ?village_id= if it belongs to the active account — otherwise
  // we'd load farmlists from another account through URL memory.
  const villageBelongs = urlVillageId != null && villages.some((v) => v.id === urlVillageId);
  const villageId = villageBelongs ? (urlVillageId as number) : "";

  // Default to the first village of the active account. Distance only makes
  // sense relative to a specific source village.
  useEffect(() => {
    if (villageId === "" && villages.length > 0) {
      setParams({ village_id: String(villages[0].id) }, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [villages, villageBelongs]);

  const { data: lists = [], isPending } = useFarmlists(
    typeof villageId === "number" ? villageId : undefined,
  );
  const toggle = useToggleFarmlist();

  const columns = useMemo<MRT_ColumnDef<Farmlist>[]>(
    () => [
      { accessorKey: "name", header: "Name" },
      {
        accessorKey: "kind",
        header: "Kind",
        Cell: ({ cell }) => (
          <Chip
            size="small"
            label={String(cell.getValue())}
            color={KIND_COLOR[cell.getValue<Farmlist["kind"]>()]}
          />
        ),
      },
      {
        accessorKey: "interval_seconds",
        header: "Interval (1x)",
        Cell: ({ cell }) => `${Math.round(cell.getValue<number>() / 60)} min`,
      },
      {
        accessorKey: "enabled",
        header: "On",
        enableSorting: false,
        // Prevent the row-click (navigate to detail) from firing when the user
        // flips the switch.
        muiTableBodyCellProps: { onClick: (e) => e.stopPropagation() },
        Cell: ({ row }) => (
          <Switch
            size="small"
            checked={row.original.enabled}
            onChange={(_e, v) =>
              toggle.mutate({ farmlistId: row.original.id, enabled: v })
            }
          />
        ),
      },
    ],
    [toggle],
  );

  const table = useMaterialReactTable({
    columns,
    data: lists,
    state: { isLoading: isPending },
    initialState: { density: "compact" },
    muiTableBodyRowProps: ({ row }) => ({
      onClick: () => navigate(`/farmlists/${row.original.id}`),
      sx: { cursor: "pointer" },
    }),
  });

  const anyOn = lists.some((l) => l.enabled);
  const anyOff = lists.some((l) => !l.enabled);
  const setAll = (value: boolean) => {
    for (const l of lists) {
      if (l.enabled !== value) toggle.mutate({ farmlistId: l.id, enabled: value });
    }
  };

  if (activeAccountId == null) {
    return (
      <Stack spacing={2}>
        <Typography variant="h5">Farmlists</Typography>
        <NoAccountSelected />
      </Stack>
    );
  }

  return (
    <Stack spacing={2}>
      <Stack direction="row" alignItems="center" spacing={1}>
        <Typography variant="h5">Farmlists</Typography>
        {activeAccount && (
          <Chip size="small" label={`account: ${activeAccount.label}`} />
        )}
        <Box flex={1} />
        <Button
          size="small"
          variant="outlined"
          startIcon={<Power size={14} />}
          disabled={!anyOff}
          onClick={() => setAll(true)}
        >
          Enable all
        </Button>
        <Button
          size="small"
          variant="outlined"
          color="warning"
          startIcon={<PowerOff size={14} />}
          disabled={!anyOn}
          onClick={() => setAll(false)}
        >
          Disable all
        </Button>
      </Stack>
      <Autocomplete<Village>
        size="small"
        sx={{ maxWidth: 360 }}
        options={villages}
        value={villages.find((v) => v.id === villageId) ?? null}
        onChange={(_e, v) =>
          setParams(v == null ? {} : { village_id: String(v.id) }, { replace: true })
        }
        getOptionLabel={(v) => `${v.name} (${v.x}|${v.y})`}
        isOptionEqualToValue={(a, b) => a.id === b.id}
        renderInput={(params) => <TextField {...params} label="Source village" />}
      />
      <MaterialReactTable table={table} />
    </Stack>
  );
}
