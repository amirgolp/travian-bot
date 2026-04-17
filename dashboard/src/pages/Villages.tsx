import { useMemo } from "react";
import { Chip, Stack, Typography } from "@mui/material";
import { MaterialReactTable, type MRT_ColumnDef, useMaterialReactTable } from "material-react-table";
import { useNavigate } from "react-router-dom";
import { useVillages } from "../api/hooks";
import type { Village } from "../api/types";
import { useActiveAccount } from "../components/ActiveAccountContext";
import NoAccountSelected from "../components/NoAccountSelected";

export default function Villages() {
  const navigate = useNavigate();
  const { activeAccountId, activeAccount } = useActiveAccount();
  const { data: villages = [], isPending } = useVillages(activeAccountId ?? undefined);

  const columns = useMemo<MRT_ColumnDef<Village>[]>(
    () => [
      { accessorKey: "name", header: "Name" },
      {
        header: "Coords",
        id: "xy",
        accessorFn: (v) => `(${v.x}|${v.y})`,
      },
      {
        header: "Capital",
        id: "cap",
        Cell: ({ row }) =>
          row.original.is_capital ? <Chip size="small" label="capital" color="warning" /> : null,
      },
    ],
    [],
  );

  const table = useMaterialReactTable({
    columns,
    data: villages,
    state: { isLoading: isPending },
    initialState: { density: "compact" },
    muiTableBodyRowProps: ({ row }) => ({
      onClick: () => navigate(`/villages/${row.original.id}`),
      sx: { cursor: "pointer" },
    }),
  });

  if (activeAccountId == null) {
    return (
      <Stack spacing={2}>
        <Typography variant="h5">Villages</Typography>
        <NoAccountSelected />
      </Stack>
    );
  }

  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={1} alignItems="center">
        <Typography variant="h5">Villages</Typography>
        {activeAccount && (
          <Chip size="small" label={`account: ${activeAccount.label}`} />
        )}
      </Stack>
      <MaterialReactTable table={table} />
    </Stack>
  );
}
