import { useEffect, useMemo, useState } from "react";
import {
  Box,
  Button,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControlLabel,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";
import { MaterialReactTable, type MRT_ColumnDef, useMaterialReactTable } from "material-react-table";
import { Play, Pause, Plus, Settings2 } from "lucide-react";
import { useNavigate } from "react-router-dom";
import {
  useAccounts,
  useControllerToggles,
  useCreateAccount,
  useFeatures,
  usePatchAccount,
  useSetControllerToggles,
  useSetFeatures,
  useStartAccount,
  useStartAllAccounts,
  useStopAccount,
  useStopAllAccounts,
  useWorkerStatus,
} from "../api/hooks";
import type { Account } from "../api/types";
import { useActiveAccount } from "../components/ActiveAccountContext";

export default function Accounts() {
  const navigate = useNavigate();
  const { setActiveAccountId } = useActiveAccount();
  const { data = [], isPending } = useAccounts();
  const { data: status } = useWorkerStatus();
  const start = useStartAccount();
  const stop = useStopAccount();
  const startAll = useStartAllAccounts();
  const stopAll = useStopAllAccounts();
  const create = useCreateAccount();

  const [open, setOpen] = useState(false);
  const [togglesAccountId, setTogglesAccountId] = useState<number | null>(null);
  const [form, setForm] = useState({
    label: "",
    server_url: "",
    username: "",
    password: "",
    active_hours: "09:00-13:00,14:00-22:00,23:00-08:00",
  });

  const columns = useMemo<MRT_ColumnDef<Account>[]>(
    () => [
      { accessorKey: "label", header: "Label" },
      { accessorKey: "server_code", header: "Server" },
      {
        accessorKey: "status",
        header: "DB status",
        Cell: ({ cell }) => <Chip size="small" label={String(cell.getValue())} />,
      },
      {
        header: "Worker",
        id: "worker",
        Cell: ({ row }) => {
          const w = status?.workers[String(row.original.id)];
          return (
            <Chip
              size="small"
              color={w?.running ? "success" : "default"}
              label={w?.running ? "running" : "stopped"}
            />
          );
        },
      },
      { accessorKey: "active_hours", header: "Active hours" },
      {
        header: "Actions",
        id: "actions",
        enableSorting: false,
        // Stop row-click propagation on the whole cell so the buttons
        // don't double-dispatch as "open villages".
        muiTableBodyCellProps: { onClick: (e) => e.stopPropagation() },
        Cell: ({ row }) => {
          const w = status?.workers[String(row.original.id)];
          return (
            <Stack direction="row" spacing={1}>
              {w?.running ? (
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<Pause size={14} />}
                  onClick={() => stop.mutate(row.original.id)}
                >
                  Stop
                </Button>
              ) : (
                <Button
                  size="small"
                  variant="contained"
                  startIcon={<Play size={14} />}
                  onClick={() => start.mutate(row.original.id)}
                >
                  Start
                </Button>
              )}
              <Button
                size="small"
                variant="text"
                startIcon={<Settings2 size={14} />}
                onClick={() => setTogglesAccountId(row.original.id)}
              >
                Settings
              </Button>
            </Stack>
          );
        },
      },
    ],
    [status, start, stop],
  );

  const table = useMaterialReactTable({
    columns,
    data,
    state: { isLoading: isPending },
    enableDensityToggle: false,
    initialState: { density: "compact" },
    // Row click → switch the active account and jump to its villages.
    muiTableBodyRowProps: ({ row }) => ({
      onClick: () => {
        setActiveAccountId(row.original.id);
        navigate("/villages");
      },
      sx: { cursor: "pointer" },
    }),
  });

  return (
    <Stack spacing={2}>
      <Stack direction="row" alignItems="center" spacing={1}>
        <Typography variant="h5">Accounts</Typography>
        <Box flex={1} />
        <Button
          size="small"
          variant="outlined"
          startIcon={<Play size={14} />}
          onClick={() => startAll.mutate()}
          disabled={data.length === 0}
        >
          Start all
        </Button>
        <Button
          size="small"
          variant="outlined"
          color="warning"
          startIcon={<Pause size={14} />}
          onClick={() => stopAll.mutate()}
          disabled={data.length === 0}
        >
          Stop all
        </Button>
        <Button variant="contained" startIcon={<Plus size={16} />} onClick={() => setOpen(true)}>
          Add account
        </Button>
      </Stack>
      <MaterialReactTable table={table} />

      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Add account</DialogTitle>
        <DialogContent>
          <Stack spacing={2} mt={1}>
            <TextField
              label="Label"
              helperText="Short identifier e.g. 'main-rof'"
              value={form.label}
              onChange={(e) => setForm({ ...form, label: e.target.value })}
            />
            <TextField
              label="Server URL"
              placeholder="https://rof.x3.international.travian.com"
              value={form.server_url}
              onChange={(e) => setForm({ ...form, server_url: e.target.value })}
            />
            <TextField
              label="Username"
              value={form.username}
              onChange={(e) => setForm({ ...form, username: e.target.value })}
            />
            <TextField
              label="Password"
              type="password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
            />
            <TextField
              label="Active hours"
              helperText="HH:MM-HH:MM, comma-separate multiple windows. Wrap past midnight OK."
              value={form.active_hours}
              onChange={(e) => setForm({ ...form, active_hours: e.target.value })}
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            disabled={!form.label || !form.server_url || !form.username || !form.password}
            onClick={async () => {
              await create.mutateAsync(form);
              setOpen(false);
              setForm({ ...form, label: "", username: "", password: "" });
            }}
          >
            Create
          </Button>
        </DialogActions>
      </Dialog>

      <ControllersDialog
        accountId={togglesAccountId}
        onClose={() => setTogglesAccountId(null)}
      />
    </Stack>
  );
}

