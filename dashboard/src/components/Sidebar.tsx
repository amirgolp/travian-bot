import { Box, List, ListItemButton, ListItemIcon, ListItemText } from "@mui/material";
import { NavLink, useLocation } from "react-router-dom";
import {
  LayoutDashboard,
  Users,
  Home,
  Sword,
  Hammer,
  Shield,
  Map as MapIcon,
  FileText,
} from "lucide-react";

const ITEMS = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/accounts", label: "Accounts", icon: Users },
  { to: "/villages", label: "Villages", icon: Home },
  { to: "/farmlists", label: "Farmlists", icon: Sword },
  { to: "/build", label: "Build Queue", icon: Hammer },
  { to: "/hero", label: "Hero", icon: Shield },
  { to: "/map", label: "Map Tiles", icon: MapIcon },
  { to: "/reports", label: "Reports", icon: FileText },
];

export default function Sidebar() {
  const loc = useLocation();
  return (
    <Box
      sx={{
        width: 220,
        bgcolor: "background.paper",
        borderRight: 1,
        borderColor: "divider",
        py: 1,
      }}
    >
      <List>
        {ITEMS.map(({ to, label, icon: Icon, end }) => {
          const active = end
            ? loc.pathname === to
            : loc.pathname === to || loc.pathname.startsWith(to + "/");
          return (
            <ListItemButton
              key={to}
              component={NavLink}
              to={to}
              end={end}
              selected={active}
              sx={{ borderRadius: 1, mx: 1, my: 0.5 }}
            >
              <ListItemIcon sx={{ minWidth: 36 }}>
                <Icon size={18} />
              </ListItemIcon>
              <ListItemText primary={label} />
            </ListItemButton>
          );
        })}
      </List>
    </Box>
  );
}
