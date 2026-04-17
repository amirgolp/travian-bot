import { createTheme } from "@mui/material/styles";

export const theme = createTheme({
  palette: {
    mode: "dark",
    primary: { main: "#8ab4f8" },
    secondary: { main: "#f28b82" },
    background: { default: "#0f1115", paper: "#151821" },
  },
  shape: { borderRadius: 8 },
  typography: { fontFamily: "Inter, system-ui, sans-serif" },
});