function ControllersDialog({
  accountId,
  onClose,
}: {
  accountId: number | null;
  onClose: () => void;
}) {
  const { data: accounts = [] } = useAccounts();
  const account = accounts.find((a) => a.id === accountId) ?? null;
  const { data } = useControllerToggles(accountId ?? undefined);
  const { data: features } = useFeatures(accountId ?? undefined);
  const setter = useSetControllerToggles();
  const featureSetter = useSetFeatures();
  const patchAccount = usePatchAccount();
  const open = accountId != null;

  const [hoursDraft, setHoursDraft] = useState("");
  const [hoursError, setHoursError] = useState<string | null>(null);
  const currentHours = account?.active_hours ?? "";
  // Reset draft when the selected account changes or the dialog reopens.
  useEffect(() => {
    setHoursDraft(currentHours);
    setHoursError(null);
  }, [accountId, currentHours]);

  const saveHours = async () => {
    if (accountId == null) return;
    const trimmed = hoursDraft.trim();
    if (trimmed === currentHours) return;
    try {
      await patchAccount.mutateAsync({ id: accountId, patch: { active_hours: trimmed } });
      setHoursError(null);
    } catch (e: unknown) {
      setHoursError(e instanceof Error ? e.message : String(e));
    }
  };

  const handle = (name: string, value: boolean) => {
    if (accountId == null || !data) return;
    setter.mutate({ accountId, enabled: { ...data.enabled, [name]: value } });
  };

  const setAllControllers = (value: boolean) => {
    if (accountId == null || !data) return;
    const next: Record<string, boolean> = {};
    for (const name of Object.keys(data.enabled)) next[name] = value;
    setter.mutate({ accountId, enabled: next });
  };

  const allOn = !!data && Object.values(data.enabled).every(Boolean);
  const allOff = !!data && Object.values(data.enabled).every((v) => !v);

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Account settings</DialogTitle>
      <DialogContent>
        <Typography variant="caption" color="text.secondary">
          Active hours
        </Typography>
        <Stack direction="row" spacing={1} mt={1} mb={2} alignItems="flex-start">
          <TextField
            size="small"
            fullWidth
            value={hoursDraft}
            error={!!hoursError}
            helperText={
              hoursError ??
              "HH:MM-HH:MM, comma-separate multiple windows. Wrap past midnight OK."
            }
            onChange={(e) => setHoursDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") saveHours();
            }}
          />
          <Button
            size="small"
            variant="contained"
            disabled={
              hoursDraft.trim() === currentHours || patchAccount.isPending || accountId == null
            }
            onClick={saveHours}
          >
            Save
          </Button>
        </Stack>
        <Divider sx={{ my: 1 }} />
        <Stack direction="row" alignItems="center">
          <Typography variant="caption" color="text.secondary">
            Controllers
          </Typography>
          <Box flex={1} />
          <Button size="small" onClick={() => setAllControllers(true)} disabled={!data || allOn}>
            All on
          </Button>
          <Button size="small" onClick={() => setAllControllers(false)} disabled={!data || allOff}>
            All off
          </Button>
        </Stack>
        <Stack spacing={0.5} mt={1}>
          {data &&
            Object.entries(data.enabled).map(([name, on]) => (
              <FormControlLabel
                key={name}
                control={
                  <Switch
                    checked={on}
                    onChange={(_e, v) => handle(name, v)}
                  />
                }
                label={name}
              />
            ))}
          {!data && <Typography color="text.secondary">loading...</Typography>}
        </Stack>
        <Divider sx={{ my: 2 }} />
        <Typography variant="caption" color="text.secondary">
          Features
        </Typography>
        <Stack mt={1}>
          <FormControlLabel
            control={
              <Switch
                checked={features?.watch_video_bonuses ?? true}
                disabled={!features || accountId == null}
                onChange={(_e, v) =>
                  accountId != null &&
                  featureSetter.mutate({ accountId, patch: { watch_video_bonuses: v } })
                }
              />
            }
            label={
              <Stack>
                <Typography variant="body2">Watch video bonuses</Typography>
                <Typography variant="caption" color="text.secondary">
                  Click "Watch video" for +25% faster upgrades and adventure bonuses
                </Typography>
              </Stack>
            }
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
      </DialogActions>
    </Dialog>
  );
}
