import { Box, AppBar, Toolbar, Typography, Autocomplete, TextField, Chip } from "@mui/material";
import { Outlet } from "react-router-dom";
import Sidebar from "./Sidebar";
import { useActiveAccount } from "./ActiveAccountContext";
import type { Account } from "../api/types";

export default function Layout() {
  const { accounts, activeAccount, setActiveAccountId, isLoading } = useActiveAccount();
  return (
    <Box sx={{ display: "flex", minHeight: "100vh" }}>
      <Sidebar />
      <Box sx={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
        <AppBar position="static" color="transparent" elevation={0}>
          <Toolbar>
            <Typography variant="h6" sx={{ flexGrow: 1 }}>
              travian-bot
            </Typography>
            {accounts.length === 0 && !isLoading ? (
              <Chip size="small" label="no accounts — add one" color="warning" />
            ) : (
              <Autocomplete<Account>
                size="small"
                sx={{ minWidth: 260 }}
                options={accounts}
                value={activeAccount}
                onChange={(_e, v) => setActiveAccountId(v?.id ?? null)}
                getOptionLabel={(a) => `${a.label} · ${a.server_code}`}
                isOptionEqualToValue={(a, b) => a.id === b.id}
                renderInput={(params) => <TextField {...params} label="Active account" />}
              />
            )}
          </Toolbar>
        </AppBar>
        <Box sx={{ p: 3, flex: 1, minWidth: 0 }}>
          <Outlet />
        </Box>
      </Box>
    </Box>
  );
}
