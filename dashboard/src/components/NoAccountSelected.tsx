import { Alert, Stack } from "@mui/material";
import { useActiveAccount } from "./ActiveAccountContext";

export default function NoAccountSelected() {
  const { accounts, isLoading } = useActiveAccount();
  if (isLoading) return null;
  const msg =
    accounts.length === 0
      ? "No accounts yet — add one in the Accounts page to get started."
      : "Select an account from the top bar to view its data.";
  return (
    <Stack>
      <Alert severity="info">{msg}</Alert>
    </Stack>
  );
}
