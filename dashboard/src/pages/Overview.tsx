import { Box, Card, CardContent, Chip, Grid2 as Grid, LinearProgress, Stack, Typography } from "@mui/material";
import { Activity, AlertTriangle, CheckCircle2 } from "lucide-react";
import { useAccounts, useWorkerStatus } from "../api/hooks";
import type { ControllerSnapshot } from "../api/types";

function ControllerPill({ c }: { c: ControllerSnapshot }) {
  const ok = c.errors === 0;
  return (
    <Chip
      size="small"
      icon={ok ? <CheckCircle2 size={14} /> : <AlertTriangle size={14} />}
      color={ok ? "default" : "warning"}
      label={`${c.name}: ${c.last_message || "idle"}`}
      sx={{ mr: 0.5, mb: 0.5 }}
    />
  );
}

export default function Overview() {
  const accounts = useAccounts();
  const status = useWorkerStatus();

  const rows = accounts.data ?? [];

  return (
    <Stack spacing={2}>
      <Typography variant="h5">Overview</Typography>
      {(accounts.isPending || status.isPending) && <LinearProgress />}
      <Grid container spacing={2}>
        {rows.map((a) => {
          const w = status.data?.workers[String(a.id)];
          return (
            <Grid key={a.id} size={{ xs: 12, md: 6, lg: 4 }}>
              <Card>
                <CardContent>
                  <Stack direction="row" alignItems="center" spacing={1} mb={1}>
                    <Activity size={16} />
                    <Typography variant="h6">{a.label}</Typography>
                    <Box flex={1} />
                    <Chip
                      size="small"
                      label={w?.running ? "running" : a.status}
                      color={w?.running ? "success" : "default"}
                    />
                  </Stack>
                  <Typography variant="caption" color="text.secondary">
                    {a.server_code}
                  </Typography>
                  <Box mt={1}>
                    {(w?.controllers ?? []).map((c) => (
                      <ControllerPill key={c.name} c={c} />
                    ))}
                  </Box>
                </CardContent>
              </Card>
            </Grid>
          );
        })}
        {rows.length === 0 && !accounts.isPending && (
          <Typography color="text.secondary" sx={{ p: 3 }}>
            No accounts yet. Add one in the Accounts page.
          </Typography>
        )}
      </Grid>
    </Stack>
  );
}
