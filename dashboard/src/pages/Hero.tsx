import {
  Card,
  CardContent,
  Chip,
  LinearProgress,
  Stack,
  Tooltip,
  Typography,
  Grid2 as Grid,
} from "@mui/material";
import { Heart, Zap, Compass, Swords, Shield, Coins, MapPin, Package, Backpack } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAccounts } from "../api/hooks";
import { useActiveAccount } from "../components/ActiveAccountContext";
import NoAccountSelected from "../components/NoAccountSelected";

interface EquipmentSlot {
  slot: string;
  empty: boolean;
  rarity: string | null;
  // Level 1..5 (replaces the old `quality` string column).
  level: number | null;
  item_type_id: number | null;
  instance_id: number | null;
  // Resolved from app/data/hero_items.yaml — falls back to "Item #<id>".
  name: string | null;
  description: string | null;
}

interface BagItem {
  item_type_id: number | null;
  count: number;
  name: string | null;
  description: string | null;
}

interface HeroRow {
  id: number;
  account_id: number;
  health_pct: number | null;
  experience: number | null;
  speed_fph: number | null;
  production_per_hour: number | null;
  fighting_strength: number | null;
  off_bonus_pct: number | null;
  def_bonus_pct: number | null;
  attribute_points: number;
  home_village_id: number | null;
  status: string | null;
  adventures_available: number;
  equipment: EquipmentSlot[];
  bag_count: number;
  bag_items: BagItem[];
  observed_at: string | null;
}

function Stat({
  icon: Icon,
  label,
  value,
  unit,
}: {
  icon: typeof Heart;
  label: string;
  value: string | number | null | undefined;
  unit?: string;
}) {
  return (
    <Stack direction="row" spacing={1} alignItems="center" sx={{ minWidth: 140 }}>
      <Icon size={16} />
      <Stack>
        <Typography variant="caption" color="text.secondary">
          {label}
        </Typography>
        <Typography variant="body2">
          {value ?? "—"}
          {value != null && unit ? ` ${unit}` : ""}
        </Typography>
      </Stack>
    </Stack>
  );
}

