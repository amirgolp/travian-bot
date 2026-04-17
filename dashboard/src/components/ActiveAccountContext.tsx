import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useAccounts } from "../api/hooks";
import type { Account } from "../api/types";

const STORAGE_KEY = "travian-bot.activeAccountId";

interface ActiveAccountCtx {
  activeAccountId: number | null;
  activeAccount: Account | null;
  setActiveAccountId: (id: number | null) => void;
  accounts: Account[];
  isLoading: boolean;
}

const Ctx = createContext<ActiveAccountCtx | null>(null);

function loadPersisted(): number | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const n = Number(raw);
    return Number.isFinite(n) ? n : null;
  } catch {
    return null;
  }
}

export function ActiveAccountProvider({ children }: { children: React.ReactNode }) {
  const { data: accounts = [], isPending } = useAccounts();
  const [activeAccountId, setActiveAccountIdState] = useState<number | null>(loadPersisted);

  const setActiveAccountId = useCallback((id: number | null) => {
    setActiveAccountIdState(id);
    try {
      if (id == null) localStorage.removeItem(STORAGE_KEY);
      else localStorage.setItem(STORAGE_KEY, String(id));
    } catch {
      // localStorage unavailable — fine, state still works for the session.
    }
  }, []);

  // Reconcile the persisted id with the current account list: pick the first
  // account on first load, and drop the selection if the account was deleted.
  useEffect(() => {
    if (isPending) return;
    if (accounts.length === 0) {
      if (activeAccountId != null) setActiveAccountId(null);
      return;
    }
    const stillExists = accounts.some((a) => a.id === activeAccountId);
    if (!stillExists) setActiveAccountId(accounts[0].id);
  }, [accounts, isPending, activeAccountId, setActiveAccountId]);

  const activeAccount = useMemo(
    () => accounts.find((a) => a.id === activeAccountId) ?? null,
    [accounts, activeAccountId],
  );

  const value = useMemo<ActiveAccountCtx>(
    () => ({ activeAccountId, activeAccount, setActiveAccountId, accounts, isLoading: isPending }),
    [activeAccountId, activeAccount, setActiveAccountId, accounts, isPending],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useActiveAccount() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useActiveAccount must be used inside ActiveAccountProvider");
  return ctx;
}