function HeroCard({ hero, label }: { hero: HeroRow; label: string }) {
  const hp = hero.health_pct ?? 0;
  return (
    <Card>
      <CardContent>
        <Stack direction="row" alignItems="center" spacing={1} mb={1}>
          <Typography variant="h6">{label}</Typography>
          <Chip
            size="small"
            label={hero.status ?? "unknown"}
            color={hero.status === "home" ? "success" : "default"}
          />
          {hero.attribute_points > 0 && (
            <Chip size="small" color="warning" label={`+${hero.attribute_points} unspent`} />
          )}
        </Stack>
        <Stack direction="row" spacing={1} alignItems="center" mb={2}>
          <Heart size={14} />
          <LinearProgress
            variant="determinate"
            value={Math.max(0, Math.min(100, hp))}
            sx={{ flex: 1, height: 8, borderRadius: 4 }}
            color={hp > 50 ? "success" : hp > 20 ? "warning" : "error"}
          />
          <Typography variant="caption">{hp}%</Typography>
        </Stack>
        <Grid container spacing={2}>
          <Grid size={{ xs: 6, md: 4 }}>
            <Stat icon={Zap} label="XP" value={hero.experience} />
          </Grid>
          <Grid size={{ xs: 6, md: 4 }}>
            <Stat icon={Compass} label="Speed" value={hero.speed_fph} unit="fields/h" />
          </Grid>
          <Grid size={{ xs: 6, md: 4 }}>
            <Stat icon={Coins} label="Production" value={hero.production_per_hour} unit="/h" />
          </Grid>
          <Grid size={{ xs: 6, md: 4 }}>
            <Stat icon={Swords} label="Strength" value={hero.fighting_strength} />
          </Grid>
          <Grid size={{ xs: 6, md: 4 }}>
            <Stat icon={Swords} label="Off bonus" value={hero.off_bonus_pct} unit="%" />
          </Grid>
          <Grid size={{ xs: 6, md: 4 }}>
            <Stat icon={Shield} label="Def bonus" value={hero.def_bonus_pct} unit="%" />
          </Grid>
          <Grid size={{ xs: 6, md: 4 }}>
            <Stat icon={Compass} label="Adventures" value={hero.adventures_available} />
          </Grid>
          <Grid size={{ xs: 6, md: 4 }}>
            <Stat icon={MapPin} label="Home did" value={hero.home_village_id} />
          </Grid>
          <Grid size={{ xs: 6, md: 4 }}>
            <Stat icon={Backpack} label="Bag items" value={hero.bag_count} />
          </Grid>
        </Grid>
        {hero.equipment && hero.equipment.length > 0 && (
          <Stack mt={2} spacing={0.5}>
            <Stack direction="row" alignItems="center" spacing={1}>
              <Package size={14} />
              <Typography variant="caption" color="text.secondary">
                Equipment
              </Typography>
            </Stack>
            <Stack direction="row" spacing={0.5} flexWrap="wrap">
              {hero.equipment.map((e) => {
                const chip = (
                  <Chip
                    key={e.slot}
                    size="small"
                    variant={e.empty ? "outlined" : "filled"}
                    color={
                      e.empty
                        ? "default"
                        : e.rarity === "unique"
                        ? "warning"
                        : e.rarity === "epic"
                        ? "secondary"
                        : "primary"
                    }
                    label={
                      e.empty
                        ? `${e.slot}: —`
                        : [
                            e.name ?? e.slot,
                            e.level != null ? `lvl ${e.level}` : null,
                            e.rarity,
                          ]
                            .filter(Boolean)
                            .join(" · ")
                    }
                    sx={{ mr: 0.5, mb: 0.5 }}
                  />
                );
                return e.description ? (
                  <Tooltip key={e.slot} title={e.description} arrow>
                    {chip}
                  </Tooltip>
                ) : (
                  chip
                );
              })}
            </Stack>
          </Stack>
        )}
        {hero.bag_items && hero.bag_items.length > 0 && (
          <Stack mt={2} spacing={0.5}>
            <Stack direction="row" alignItems="center" spacing={1}>
              <Backpack size={14} />
              <Typography variant="caption" color="text.secondary">
                Bag ({hero.bag_count})
              </Typography>
            </Stack>
            <Stack direction="row" spacing={0.5} flexWrap="wrap">
              {hero.bag_items.map((b, i) => {
                const label = `${b.count} × ${b.name ?? `Item #${b.item_type_id ?? "?"}`}`;
                const chip = (
                  <Chip
                    key={i}
                    size="small"
                    variant="outlined"
                    label={label}
                    sx={{ mr: 0.5, mb: 0.5 }}
                  />
                );
                return b.description ? (
                  <Tooltip key={i} title={b.description} arrow>
                    {chip}
                  </Tooltip>
                ) : (
                  chip
                );
              })}
            </Stack>
          </Stack>
        )}
        {hero.observed_at && (
          <Typography variant="caption" color="text.secondary" mt={2} component="div">
            observed {new Date(hero.observed_at).toLocaleString()}
          </Typography>
        )}
      </CardContent>
    </Card>
  );
}

export default function HeroPage() {
  const { activeAccountId, activeAccount } = useActiveAccount();
  const { data: accounts = [] } = useAccounts();
  const { data: allHeroes = [], isPending } = useQuery({
    queryKey: ["hero"],
    queryFn: () => api.get<HeroRow[]>("/hero"),
    refetchInterval: 30_000,
  });

  const heroes = allHeroes.filter((h) => h.account_id === activeAccountId);
  const label = (aid: number) => accounts.find((a) => a.id === aid)?.label ?? `#${aid}`;

  if (activeAccountId == null) {
    return (
      <Stack spacing={2}>
        <Typography variant="h5">Hero</Typography>
        <NoAccountSelected />
      </Stack>
    );
  }

  return (
    <Stack spacing={2}>
      <Stack direction="row" spacing={1} alignItems="center">
        <Typography variant="h5">Hero</Typography>
        {activeAccount && (
          <Chip size="small" label={`account: ${activeAccount.label}`} />
        )}
      </Stack>
      {isPending && <LinearProgress />}
      {heroes.length === 0 && !isPending && (
        <Typography color="text.secondary">
          No hero data yet. HeroController scrapes /hero/attributes every ~15 min;
          it will appear once a session runs.
        </Typography>
      )}
      <Grid container spacing={2}>
        {heroes.map((h) => (
          <Grid key={h.id} size={{ xs: 12, md: 6 }}>
            <HeroCard hero={h} label={label(h.account_id)} />
          </Grid>
        ))}
      </Grid>
    </Stack>
  );
}
